"""Send one message signed over the final request body."""

from eazy_sdk import Identity
from examples.mail.http._client import mail_client
from examples.mail.http.send import (
    MAIL_BEARER,
    MailApi,
    NewMessage,
    signing_key,
)


# region docs: signing-run
# examples/docs/signing.py
def main() -> None:
    identity = Identity(
        auth=(MAIL_BEARER.static("session-1"),),
        key_provider=signing_key,
    )
    with mail_client() as client:
        result = MailApi(client, identity=identity).send(
            body=NewMessage(
                recipient="grace@mail.example",
                subject="Подписанный запрос",
                body="Встречаемся в пятницу.",
            )
        )
    print(f"signed message: {result.message_id}")
# endregion docs: signing-run


if __name__ == "__main__":
    main()
