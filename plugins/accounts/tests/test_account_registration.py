from __future__ import annotations

import asyncio
import string
from typing import Annotated

import pytest
from eazy_sdk_accounts import (
    AccountCreated,
    AccountDraft,
    AccountIdentifier,
    MemoryRegistrationStore,
    PendingRegistration,
    VerificationAccepted,
    VerificationChallenge,
    account_registration,
)
from eazy_sdk_accounts.registration import StoredAccount
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, SecretStr


class Credentials(BaseModel):
    email: Annotated[str, AccountIdentifier()]
    password: SecretStr


class Profile(BaseModel):
    display_name: str


class Details(BaseModel):
    invite_code: SecretStr


class Codec:
    def encode(self, value: Credentials) -> object:
        return (value.email, len(value.password.get_secret_value()))

    def decode(self, value: object) -> Credentials:
        if not isinstance(value, tuple) or len(value) != 2:
            raise TypeError("invalid encoded credentials")
        email, password_length = value
        return Credentials(
            email=str(email),
            password=SecretStr("x" * int(password_length)),
        )


class Service:
    async def create(
        self,
        _draft: AccountDraft[Credentials, Profile, Details],
        _context: None,
    ) -> AccountCreated[None]:
        return AccountCreated(
            remote_id="remote-account",
            verification=VerificationChallenge("email-1", "email_code"),
        )

    async def verify(
        self,
        _account: StoredAccount[Profile, None],
        _challenge: VerificationChallenge,
        proof: object,
        _context: None,
    ) -> VerificationAccepted[None]:
        if proof != "123456":
            raise ValueError("invalid verification code")
        return VerificationAccepted()


def draft(
    password: str, invite_code: str = "invite-secret"
) -> AccountDraft[
    Credentials,
    Profile,
    Details,
]:
    return AccountDraft(
        credentials=Credentials(
            email="ada@example.test",
            password=SecretStr(password),
        ),
        profile=Profile(display_name="Ada"),
        details=Details(invite_code=SecretStr(invite_code)),
    )


@given(
    secret=st.text(
        alphabet=string.ascii_letters + string.digits,
        min_size=8,
        max_size=40,
    )
)
def test_registration_draft_repr_never_contains_generated_secrets(secret: str) -> None:
    representation = repr(draft(secret, secret))

    assert representation == "<redacted>"
    if secret != "redacted":
        assert secret not in representation


@given(
    proof=st.text(max_size=32).filter(lambda value: value != "123456"),
)
def test_any_rejected_proof_preserves_the_pending_account(proof: str) -> None:
    async def scenario() -> None:
        store: MemoryRegistrationStore[Credentials, Profile, Details, None] = (
            MemoryRegistrationStore(Codec())
        )
        flow = account_registration(
            Credentials,
            service=Service(),
            store=store,
            context_factory=lambda: None,
        )
        pending = await flow.create(draft("correct-horse"))
        assert isinstance(pending, PendingRegistration)
        before = await store.load_account("ada@example.test", None)

        with pytest.raises(ValueError, match="invalid verification code"):
            await flow.verify(pending, proof)

        after = await store.load_account("ada@example.test", None)
        assert after == before
        assert store.commits == [
            "registration.started",
            "registration.remote_created",
            "verification.requested",
        ]

    asyncio.run(scenario())
