"""Run one declared browser operation through all composer outcomes."""

from __future__ import annotations

import asyncio

from examples.mail.browser.send import send_three_messages


async def main() -> None:
    # docs:integration-browser-outcomes:start
    # examples/docs/integration_browser_outcomes.py
    sent, rejected, silent = await send_three_messages()
    outcomes = type(sent).__name__, type(rejected).__name__, type(silent).__name__
    # docs:integration-browser-outcomes:end
    print(f"outcomes: {', '.join(outcomes)}")


if __name__ == "__main__":
    asyncio.run(main())
