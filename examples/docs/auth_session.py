"""Reuse an existing session without login or refresh."""

import asyncio

from examples.docs.auth_lifecycle import AuthSite, mail_sdk, saved_session


# region docs: auth-session-run
# examples/docs/auth_session.py
async def main() -> None:
    site = AuthSite()
    async with mail_sdk(site, session=saved_session()) as sdk:
        account = await sdk.account.get_account()

    print(f"account: {account.id} {account.email}")
    print(f"requests: {len(site.calls)}")
# endregion docs: auth-session-run


if __name__ == "__main__":
    asyncio.run(main())
