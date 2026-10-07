"""Run the documented field and whole-body encryption profile."""

from examples.mail.http.encryption import send_encrypted_message


# region docs: encryption-run
# examples/docs/encryption.py
def main() -> None:
    message_id, clear_subject = send_encrypted_message()
    print(f"message: {message_id}")
    print(f"decrypted subject: {clear_subject}")
# endregion docs: encryption-run


if __name__ == "__main__":
    main()
