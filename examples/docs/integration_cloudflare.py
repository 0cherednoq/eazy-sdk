"""Construct a Cloudflare Challenge Pages protection preset."""

from __future__ import annotations

from eazy_sdk.protection import host
from eazy_sdk_presets import cloudflare


def main() -> None:
    # docs:integration-cloudflare:start
    # examples/docs/integration_cloudflare.py
    challenge = cloudflare.challenge_pages(scope=host("api.mail.example"))
    # docs:integration-cloudflare:end
    print(f"protection: {challenge.id}")


if __name__ == "__main__":
    main()
