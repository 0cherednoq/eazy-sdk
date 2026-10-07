"""Attach a session cookie through a declared scheme."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Identity, SyncApi, op
from eazy_sdk.auth import CookieScheme
from examples.docs.request_site import request_client


class AuthResult(BaseModel):
    credential: str


# region docs: auth-cookie
# examples/docs/auth_cookie.py
MAIL_SESSION = CookieScheme("mail_session", name="mail-session")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetInbox(HttpOperation[AuthResult]):
    __http__ = Http.get("/auth/cookie", security=MAIL_SESSION)


class InboxApi(SyncApi):
    get_inbox = op(GetInbox)


def main() -> None:
    identity = Identity(auth=(MAIL_SESSION.static("session-1"),))
    with request_client() as client:
        result = InboxApi(client, identity=identity).get_inbox()
    print(f"session: {result.credential}")
# endregion docs: auth-cookie


if __name__ == "__main__":
    main()
