"""Refresh and replay after a protected endpoint returns 401."""

import asyncio

from examples.docs.auth_lifecycle import AuthSite, mail_sdk, saved_session


# region docs: auth-refresh-run
# examples/docs/auth_refresh.py
async def main() -> None:
    site = AuthSite()
    async with mail_sdk(site, session=saved_session(access="access-1")) as sdk:
        account = await sdk.account.get_account()

    print(f"account: {account.id}")
    for call in site.calls:
        print(call)
# endregion docs: auth-refresh-run


if __name__ == "__main__":
    asyncio.run(main())
