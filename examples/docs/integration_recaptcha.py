"""Construct Google reCAPTCHA protection presets."""

from __future__ import annotations

from eazy_sdk.protection import operation
from eazy_sdk_presets import recaptcha


def main() -> None:
    # docs:integration-recaptcha:start
    # examples/docs/integration_recaptcha.py
    checkbox = recaptcha.v2_checkbox(scope=operation("login"))
    action = recaptcha.v3_action(
        scope=operation("createAccount"),
        site_key="public-site-key",
        action="create_account",
    )
    # docs:integration-recaptcha:end
    print(f"protections: {checkbox.id}, {action.id}")


if __name__ == "__main__":
    main()
