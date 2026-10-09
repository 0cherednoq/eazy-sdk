"""Phase 58.4: a session on a cookie site is saved with its cookies and needs them to be valid.

The scenario is a webmail login in three steps. The entrance page marks the device with a
cookie, the login sets the session cookie, and a page hands out the token every API call
carries. No step passes a cookie to the next one by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Self

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Identity, api_group, op
from eazy_sdk.auth import AuthContext, Placed, session_scheme
from eazy_sdk.auth.session import MemorySessionStore, SessionKey, SessionRevision, StoredSession
from eazy_sdk.cookies import Cookies, CookieState, StoredCookie
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import Form

BASE = "https://mail.example"
KEY = SessionKey("session:client")


@dataclass(slots=True)
class MailSite:
    """Marks a new device, signs in with a cookie, and serves the API to a signed-in caller."""

    sets_session: bool = True
    logins: int = 0
    devices: int = 0
    seen: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        cookies = dict(
            pair.split("=", 1) for pair in request.headers.get("cookie", "").split("; ") if pair
        )
        path = request.url.path
        self.seen.append((path, request.headers.get("cookie", "")))
        if path == "/page":
            if "device" in cookies:
                return httpx.Response(200, json={"ok": True})
            self.devices += 1
            return httpx.Response(
                200,
                json={"ok": True},
                headers={"set-cookie": f"device=d{self.devices}; Path=/; Max-Age=31536000"},
            )
        if path == "/login":
            self.logins += 1
            headers = {"set-cookie": f"sid=s{self.logins}; Path=/"} if self.sets_session else {}
            return httpx.Response(200, json={"ok": True}, headers=headers)
        if path == "/token":
            if "sid" not in cookies:
                return httpx.Response(401, json={"token": ""})
            return httpx.Response(200, json={"token": f"token-for-{cookies['sid']}"})
        accepted = "sid" in cookies and request.url.params.get("token", "").endswith(
            cookies.get("sid", "?")
        )
        return httpx.Response(200 if accepted else 401, json={"folders": ["Inbox"]})

    def cookie_names(self, path: str) -> list[str]:
        header = next(cookie for seen, cookie in reversed(self.seen) if seen == path)
        return sorted(pair.split("=")[0] for pair in header.split("; ") if pair)


class Ok(BaseModel):
    ok: bool


class Token(BaseModel):
    token: str


class Folders(BaseModel):
    folders: list[str]


class MailLogin(BaseModel):
    username: str


class MailSession(BaseModel):
    """The token goes into the query; the cookies are not this model's business."""

    token: Annotated[str, Placed.query("token")]


MAIL_SESSION = session_scheme(MailSession, name="mail-session")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenPage(HttpOperation[Ok]):
    __http__ = Http.get("/page")


@dataclass(frozen=True, slots=True, kw_only=True)
class Submit(HttpOperation[Ok]):
    __http__ = Http.post("/login")

    username: Form[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadToken(HttpOperation[Token]):
    __http__ = Http.get("/token")


@dataclass(frozen=True, slots=True, kw_only=True)
class ListFolders(HttpOperation[Folders]):
    __http__ = Http.get("/api/folders")


class LoginApi(AsyncApi):
    open_page = op(OpenPage)
    submit = op(Submit)
    token = op(ReadToken)


class MailApi(AsyncApi):
    security = MAIL_SESSION

    folders = op(ListFolders)


class MailLoginService:
    """Three steps, and not one cookie named or handed over."""

    async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> MailSession:
        await context.sdk.login.open_page()
        await context.sdk.login.submit(username=credentials.username)
        return MailSession(token=(await context.sdk.login.token()).token)


class MailSdk(AsyncRoot):
    cookies = Cookies(required=("sid",))

    login = api_group(LoginApi)
    mail = api_group(MailApi)

    @classmethod
    def open(
        cls,
        site: MailSite,
        store: MemorySessionStore[MailSession],
        *,
        session: MailSession | None = None,
        cookies: CookieState | None = None,
    ) -> Self:
        auth = MAIL_SESSION.configure(
            credentials=None if session is not None else MailLogin(username="ada"),
            session=session,
            service=MailLoginService(),
            store=store,
        )
        raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
        return cls.from_handler(
            handler=AsyncHttpxHandler(raw, owns_client=True),
            base_url=BASE,
            identity=Identity(auth=(auth,), cookies=cookies),
        )


def _cookie(name: str, value: str, **attributes: Any) -> StoredCookie:
    return StoredCookie(name, value, "mail.example", **attributes)


# --- a login nobody passes cookies through ----------------------------------------------------


@pytest.mark.asyncio
async def test_three_steps_sign_in_without_a_cookie_field_and_only_once() -> None:
    site, store = MailSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(site, store) as sdk:
        assert (await sdk.mail.folders()).folders == ["Inbox"]
        await sdk.mail.folders()

    assert site.logins == 1
    assert [path for path, _ in site.seen] == [
        "/page",
        "/login",
        "/token",
        "/api/folders",
        "/api/folders",
    ]
    assert site.cookie_names("/login") == ["device"]
    assert site.cookie_names("/token") == ["device", "sid"]
    assert site.cookie_names("/api/folders") == ["device", "sid"]


@pytest.mark.asyncio
async def test_the_session_is_saved_with_the_cookies_under_one_revision() -> None:
    site, store = MailSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.folders()

    stored = await store.load(KEY)
    assert stored is not None and stored.revision == SessionRevision(1)
    assert stored.value == MailSession(token="token-for-s1")
    assert stored.cookies is not None
    assert sorted(cookie.name for cookie in stored.cookies) == ["device", "sid"]
    assert [cookie.is_session() for cookie in stored.cookies if cookie.name == "sid"] == [True]


@pytest.mark.asyncio
async def test_a_new_process_continues_from_the_saved_snapshot_without_a_login() -> None:
    site, store = MailSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.folders()
    site.seen.clear()

    async with MailSdk.open(site, store) as again:
        await again.mail.folders()

    assert site.logins == 1
    assert [path for path, _ in site.seen] == ["/api/folders"]
    assert site.cookie_names("/api/folders") == ["device", "sid"]


# --- like a browser: cookies outlive the session ----------------------------------------------


@pytest.mark.asyncio
async def test_a_login_after_a_dead_session_still_carries_the_device_cookie() -> None:
    """The saved session is no longer valid, and its cookies are what tells the device apart."""

    site, store = MailSite(), MemorySessionStore[MailSession]()
    await store.save(
        KEY,
        MailSession(token=""),
        SessionRevision(4),
        cookies=CookieState((_cookie("device", "d-known"),)),
    )
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.folders()

    assert site.devices == 0
    assert dict(site.seen)["/page"] == "device=d-known"
    stored = await store.load(KEY)
    assert stored is not None and stored.revision == SessionRevision(5)


@pytest.mark.asyncio
async def test_a_second_login_keeps_the_cookies_of_the_first() -> None:
    site, store = MailSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.folders()
        # The application forgets the session; the user's cookies stay where they are.
        await store.save(KEY, MailSession(token=""), SessionRevision(2))
        site.seen.clear()
        await sdk.mail.folders()

    assert site.logins == 2 and site.devices == 1
    assert site.cookie_names("/page") == ["device", "sid"]


@pytest.mark.asyncio
async def test_a_saved_snapshot_is_poured_in_once_and_does_not_undo_newer_cookies() -> None:
    site, store = MailSite(), MemorySessionStore[MailSession]()
    await store.save(
        KEY,
        MailSession(token="token-for-s-old"),
        SessionRevision(1),
        cookies=CookieState((_cookie("sid", "s-old"),)),
    )
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.folders()
        sdk._scope.jar.store(f"{BASE}/", ["sid=s-rotated; Path=/"])
        await sdk.login.token()

    assert dict(site.seen)["/token"] == "sid=s-rotated"


# --- a session needs its cookies --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_login_that_sets_no_session_cookie_is_not_a_session() -> None:
    site, store = MailSite(sets_session=False), MemorySessionStore[MailSession]()

    class Lenient(MailLoginService):
        async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> MailSession:
            await context.sdk.login.open_page()
            await context.sdk.login.submit(username=credentials.username)
            return MailSession(token="made-up")

    auth = MAIL_SESSION.configure(
        credentials=MailLogin(username="ada"), service=Lenient(), store=store
    )
    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    sdk = MailSdk.from_handler(
        handler=AsyncHttpxHandler(raw, owns_client=True),
        base_url=BASE,
        identity=Identity(auth=(auth,)),
    )
    async with sdk:
        with pytest.raises(Exception, match="did not set a live cookie 'sid'"):
            await sdk.mail.folders()
    assert await store.load(KEY) is None
    assert "/api/folders" not in dict(site.seen)


@pytest.mark.parametrize(
    "snapshot",
    [
        CookieState(()),
        CookieState((_cookie("other", "1"),)),
        CookieState((_cookie("sid", "s9", expires_at=datetime.now(UTC) - timedelta(minutes=1)),)),
        CookieState((_cookie("sid", "s9", expires_at=datetime.now(UTC) + timedelta(seconds=5)),)),
    ],
    ids=["no-cookies", "another-cookie", "expired", "expires-within-the-margin"],
)
@pytest.mark.asyncio
async def test_a_saved_session_without_a_live_required_cookie_signs_in_again(
    snapshot: CookieState,
) -> None:
    site, store = MailSite(), MemorySessionStore[MailSession]()
    await store.save(KEY, MailSession(token="token-for-s9"), SessionRevision(1), cookies=snapshot)
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.folders()
    assert site.logins == 1


@pytest.mark.asyncio
async def test_a_ready_session_with_cookies_from_elsewhere_needs_no_login() -> None:
    """What a browser login hands over: the token and the cookies, straight into the identity."""

    site, store = MailSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(
        site,
        store,
        session=MailSession(token="token-for-s7"),
        cookies=CookieState((_cookie("sid", "s7"),)),
    ) as sdk:
        assert (await sdk.mail.folders()).folders == ["Inbox"]
    assert site.logins == 0
    assert [path for path, _ in site.seen] == ["/api/folders"]


# --- forgetting a user ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidating_the_record_removes_the_cookies_with_it() -> None:
    site, store = MailSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.folders()
    await store.invalidate(KEY)
    assert await store.load(KEY) is None

    site.seen.clear()
    async with MailSdk.open(site, store) as fresh:
        await fresh.mail.folders()
    assert site.devices == 2
    assert dict(site.seen)["/page"] == ""


# --- a token session is stored as it always was -----------------------------------------------


@pytest.mark.asyncio
async def test_a_store_written_before_this_phase_keeps_working_for_a_token_session() -> None:
    """Without ``Cookies`` the lifecycle calls ``save`` with the three arguments it always did."""

    class OldStore:
        def __init__(self) -> None:
            self.saved: list[tuple[str, object, int]] = []

        async def load(self, key: SessionKey) -> StoredSession[MailSession] | None:
            return None

        async def save(
            self, key: SessionKey, value: MailSession, revision: SessionRevision
        ) -> None:
            self.saved.append((key.value, value, revision.value))

        async def invalidate(
            self, key: SessionKey, expected: SessionRevision | None = None
        ) -> None:
            return None

    class TokenService:
        async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> MailSession:
            return MailSession(token="plain")

    class TokenApi(AsyncApi):
        security = MAIL_SESSION

        folders = op(ListFolders)

    store = OldStore()
    auth = MAIL_SESSION.configure(
        credentials=MailLogin(username="ada"),
        service=TokenService(),
        store=store,  # type: ignore[arg-type]
    )

    def always(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"folders": []})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(always), headers={}, cookies={})
    from eazy_sdk import AsyncClient

    async with AsyncClient(
        base_url=BASE, handler=AsyncHttpxHandler(raw, owns_client=True)
    ) as client:
        await TokenApi(client, identity=Identity(auth=(auth,))).folders()
    assert store.saved == [("session:client", MailSession(token="plain"), 1)]


# --- the repository-backed store --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_repository_store_keeps_the_snapshot_beside_the_session() -> None:
    from eazy_sdk_accounts.storage.session_bridge import RepositorySessionStore

    class Repository:
        def __init__(self) -> None:
            self.data: dict[str, tuple[object, int]] = {}

        async def load_session_data(self, key: str) -> tuple[object, int] | None:
            return self.data.get(key)

        async def save_session_data(self, key: str, value: object, revision: int) -> None:
            self.data[key] = (value, revision)

        async def invalidate_session_data(self, key: str, expected_revision: int | None) -> None:
            self.data.pop(key, None)

    class Codec:
        def encode(self, value: MailSession) -> object:
            return value.model_dump()

        def decode(self, value: object) -> MailSession:
            return MailSession.model_validate(value)

    repository = Repository()
    store = RepositorySessionStore(repository, Codec())
    session = MailSession(token="t")

    await store.save(KEY, session, SessionRevision(1))
    assert repository.data[KEY.value][0] == {"token": "t"}
    plain = await store.load(KEY)
    assert plain is not None and plain.value == session and plain.cookies is None

    snapshot = CookieState((_cookie("sid", "s1"),))
    await store.save(KEY, session, SessionRevision(2), cookies=snapshot)
    loaded = await store.load(KEY)
    assert loaded is not None
    assert (loaded.value, loaded.revision, loaded.cookies) == (
        session,
        SessionRevision(2),
        snapshot,
    )
