"""Acquire a session automatically before a protected request."""

import asyncio

from pydantic import SecretStr

from examples.docs.auth_lifecycle import AuthSite, LoginCredentials, mail_sdk


# region docs: auth-login-run
# examples/docs/auth_login.py
async def main() -> None:
    site = AuthSite()
    credentials = LoginCredentials(
        email="ada@mail.example",
        password=SecretStr("correct-horse"),
    )
    async with mail_sdk(site, credentials=credentials) as sdk:
        account = await sdk.account.get_account()

    print(f"account: {account.id} {account.email}")
    print(f"first request: {site.calls[0]}")
# endregion docs: auth-login-run


if __name__ == "__main__":
    asyncio.run(main())
