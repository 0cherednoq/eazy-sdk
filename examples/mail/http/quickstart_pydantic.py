"""Read one message into a Pydantic response model."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from eazy_sdk import Http, HttpOperation, Path, SyncApi, op

from ._quickstart import quickstart_client


# region docs: quickstart-pydantic
# mail_sdk/messages.py
class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

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
# endregion docs: quickstart-pydantic


if __name__ == "__main__":
    main()
