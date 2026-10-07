"""Send HTTP Basic credentials through a declared scheme."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Identity, SyncApi, op
from eazy_sdk.auth import BasicScheme
from examples.docs.request_site import request_client


class AuthResult(BaseModel):
    credential: str


# region docs: auth-basic
# examples/docs/auth_basic.py
BASIC = BasicScheme("mail-basic")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetMailbox(HttpOperation[AuthResult]):
    __http__ = Http.get("/auth/basic", security=BASIC)


class MailboxApi(SyncApi):
    get_mailbox = op(GetMailbox)


def main() -> None:
    identity = Identity(auth=(BASIC.static(("ada", "correct-horse")),))
    with request_client() as client:
        result = MailboxApi(client, identity=identity).get_mailbox()
    print(f"user: {result.credential}")
# endregion docs: auth-basic


if __name__ == "__main__":
    main()
