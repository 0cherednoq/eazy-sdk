# Sign-in and the session

Sign-in is a chain of ordinary operations. A small service calls them in order and holds
only the branches. The session is a model whose fields say where they travel.

## Reading the inventory

- **Redirect chains** show the path from the credentials request to the signed-in page.
- **Cookies** show what the site sets and where it is sent back. You do not declare any of
  it: `cookies = Cookies()` on the root makes the library carry cookies between requests and
  hosts the way a browser does.
- A value of a later request that `comes from` a cookie, a `Location` or a page body of the
  sign-in chain is session state.
- A value `typed by the user` is a credential.

## The session model

Declare only what the SDK itself must put into requests. Cookies are not in the model.

```python
from typing import Annotated

from eazy_sdk.auth import Placed, SessionScheme, session_scheme
from pydantic import BaseModel, ConfigDict


class SiteSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    email: Annotated[str, Placed.query("email", secret=False)]
    token: Annotated[str, Placed.query("token")]
    csrf: str = ""


SESSION: SessionScheme[SiteSession] = session_scheme(SiteSession, name="site-session")
```

`Placed.query(name)` / `Placed.header(name)` put the field into every request of a router
that declares `security = SESSION`. A session whose placed value is empty is not a session
and is acquired again.

When the site authorises by cookies alone, there may be nothing to place: keep a session
model with what the caller wants to know (the account address) and let the cookies travel.

## The steps

Each step is an operation without security (`security=None`), on the router of its host.

```python
from dataclasses import dataclass

from eazy_sdk import Http, HttpOperation, Inject
from eazy_sdk.dependencies import service_url
from eazy_sdk.request import Form, markers


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenLoginPage(HttpOperation[LoginPage]):
    __http__ = Http.get("/login", security=None)


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCredentials(HttpOperation[SignedIn]):
    __http__ = Http.post(
        "/auth",
        security=None,
        inject=(Inject(placement=markers.Header("Origin"), source=service_url(AccountHost)),),
        success={302: SignedIn},
        fallback=InvalidCredentials,
    )

    login: Form[str]
    password: Form[str]
    csrf: Form[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenInbox(HttpOperation[MailPage]):
    """The signed-in page; the client follows the cross-host redirects before it."""

    __http__ = Http.get("/inbox/", security=None)
```

A chain of redirects and `meta refresh` stubs between hosts is not a step: the client walks
it (`Resilience(max_redirects=…, client_redirects=True)`) and the operation receives the
final page.

## The service

```python
from typing import Any

from eazy_sdk.auth import AuthContext


@dataclass(frozen=True, slots=True)
class SiteLogin:
    """Credentials: only what the caller knows."""

    username: str
    password: str


class SiteLoginService:
    async def acquire(self, credentials: SiteLogin, context: AuthContext[Any]) -> SiteSession:
        sdk = context.sdk
        page = await sdk.login_pages.open()
        await sdk.login_auth.submit(
            login=credentials.username, password=credentials.password, csrf=page.csrf
        )
        inbox = await sdk.mail_pages.inbox()
        return SiteSession(email=credentials.username, token=inbox.token)

    async def refresh(self, session: SiteSession, context: AuthContext[Any]) -> SiteSession:
        inbox = await context.sdk.mail_pages.inbox()
        return session.model_copy(update={"token": inbox.token})
```

- `context.sdk` is the same SDK on a scoped client: the service calls the step routers
  through it and keeps no reference to the root.
- `acquire` runs lazily, before the first request that needs the session; `refresh` runs
  when a response is marked `reauth` or the status is 401.
- A branch (a second factor) is an `isinstance` on a step's result. Write the branch only if
  the recording has it.
- Translate a library error into an SDK error only where the meaning is known, with
  `raise ... from error`.

## Wiring

```python
from eazy_sdk import Identity
from eazy_sdk.auth.session import MemorySessionStore

auth = SESSION.configure(
    credentials=credentials,
    service=SiteLoginService(),
    store=MemorySessionStore[SiteSession](),
)
sdk = SiteSdk.from_handler(handler=handler, base_url=..., identity=Identity(auth=(auth,)))
```

The step routers are groups of the same root (`login_pages = api_group(LoginPagesApi)`); the
service uses them, the user of the SDK does not.

To sign in without sending an API request, and to read the cookies of the signed-in user:

```python
from eazy_sdk.auth import ensure_session
from eazy_sdk.cookies import cookies_of

session = await ensure_session(sdk, scheme=SESSION)
cookies = cookies_of(sdk)
```

`Identity(cookies=state)` seeds a user's cookies from an earlier run.

## What the replay proves

A scenario that calls a protected operation replays the whole chain: sign-in steps, redirects
and the call. A wrong-password scenario proves the refusal case. What the recording does not
contain (a second factor, a captcha, a locked account) stays unwritten and goes to the
report as not observed.
