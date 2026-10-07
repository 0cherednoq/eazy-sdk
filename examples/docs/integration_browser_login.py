"""Share one browser session between two tabs."""

from __future__ import annotations

import asyncio

from examples.mail.browser.session import one_login_for_two_tabs


async def main() -> None:
    # docs:integration-browser-login:start
    # examples/docs/integration_browser_login.py
    logins, tabs, cookie = await one_login_for_two_tabs()
    # docs:integration-browser-login:end
    print(f"browser logins: {logins}")
    print(f"tabs in context: {tabs}")
    print(f"session cookie: {cookie}")


if __name__ == "__main__":
    asyncio.run(main())
