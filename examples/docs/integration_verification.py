"""Finish a pending account registration with a proof."""

from __future__ import annotations

import asyncio

from eazy_sdk_accounts import AccountDraft, PendingRegistration
from pydantic import SecretStr

from .integration_registration import Credentials, registration_flow


async def main() -> None:
    registrations = registration_flow()
    pending = await registrations.create(
        AccountDraft(
            credentials=Credentials(
                email="ada@mail.example",
                password=SecretStr("correct-horse"),
            ),
            profile=None,
            details=None,
        )
    )
    # docs:integration-verification:start
    # examples/docs/integration_verification.py
    if not isinstance(pending, PendingRegistration):
        raise RuntimeError("registration did not request verification")
    verified = await registrations.verify(pending, "123456")
    resumed = await registrations.resume("ada@mail.example")
    # docs:integration-verification:end
    print(f"verification: {type(verified).__name__}")
    print(f"resume: {type(resumed).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
