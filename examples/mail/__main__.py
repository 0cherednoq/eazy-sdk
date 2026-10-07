"""Run the complete mail tutorial through its HTTP or browser entry point."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from examples.mail.browser.assembly import main as browser_main
from examples.mail.http.assembly import main as http_main


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the teaching mail SDK")
    parser.add_argument("transport", choices=("http", "browser"))
    selected: dict[str, Callable[[], None]] = {
        "http": http_main,
        "browser": browser_main,
    }
    arguments = parser.parse_args()
    selected[arguments.transport]()


if __name__ == "__main__":
    main()
