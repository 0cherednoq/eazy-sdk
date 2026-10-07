"""Reversible teaching ciphers shared by the mail SDK and its local site."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from eazy_sdk.crypto import CryptoContext, FrozenValue

MAIL_ENCRYPTED_CONTENT_TYPE = "application/vnd.mail.encrypted+json"


# region docs: mail-demo-ciphers
# examples/mail/crypto.py
@dataclass(frozen=True, slots=True)
class DemoFieldCipher:
    name: str = "mail-fields-v1"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        _ = context
        if not isinstance(value, str):
            raise TypeError("the teaching field cipher accepts strings")
        return "mail-field:" + base64.b64encode(value.encode()).decode()

    def decrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        _ = context
        if not isinstance(value, str) or not value.startswith("mail-field:"):
            raise ValueError("unexpected teaching field ciphertext")
        return base64.b64decode(value.removeprefix("mail-field:")).decode()


@dataclass(frozen=True, slots=True)
class DemoBodyCipher:
    name: str = "mail-body-v1"

    def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        _ = context
        return b"mail-body:" + base64.b64encode(value)

    def decrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        _ = context
        if not value.startswith(b"mail-body:"):
            raise ValueError("unexpected teaching body ciphertext")
        return base64.b64decode(value.removeprefix(b"mail-body:"))


FIELD_CIPHER = DemoFieldCipher()
BODY_CIPHER = DemoBodyCipher()
# endregion docs: mail-demo-ciphers


__all__ = [
    "BODY_CIPHER",
    "FIELD_CIPHER",
    "MAIL_ENCRYPTED_CONTENT_TYPE",
    "DemoBodyCipher",
    "DemoFieldCipher",
]
