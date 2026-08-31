from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from eazy_sdk_openapi import GenerationConfig, ProjectionImport
from eazy_sdk_openapi.cli import projection_import
from eazy_sdk_openapi.generator import generate_package, render_client
from eazy_sdk_openapi.ir import BodyProjectionIR, UnsupportedOpenAPIError, parse_openapi


def projection_specification(version: str) -> dict[str, Any]:
    return {
        "openapi": version,
        "info": {"title": "Projection fixture", "version": "1"},
        "paths": {
            "/register": {
                "post": {
                    "operationId": "registerUser",
                    "tags": ["Registration"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RegisterWire"}
                            }
                        },
                    },
                    "responses": {"204": {"description": "registered"}},
                    "x-eazy-sdk": {
                        "projection": {
                            "source": {"$ref": "#/components/schemas/RegisterPublic"},
                            "target": {"$ref": "#/components/schemas/RegisterWire"},
                            "application": "register-user",
                            "encoding": "application/json",
                            "name": "registration-v1",
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "RegisterPublic": {
                    "type": "object",
                    "required": ["login", "email", "first_name", "last_name"],
                    "properties": {
                        "login": {"type": "string"},
                        "email": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                    },
                },
                "AccountWire": {
                    "type": "object",
                    "required": ["login", "email"],
                    "properties": {
                        "login": {"type": "string"},
                        "email": {"type": "string"},
                    },
                },
                "ProfileWire": {
                    "type": "object",
                    "required": ["first_name", "last_name"],
                    "properties": {
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                    },
                },
                "ClientWire": {
                    "type": "object",
                    "required": ["platform", "version"],
                    "properties": {
                        "platform": {"type": "string"},
                        "version": {"type": "string"},
                    },
                },
                "RegisterWire": {
                    "type": "object",
                    "required": ["account", "profile", "client"],
                    "properties": {
                        "account": {"$ref": "#/components/schemas/AccountWire"},
                        "profile": {"$ref": "#/components/schemas/ProfileWire"},
                        "client": {"$ref": "#/components/schemas/ClientWire"},
                    },
                },
            }
        },
    }


def generation_config(module: str = "projection_application") -> GenerationConfig:
    return GenerationConfig(
        projections=(
            ProjectionImport("register-user", f"{module}:register_to_wire"),
        )
    )


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1", "3.2.0"])
def test_projection_extension_lowers_to_deterministic_ir_and_source(version: str) -> None:
    ir = parse_openapi(projection_specification(version))
    projection = ir.operations[0].body_projection

    assert isinstance(projection, BodyProjectionIR)
    assert projection.application == "register-user"
    assert projection.name == "registration-v1"
    assert tuple(field.name for field in projection.source_fields) == (
        "login",
        "email",
        "first_name",
        "last_name",
    )
    assert tuple(field.name for field in projection.target_fields) == (
        "account",
        "profile",
        "client",
    )

    source = render_client(ir, config=generation_config())
    assert source == render_client(ir, config=generation_config())
    assert hashlib.sha256(source.encode()).hexdigest() == (
        "cfb93832a350b91fb467d64889a226b27dd8671930e6822096ee86e5b4f334e5"
    )
    assert "class RegisterUserPublicBody(TypedDict, total=False):" in source
    assert "class _RegisterUserProjectionTarget(OpenAPIModel):" in source
    assert "class RegisterUserRequest(RegisterUserPublicBody, total=False):" in source
    assert "body=_REGISTER_USER_BODY_PROJECTION" in source
    assert "name='registration-v1'" in source
    assert (
        "from projection_application import register_to_wire as "
        "_REGISTER_USER_PROJECTION_APPLICATION"
    ) in source
    assert all(token not in source for token in ("lambda", "eval(", "import_module"))
    compile(source, "generated/client.py", "exec")


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1", "3.2.0"])
@pytest.mark.timeout(30)
async def test_generated_projection_import_types_and_executes_exact_wire_body(
    version: str, tmp_path: Path, httpserver: Any
) -> None:
    suffix = version.replace(".", "")
    application_module = f"projection_application_{suffix}"
    (tmp_path / f"{application_module}.py").write_text(
        "from collections.abc import Mapping\n"
        "\n"
        "def register_to_wire(source: Mapping[str, object]) -> dict[str, object]:\n"
        "    return {\n"
        "        'account': {'login': source['login'], 'email': source['email']},\n"
        "        'profile': {\n"
        "            'first_name': source['first_name'],\n"
        "            'last_name': source['last_name'],\n"
        "        },\n"
        "        'client': {'platform': 'web', 'version': '1.0'},\n"
        "    }\n",
        encoding="utf-8",
    )
    spec = projection_specification(version)
    spec_path = tmp_path / f"projection-{suffix}.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    package_name = f"projection_sdk_{suffix}"
    package = generate_package(
        spec,
        spec_path=spec_path,
        output_directory=tmp_path,
        package_name=package_name,
        config=generation_config(application_module),
    )

    consumer = tmp_path / f"consumer_{suffix}.py"
    consumer.write_text(
        f"from {package_name} import AsyncAPI\n"
        "\n"
        "async def consume(api: AsyncAPI) -> None:\n"
        "    await api.registration.registerUser(\n"
        "        login='john',\n"
        "        email='john@example.test',\n"
        "        first_name='John',\n"
        "        last_name='Smith',\n"
        "    )\n",
        encoding="utf-8",
    )
    typing = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package), str(consumer)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert typing.returncode == 0, typing.stdout + typing.stderr

    httpserver.expect_request(
        "/register",
        method="POST",
        json={
            "account": {"login": "john", "email": "john@example.test"},
            "profile": {"first_name": "John", "last_name": "Smith"},
            "client": {"platform": "web", "version": "1.0"},
        },
    ).respond_with_data(status=204)
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module(package_name)
        sdk = module.AsyncAPI.httpx(base_url=httpserver.url_for("/"))
        await sdk.registration.registerUser(
            login="john",
            email="john@example.test",
            first_name="John",
            last_name="Smith",
        )
        await sdk.aclose()
    finally:
        sys.path.remove(str(tmp_path))
        for name in tuple(sys.modules):
            if name in {package_name, application_module} or name.startswith(
                f"{package_name}."
            ):
                del sys.modules[name]


@pytest.mark.timeout(30)
def test_projection_consumer_rejects_missing_required_public_field(tmp_path: Path) -> None:
    spec = projection_specification("3.1.1")
    spec_path = tmp_path / "projection.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    (tmp_path / "projection_application.py").write_text(
        "def register_to_wire(source: object) -> object:\n    return source\n",
        encoding="utf-8",
    )
    package = generate_package(
        spec,
        spec_path=spec_path,
        output_directory=tmp_path,
        package_name="projection_sdk",
        config=generation_config(),
    )
    consumer = tmp_path / "invalid_consumer.py"
    consumer.write_text(
        "from projection_sdk import AsyncAPI\n\n"
        "async def consume(api: AsyncAPI) -> None:\n"
        "    await api.registration.registerUser(login='john')\n",
        encoding="utf-8",
    )

    typing = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(consumer)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert typing.returncode == 1
    assert "Missing named argument" in typing.stdout
    assert package.is_dir()


def test_standard_request_without_projection_stays_wire_shaped() -> None:
    spec = projection_specification("3.1.1")
    operation = spec["paths"]["/register"]["post"]
    operation.pop("x-eazy-sdk")

    source = render_client(parse_openapi(spec))

    assert "class RegisterUserPublicBody" not in source
    assert "BodyProjection" not in source
    assert "body: Required[Annotated[RegisterWire, JsonBody(" in source


@pytest.mark.parametrize(
    ("mutate", "pointer"),
    [
        (lambda projection: projection.pop("source"), "/source"),
        (lambda projection: projection.update({"application": ""}), "/application"),
        (lambda projection: projection.update({"encoding": "text/plain"}), "/encoding"),
        (
            lambda projection: projection.update(
                {"target": {"$ref": "#/components/schemas/RegisterPublic"}}
            ),
            "/target",
        ),
    ],
)
def test_projection_extension_rejects_invalid_contract(
    mutate: Any, pointer: str
) -> None:
    spec = projection_specification("3.1.1")
    projection = spec["paths"]["/register"]["post"]["x-eazy-sdk"]["projection"]
    mutate(projection)

    with pytest.raises(UnsupportedOpenAPIError) as captured:
        parse_openapi(spec)

    assert captured.value.pointer.endswith(pointer)


def test_projection_requirement_must_be_resolved_before_emission() -> None:
    ir = parse_openapi(projection_specification("3.1.1"))

    with pytest.raises(ValueError, match=r"missing projection imports.*register-user"):
        render_client(ir)


@pytest.mark.parametrize(
    "implementation", ["dynamic", "module:", "module:attribute.extra", "bad-module:value"]
)
def test_projection_import_is_a_static_python_reference(implementation: str) -> None:
    with pytest.raises(ValueError, match="static 'module:attribute'"):
        ProjectionImport("register-user", implementation)


def test_cli_projection_argument_builds_typed_generation_config_input() -> None:
    assert projection_import("register-user=application.projections:REGISTER") == (
        ProjectionImport("register-user", "application.projections:REGISTER")
    )
