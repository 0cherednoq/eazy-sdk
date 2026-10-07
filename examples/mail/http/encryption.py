"""Encrypt selected mail fields and then the serialized HTTP body."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import Http, HttpOperation, Identity, SyncApi, op
from eazy_sdk.crypto import (
    decrypt_encoded,
    decrypt_inbound,
    encrypt_encoded,
    encrypt_field,
    encrypt_outbound,
    http_encrypted,
    payload_crypto,
)
from eazy_sdk.request import Wire
from eazy_sdk.request.markers import JsonBody
from eazy_sdk.response import Json

from examples.mail.crypto import (
    BODY_CIPHER,
    FIELD_CIPHER,
    MAIL_ENCRYPTED_CONTENT_TYPE,
)
from examples.mail.http._client import mail_client
from examples.mail.http.send import (
    MAIL_BEARER,
    MAIL_HMAC,
    FindSent,
    NewMessage,
    RecipientRejected,
    RejectedEnvelope,
    SendEnvelope,
    SendResult,
    signing_key,
)


# region docs: mail-encryption-profile
# examples/mail/http/encryption.py
MAIL_CRYPTO = payload_crypto(
    "mail-send-v1",
    outbound=encrypt_outbound(
        encrypt_field(NewMessage, lambda message: message.subject, using=FIELD_CIPHER),
        encrypt_field(NewMessage, lambda message: message.body, using=FIELD_CIPHER),
        encoded=encrypt_encoded(
            using=BODY_CIPHER,
            max_input_bytes=16_000,
            max_output_bytes=24_000,
        ),
    ),
    inbound=decrypt_inbound(
        encoded=decrypt_encoded(
            using=BODY_CIPHER,
            max_input_bytes=24_000,
            max_output_bytes=16_000,
        )
    ),
)

MAIL_WIRE = http_encrypted(
    content_type=MAIL_ENCRYPTED_CONTENT_TYPE,
    clear_content_type="application/json",
    plaintext_statuses=frozenset({401, 403}),
)
# endregion docs: mail-encryption-profile


# region docs: encrypted-mail-operation
# examples/mail/http/encryption.py
@dataclass(frozen=True, slots=True, kw_only=True)
class SendEncryptedMessage(HttpOperation[SendResult]):
    __http__ = Http.post(
        "/api/v1/messages/send",
        success={200: Json(SendEnvelope)},
        errors={200: (Json(RejectedEnvelope), RecipientRejected)},
        security=MAIL_BEARER,
        signing=MAIL_HMAC,
        crypto=MAIL_CRYPTO,
        wire=Wire(encrypted=MAIL_WIRE),
    )

    body: Annotated[NewMessage, JsonBody()]


class EncryptedMailApi(SyncApi):
    base_url = "https://mail.example"

    send = op(SendEncryptedMessage)
    sent = op(FindSent)
# endregion docs: encrypted-mail-operation


# region docs: encrypted-mail-flow
# examples/mail/http/encryption.py
def send_encrypted_message() -> tuple[int, str]:
    identity = Identity(
        auth=(MAIL_BEARER.static("session-1"),),
        key_provider=signing_key,
    )
    subject = "Секретная встреча"
    with mail_client() as client:
        api = EncryptedMailApi(client, identity=identity)
        sent = api.send(
            body=NewMessage(
                recipient="grace@mail.example",
                subject=subject,
                body="Ключи привезут в пятницу.",
            )
        )
        found = api.sent(recipient="grace@mail.example", subject=subject)
    return sent.message_id or 0, found.items[0].subject
# endregion docs: encrypted-mail-flow


def main() -> None:
    message_id, subject = send_encrypted_message()
    print(f"encrypted message: {message_id}")
    print(f"found clear subject: {subject}")


if __name__ == "__main__":
    main()
