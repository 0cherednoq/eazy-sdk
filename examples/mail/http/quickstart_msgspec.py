"""Read one message into a msgspec Struct response model."""

from dataclasses import dataclass

import msgspec

from eazy_sdk import Http, HttpOperation, Path, SyncApi, op

from ._quickstart import quickstart_client


# region docs: quickstart-msgspec
# mail_sdk/messages.py
class Message(msgspec.Struct, frozen=True):
    id: int
    sender: str
    subject: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/{message_id}")

    message_id: Path[int]


class MailApi(SyncApi):
    base_url = "https://mail.example"
    message = op(GetMessage)


def main() -> None:
    with quickstart_client() as client:
        message = MailApi(client).message(message_id=42)
    print(f"{message.sender}: {message.subject}")
# endregion docs: quickstart-msgspec


if __name__ == "__main__":
    main()
