"""Construct Cloudflare Turnstile protection presets."""

from __future__ import annotations

from eazy_sdk.protection import host, operation
from eazy_sdk_presets import cloudflare


def main() -> None:
    # docs:integration-turnstile:start
    # examples/docs/integration_turnstile.py
    widget = cloudflare.turnstile_widget(scope=operation("login"))
    preclearance = cloudflare.turnstile_preclearance(
        scope=host("api.mail.example"),
        site_key="public-site-key",
        page_url="https://api.mail.example/login",
    )
    # docs:integration-turnstile:end
    print(f"protections: {widget.id}, {preclearance.id}")


if __name__ == "__main__":
    main()
