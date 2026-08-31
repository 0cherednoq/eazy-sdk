from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_selector_lambda_is_contextually_typed_by_mypy(tmp_path: Path) -> None:
    source = tmp_path / "selector_typing.py"
    source.write_text(
        """from pydantic import BaseModel

from eazy_sdk.crypto import CryptoContext, FrozenValue, encrypt_field


class Card(BaseModel):
    number: str


class Payment(BaseModel):
    card: Card


class Cipher:
    name = "typing-test-only"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        return value


encrypt_field(Payment, lambda body: body.card.number, using=Cipher())
encrypt_field(Payment, lambda body: body.card.nubmer, using=Cipher())
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(source)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert '"Card" has no attribute "nubmer"' in result.stdout


def test_crypto_namespace_import_is_transport_and_cipher_library_free() -> None:
    program = (
        "import sys; import eazy_sdk.crypto; "
        "blocked=('zapros', 'httpx', 'requests', 'curl_cffi', 'cryptography', 'Crypto', 'nacl'); "
        "assert not [name for name in sys.modules if name.split('.')[0] in blocked]"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", program],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
