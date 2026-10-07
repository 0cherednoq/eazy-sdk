"""Generate a small WebSocket SDK package from an AsyncAPI document."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from eazy_sdk_asyncapi import generate_package


DOCUMENT = {
    "asyncapi": "3.0.0",
    "info": {"title": "Mail events", "version": "1"},
    "servers": {
        "production": {
            "host": "stream.mail.example",
            "pathname": "/ws",
            "protocol": "wss",
        }
    },
    "channels": {
        "mail": {
            "address": "mail/{account}",
            "parameters": {"account": {"description": "Account"}},
            "messages": {"Notice": {"$ref": "#/components/messages/Notice"}},
        }
    },
    "operations": {
        "observe": {
            "action": "receive",
            "channel": {"$ref": "#/channels/mail"},
            "messages": [{"$ref": "#/channels/mail/messages/Notice"}],
        }
    },
    "components": {
        "messages": {
            "Notice": {
                "name": "notice",
                "payload": {
                    "type": "object",
                    "properties": {"subject": {"type": "string"}},
                    "required": ["subject"],
                },
            }
        }
    },
}


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        # docs:integration-asyncapi:start
        # examples/docs/integration_asyncapi.py
        package = generate_package(
            DOCUMENT,
            spec_path=root / "asyncapi.json",
            output_directory=root / "generated",
            package_name="mail_events_sdk",
        )
        files = sorted(path.name for path in package.iterdir())
        # docs:integration-asyncapi:end
    print(f"package: {package.name}")
    print(f"files: {', '.join(files)}")


if __name__ == "__main__":
    main()
