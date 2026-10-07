"""Sign a mail send request and handle sent, rejected and silent outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Identity, Query, SyncApi, op
from eazy_sdk.auth import BearerScheme
from eazy_sdk.request import (
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    header_output,
    hmac_sha256,
)
from eazy_sdk.request.markers import JsonBody
from eazy_sdk.response import ApiError, Const, Json, Payload

from examples.mail.http._client import mail_client


class NewMessage(BaseModel):
    recipient: str
    subject: str
    body: str


class SendResult(BaseModel):
    outcome: str
    message_id: int | None = None


class SendEnvelope(BaseModel):
    status: Annotated[str, Const("ok")]
    body: Payload[SendResult]


class RejectedDetails(BaseModel):
    code: Annotated[str, Const("recipient_rejected")]
    message: str


class RejectedEnvelope(BaseModel):
    status: Annotated[str, Const("recipient_rejected")]
    body: RejectedDetails


class RecipientRejected(ApiError[RejectedEnvelope]):
    pass


class SentMessage(BaseModel):
    id: int
    sender: str
    recipient: str
    subject: str


class SentMessages(BaseModel):
    items: list[SentMessage]


class SentMessagesEnvelope(BaseModel):
    status: Annotated[str, Const("ok")]
    body: Payload[SentMessages]


# region docs: http-send-signature
# examples/mail/http/send.py
MAIL_BEARER = BearerScheme("mail-bearer")
MAIL_KEY = SigningKeyRequirement("mail-send")
MAIL_HMAC = hmac_sha256(
    key=MAIL_KEY,
    base=body_digest("sha256"),
    output=header_output("X-Mail-Signature"),
)


def signing_key(requirement: SigningKeyRequirement) -> SigningKey:
    if requirement == MAIL_KEY:
        return SigningKey(b"mail-demo-secret")
    raise LookupError(requirement.name)
# endregion docs: http-send-signature


# region docs: http-send-operations
# examples/mail/http/send.py
@dataclass(frozen=True, slots=True, kw_only=True)
class SendMessage(HttpOperation[SendResult]):
    __http__ = Http.post(
        "/api/v1/messages/send",
        success={200: Json(SendEnvelope)},
        errors={200: (Json(RejectedEnvelope), RecipientRejected)},
        security=MAIL_BEARER,
        signing=MAIL_HMAC,
    )

    body: Annotated[NewMessage, JsonBody()]


@dataclass(frozen=True, slots=True, kw_only=True)
class FindSent(HttpOperation[SentMessages]):
    __http__ = Http.get(
        "/api/v1/messages/sent",
        success={200: Json(SentMessagesEnvelope)},
        security=MAIL_BEARER,
    )

    recipient: Query[str]
    subject: Query[str]


class MailApi(SyncApi):
    base_url = "https://mail.example"

    send = op(SendMessage)
    sent = op(FindSent)
# endregion docs: http-send-operations


# region docs: http-send-flow
# examples/mail/http/send.py
def send_three_messages() -> tuple[int, str, str, int]:
    identity = Identity(
        auth=(MAIL_BEARER.static("session-1"),),
        key_provider=signing_key,
    )
    with mail_client() as client:
        api = MailApi(client, identity=identity)
        sent = api.send(
            body=NewMessage(
                recipient="grace@mail.example",
                subject="Проверка доставки",
                body="Встречаемся в пятницу.",
            )
        )
        try:
            api.send(
                body=NewMessage(
                    recipient="rejected@mail.example",
                    subject="Проверка адреса",
                    body="Это письмо отклонят.",
                )
            )
        except RecipientRejected as error:
            rejected = type(error).__name__

        silent = api.send(
            body=NewMessage(
                recipient="quiet@mail.example",
                subject="Тихая доставка",
                body="Проверим папку отправленных.",
            )
        )
        found = api.sent(
            recipient="quiet@mail.example",
            subject="Тихая доставка",
        )

    return sent.message_id or 0, rejected, silent.outcome, found.items[0].id
# endregion docs: http-send-flow


def main() -> None:
    sent, rejected, silent, found = send_three_messages()
    print(f"sent: {sent}")
    print(f"rejected: {rejected}")
    print(f"silent response: {silent}")
    print(f"found in Sent: {found}")


if __name__ == "__main__":
    main()
