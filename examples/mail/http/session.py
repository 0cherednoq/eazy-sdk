"""Acquire a mail session from HTML, then refresh it by time and by rejection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated

from eazy_sdk_html import CSS
from pydantic import BaseModel, SecretStr

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Identity, JsonField, api_group, op
from eazy_sdk.auth import AuthContext, AuthScheme, Bearer, ExpiresAt, RefreshToken, session_auth
from eazy_sdk.response import ApiError, Const, Error, Html, Json, Payload

from examples.mail.http._client import async_mail_client
from examples.mail.site import MailSite


@dataclass(slots=True)
class DemoClock:
    now: datetime = field(default_factory=lambda: datetime(2030, 1, 1, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now


CLOCK = DemoClock()


# region docs: http-session-model
# examples/mail/http/session.py
class MailSession(BaseModel):
    access_token: Annotated[
        SecretStr,
        Bearer(),
        CSS('meta[name="mail-token"]::attr(content)'),
    ]
    refresh_token: Annotated[
        SecretStr,
        RefreshToken(),
        CSS('meta[name="mail-refresh"]::attr(content)'),
    ]
    expires_at: Annotated[
        datetime,
        ExpiresAt(leeway=timedelta(seconds=10)),
        CSS('meta[name="mail-expires"]::attr(content)'),
    ]
# endregion docs: http-session-model


class SessionEnvelope(BaseModel):
    status: Annotated[str, Const("ok")]
    body: Payload[MailSession]


class User(BaseModel):
    email: str
    display_name: str


class UserEnvelope(BaseModel):
    status: Annotated[str, Const("ok")]
    body: Payload[User]


class SessionProblem(BaseModel):
    code: str
    message: str


class ProblemEnvelope(BaseModel):
    status: str
    body: Payload[SessionProblem]


class SessionRejected(ApiError[ProblemEnvelope]):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadSessionFromInbox(HttpOperation[MailSession]):
    __http__ = Http.get(
        "/inbox/",
        operation_id="readSessionFromInbox",
        success={200: Html(MailSession)},
        security=None,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RefreshSession(HttpOperation[MailSession]):
    __http__ = Http.post(
        "/api/v1/session/refresh",
        operation_id="refreshSession",
        success={200: Json(SessionEnvelope)},
        security=None,
    )

    refresh_token: JsonField[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class GetCurrentUser(HttpOperation[User]):
    __http__ = Http.get(
        "/api/v1/user/short",
        operation_id="getCurrentUser",
        success={200: Json(UserEnvelope)},
        errors=(Error(401, Json(ProblemEnvelope), exception=SessionRejected),),
    )


class SessionApi(AsyncApi):
    read_from_inbox = op(ReadSessionFromInbox)
    refresh = op(RefreshSession)


class UserApi(AsyncApi):
    current = op(GetCurrentUser)


class MailSdk(AsyncRoot):
    session = api_group(SessionApi)
    user = api_group(UserApi)


def protected_mail_sdk(scheme: AuthScheme[MailSession]) -> type[MailSdk]:
    class ProtectedUserApi(UserApi):
        security = scheme

    class ProtectedMailSdk(MailSdk):
        user = api_group(ProtectedUserApi)

    return ProtectedMailSdk


# region docs: http-session-service
# examples/mail/http/session.py
@dataclass(slots=True)
class MailSessionService:
    acquired: MailSession | None = None
    refreshed: list[MailSession] = field(default_factory=list)

    async def acquire(
        self,
        credentials: str,
        context: AuthContext[MailSdk],
    ) -> MailSession:
        _ = credentials
        self.acquired = await context.sdk.session.read_from_inbox()
        return self.acquired

    async def refresh(
        self,
        session: MailSession,
        context: AuthContext[MailSdk],
    ) -> MailSession:
        renewed = await context.sdk.session.refresh(
            refresh_token=session.refresh_token.get_secret_value()
        )
        self.refreshed.append(renewed)
        return renewed
# endregion docs: http-session-service


# region docs: http-session-flow
# examples/mail/http/session.py
async def session_lifecycle() -> tuple[str, str, str, str, int]:
    CLOCK.now = datetime(2030, 1, 1, tzinfo=UTC)
    site = MailSite()
    service = MailSessionService()
    auth = session_auth(
        MailSession,
        credentials="inbox HTML",
        service=service,
        name="mail-session",
        clock=CLOCK,
    )
    sdk_type = protected_mail_sdk(auth.scheme)

    async with async_mail_client(site) as client:
        sdk = sdk_type(client, identity=Identity(auth=(auth,)))
        first = await sdk.user.current()

        CLOCK.now += timedelta(minutes=5)
        await sdk.user.current()
        by_expiry = service.refreshed[-1]

        site.state.sessions.pop(by_expiry.access_token.get_secret_value())
        await sdk.user.current()
        by_rejection = service.refreshed[-1]

    if service.acquired is None:
        raise RuntimeError("the protected call did not acquire a session")
    return (
        first.email,
        service.acquired.access_token.get_secret_value(),
        by_expiry.access_token.get_secret_value(),
        by_rejection.access_token.get_secret_value(),
        site.state.refreshes,
    )
# endregion docs: http-session-flow


async def run() -> None:
    email, html_token, by_expiry, by_rejection, refreshes = await session_lifecycle()
    print(f"HTML session: {email} via {html_token}")
    print(f"expiry refresh: {by_expiry}")
    print(f"401 refresh: {by_rejection}")
    print(f"refresh requests: {refreshes}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
