"""Create a pending account registration in a memory store."""

from __future__ import annotations

import asyncio
from typing import Annotated

from pydantic import BaseModel, SecretStr

from eazy_sdk_accounts import (
    AccountCreated,
    AccountDraft,
    AccountIdentifier,
    MemoryRegistrationStore,
    RegistrationFlow,
    VerificationAccepted,
    VerificationChallenge,
    account_registration,
)
from eazy_sdk_accounts.registration import StoredAccount


class Credentials(BaseModel):
    email: Annotated[str, AccountIdentifier()]
    password: SecretStr


class CredentialsCodec:
    def encode(self, value: Credentials) -> object:
        return {"email": value.email, "password": value.password.get_secret_value()}

    def decode(self, value: object) -> Credentials:
        return Credentials.model_validate(value)


class RegistrationService:
    async def create(self, draft: AccountDraft[Credentials, None, None], context: None) -> AccountCreated[None]:
        _ = draft, context
        return AccountCreated(
            remote_id="user-42",
            verification=VerificationChallenge("email-1", "email_code"),
        )

    async def verify(
        self,
        account: StoredAccount[None, None],
        challenge: VerificationChallenge,
        proof: object,
        context: None,
    ) -> VerificationAccepted[None]:
        _ = account, challenge, context
        if proof != "123456":
            raise ValueError("invalid verification code")
        return VerificationAccepted()


def registration_flow() -> RegistrationFlow[Credentials, None, None, None, None]:
    return account_registration(
        Credentials,
        service=RegistrationService(),
        store=MemoryRegistrationStore(CredentialsCodec()),
        context_factory=lambda: None,
        provider="mail",
    )


async def main() -> None:
    # docs:integration-registration:start
    # examples/docs/integration_registration.py
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
    # docs:integration-registration:end
    print(f"registration: {type(pending).__name__}")
    print(f"remote id: {pending.account.remote_id}")


if __name__ == "__main__":
    asyncio.run(main())
