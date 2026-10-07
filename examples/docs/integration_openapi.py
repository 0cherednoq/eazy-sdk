"""Generate a small SDK package from an OpenAPI document."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from eazy_sdk_openapi import generate_package


DOCUMENT = {
    "openapi": "3.1.0",
    "info": {"title": "Mail", "version": "1"},
    "paths": {
        "/messages/{message_id}": {
            "get": {
                "operationId": "getMessage",
                "parameters": [
                    {
                        "name": "message_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Message",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Message"}
                            }
                        },
                    }
                },
            }
        }
    },
    "components": {
        "schemas": {
            "Message": {
                "type": "object",
                "required": ["id", "subject"],
                "properties": {
                    "id": {"type": "integer"},
                    "subject": {"type": "string"},
                },
            }
        }
    },
}


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        spec = root / "openapi.json"
        spec.write_text(json.dumps(DOCUMENT), encoding="utf-8")
        # docs:integration-openapi:start
        # examples/docs/integration_openapi.py
        package = generate_package(
            DOCUMENT,
            spec_path=spec,
            output_directory=root / "generated",
            package_name="mail_sdk",
        )
        files = sorted(path.name for path in package.iterdir())
        # docs:integration-openapi:end
    print(f"package: {package.name}")
    print(f"files: {', '.join(files)}")


if __name__ == "__main__":
    main()
