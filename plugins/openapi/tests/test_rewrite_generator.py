from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from eazy_sdk_openapi.generator import (
    generate_package,
    render_client,
    render_dependencies,
)
from eazy_sdk_openapi.ir import UnsupportedOpenAPIError, parse_openapi

from eazy_sdk.handlers.httpx import AsyncHttpxHandler


def specification(version: str) -> dict[str, Any]:
    return {
        "openapi": version,
        "info": {"title": "Fixture", "version": "1"},
        "paths": {
            "/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "expand",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {"$ref": "#/components/responses/UserResponse"},
                        "404": {"$ref": "#/components/responses/ProblemResponse"},
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                },
                "Problem": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                },
            },
            "responses": {
                "UserResponse": {
                    "description": "ok",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/User"}}
                    },
                },
                "ProblemResponse": {
                    "description": "no",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                    },
                },
            },
        },
    }


def test_crypto_extension_lowers_named_profile_without_algorithm_source(tmp_path: Path) -> None:
    document = specification("3.1.1")
    operation = document["paths"]["/users/{user_id}"]["get"]
    operation["x-eazy-sdk-crypto"] = {
        "profile": "payments-v1",
        "direction": "inbound",
        "wire": {
            "contentType": "application/vnd.example.encrypted+json",
            "clearContentType": "application/json",
            "plaintextStatuses": [404],
        },
    }

    ir = parse_openapi(document)
    assert ir.operations[0].crypto is not None
    assert ir.operations[0].crypto.profile == "payments-v1"
    source = render_client(ir)
    compile(source, "generated-client.py", "exec")
    assert "def crypto_registry(profiles: Mapping[str, PayloadCrypto])" in source
    assert "operation_ids=('getUser',)" in source
    assert "CryptoDirection.INBOUND" in source
    assert "application/vnd.example.encrypted+json" in source
    assert all(token not in source for token in ("lambda", "eval(", "import_module", "key="))

    spec_path = tmp_path / "crypto-openapi.json"
    spec_path.write_text(json.dumps(document), encoding="utf-8")
    package = generate_package(
        document,
        spec_path=spec_path,
        output_directory=tmp_path,
        package_name="crypto_openapi_sdk",
    )
    typing = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package)],
        capture_output=True,
        text=True,
    )
    assert typing.returncode == 0, typing.stdout + typing.stderr
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module("crypto_openapi_sdk")
        with pytest.raises(KeyError, match="payments-v1"):
            module.crypto_registry({})
    finally:
        sys.path.remove(str(tmp_path))
        for name in tuple(sys.modules):
            if name == "crypto_openapi_sdk" or name.startswith("crypto_openapi_sdk."):
                del sys.modules[name]


@pytest.mark.parametrize(
    "crypto,pointer",
    [
        ({"profile": ""}, "/profile"),
        ({"profile": "p", "direction": "sideways"}, "/direction"),
        ({"profile": "p", "wire": {"plaintextStatuses": [700]}}, "/plaintextStatuses"),
    ],
)
def test_crypto_extension_rejects_invalid_contract(
    crypto: dict[str, object], pointer: str
) -> None:
    document = specification("3.1.1")
    document["paths"]["/users/{user_id}"]["get"]["x-eazy-sdk-crypto"] = crypto

    with pytest.raises(UnsupportedOpenAPIError) as captured:
        parse_openapi(document)

    assert captured.value.pointer.endswith(pointer)


def session_specification() -> dict[str, Any]:
    return {
        "openapi": "3.1.1",
        "info": {"title": "Session fixture", "version": "1"},
        "x-eazy-sdk": {
            "sessionAuth": {
                "scheme": "bearerAuth",
                "sessionModel": "UserSession",
                "credentialsModel": "LoginCredentials",
                "bearerField": "access_token",
                "refreshTokenField": "refresh_token",
                "expiresAtField": "expires_at",
                "expiresLeewaySeconds": 30,
                "acquire": {
                    "operation": "login",
                    "requestField": "body",
                    "requestModel": "LoginRequest",
                    "fields": {
                        "username": "credentials.username",
                        "password": "credentials.password",
                        "remember_me": {"literal": True},
                    },
                },
                "refresh": {
                    "operation": "refreshSession",
                    "requestField": "body",
                    "requestModel": "RefreshRequest",
                    "fields": {"refresh_token": "session.refresh_token"},
                },
            }
        },
        "paths": {
            "/login": {
                "post": {
                    "operationId": "login",
                    "tags": ["Auth"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/LoginRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "session",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/UserSession"}
                                }
                            },
                        }
                    },
                }
            },
            "/refresh": {
                "post": {
                    "operationId": "refreshSession",
                    "tags": ["Auth"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RefreshRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "session",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/UserSession"}
                                }
                            },
                        }
                    },
                }
            },
            "/account": {
                "get": {
                    "operationId": "getAccount",
                    "tags": ["Account"],
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "account",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Account"}
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "LoginCredentials": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string", "format": "password"},
                    },
                },
                "LoginRequest": {
                    "type": "object",
                    "required": ["username", "password", "remember_me"],
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                        "remember_me": {"type": "boolean"},
                    },
                },
                "RefreshRequest": {
                    "type": "object",
                    "required": ["refresh_token"],
                    "properties": {"refresh_token": {"type": "string"}},
                },
                "UserSession": {
                    "type": "object",
                    "required": ["access_token", "refresh_token", "expires_at"],
                    "properties": {
                        "access_token": {"type": "string", "format": "password"},
                        "refresh_token": {"type": "string", "format": "password"},
                        "expires_at": {"type": "string", "format": "date-time"},
                    },
                },
                "Account": {
                    "type": "object",
                    "required": ["authorization"],
                    "properties": {"authorization": {"type": "string"}},
                },
            },
        },
    }


@pytest.mark.parametrize("version", ["3.0.3", "3.1.1", "3.2.0"])
def test_supported_versions_normalize_to_shared_contracts(version: str) -> None:
    ir = parse_openapi(specification(version))
    assert ir.version == version
    assert ir.operations[0].operation_id == "getUser"
    assert {item.identity.pointer for item in ir.references} >= {
        "#/components/responses/UserResponse",
        "#/components/schemas/User",
    }
    source = render_client(ir)
    assert "eazy_sdk.codegen" in source
    assert "RequestDraft" not in source
    assert "lambda" not in source
    assert "attrgetter" not in source
    assert "EndpointContract" not in source
    assert "class GetUserRequest(TypedDict, total=False):" in source
    assert "**request: Unpack[GetUserRequest]" in source
    assert source.count("@api.get(") == 2


def test_operation_is_a_decorated_method_with_response_on_its_descriptor() -> None:
    source = render_client(parse_openapi(specification("3.1.1")))

    async_router = source.index("class AsyncDefault(AsyncApi):")
    async_decorator = source.index("    @api.get(", async_router)
    async_method = source.index("    async def getUser(", async_decorator)
    sync_router = source.index("class SyncDefault(SyncApi):")
    sync_decorator = source.index("    @api.get(", sync_router)
    sync_method = source.index("    def getUser(", sync_decorator)

    assert async_router < async_decorator < async_method < sync_router
    assert sync_router < sync_decorator < sync_method
    assert "EndpointContract" not in source
    assert "_with_response" not in source
    assert "class GetUserRequest(TypedDict, total=False):" in source
    assert source.count("**request: Unpack[GetUserRequest]") == 2
    assert "operation_id='getUser'" in source
    assert "frozenset" not in source
    assert "requires=()," not in source
    assert "signing=()," not in source
    assert "wire=None," not in source
    assert "idempotent=None," not in source
    assert "fallback=None," not in source
    assert "raise NotImplementedError" in source

    tagged_spec = specification("3.1.1")
    tagged_spec["paths"]["/users/{user_id}"]["get"]["tags"] = ["Users"]
    assert "tags=('Users',)," in render_client(parse_openapi(tagged_spec))


async def test_generated_session_factory_hides_runtime_plumbing_and_executes(
    tmp_path: Path, httpserver: Any
) -> None:
    spec = session_specification()
    ir = parse_openapi(spec)
    source = render_client(ir)

    assert "credentials: LoginCredentials | None = None" in source
    assert "session: UserSession | None = None" in source
    assert "SessionKey" not in source
    assert "sdk_factory" not in source
    assert "AuthPlacement" not in source

    spec_path = tmp_path / "session-openapi.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    package = generate_package(
        spec,
        spec_path=spec_path,
        output_directory=tmp_path,
        package_name="session_sdk",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module("session_sdk")
        models = importlib.import_module("session_sdk.models")
        signature = inspect.signature(module.AsyncAPI.httpx)
        assert set(signature.parameters) == {
            "base_url",
            "credentials",
            "session",
            "config",
        }
        handler_signature = inspect.signature(module.AsyncAPI.from_handler)
        assert set(handler_signature.parameters) == {
            "base_url",
            "handler",
            "credentials",
            "session",
                "config",
                "owns_handler",
                "profile",
            }

        httpserver.expect_request(
            "/login",
            method="POST",
            json={
                "username": "ada@example.test",
                "password": "correct-horse",
                "remember_me": True,
            },
        ).respond_with_json(
            {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_at": "2035-01-01T00:00:00Z",
            }
        )
        httpserver.expect_request(
            "/account",
            method="GET",
            headers={"Authorization": "Bearer access-1"},
        ).respond_with_json({"error": "expired"}, status=401)
        httpserver.expect_request(
            "/refresh",
            method="POST",
            json={"refresh_token": "refresh-1"},
        ).respond_with_json(
            {
                "access_token": "access-2",
                "refresh_token": "refresh-2",
                "expires_at": "2035-01-01T00:00:00Z",
            }
        )
        httpserver.expect_request(
            "/account",
            method="GET",
            headers={"Authorization": "Bearer access-2"},
        ).respond_with_json({"authorization": "Bearer access-2"})

        credentials = models.LoginCredentials(
            username="ada@example.test",
            password="correct-horse",
        )
        api = module.AsyncAPI.httpx(
            base_url=httpserver.url_for("/"),
            credentials=credentials,
        )
        account = await api.account.getAccount()
        await api.aclose()

        assert account.authorization == "Bearer access-2"
        saved_session = models.UserSession(
            access_token="saved-access",
            refresh_token="saved-refresh",
            expires_at="2035-01-01T00:00:00Z",
        )
        assert "saved-access" not in repr(saved_session)
        httpserver.expect_request(
            "/account",
            method="GET",
            headers={"Authorization": "Bearer saved-access"},
        ).respond_with_json({"authorization": "Bearer saved-access"})
        raw = httpx.AsyncClient(headers={}, cookies={})
        saved_api = module.AsyncAPI.from_handler(
            base_url=httpserver.url_for("/"),
            handler=AsyncHttpxHandler(raw, owns_client=True),
            session=saved_session,
        )
        saved_account = await saved_api.account.getAccount()
        await saved_api.aclose()
        assert saved_account.authorization == "Bearer saved-access"

        with pytest.raises(ValueError, match="exactly one"):
            module.AsyncAPI.httpx(base_url=httpserver.url_for("/"))
        with pytest.raises(ValueError, match="exactly one"):
            module.AsyncAPI.httpx(
                base_url=httpserver.url_for("/"),
                credentials=credentials,
                session=saved_session,
            )

        typing = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", str(package)],
            capture_output=True,
            text=True,
        )
        assert typing.returncode == 0, typing.stdout + typing.stderr
    finally:
        sys.path.remove(str(tmp_path))
        for name in tuple(sys.modules):
            if name == "session_sdk" or name.startswith("session_sdk."):
                del sys.modules[name]


def test_reference_identity_is_interned_once() -> None:
    spec = specification("3.1.1")
    spec["paths"]["/users/{user_id}"]["head"] = spec["paths"]["/users/{user_id}"]["get"]
    refs = parse_openapi(spec).references
    assert sum(item.identity.pointer == "#/components/responses/UserResponse" for item in refs) == 1


def test_canonical_extension_rejects_unknown_and_legacy_fields() -> None:
    spec = specification("3.1.1")
    spec["x-eazy-sdk"] = {"unknown": {}}
    with pytest.raises(UnsupportedOpenAPIError, match="unknown fields"):
        parse_openapi(spec)
    spec.pop("x-eazy-sdk")
    spec["x-eazy-sdk-dependency-rules"] = []
    with pytest.raises(UnsupportedOpenAPIError, match="legacy"):
        parse_openapi(spec)


def test_security_or_of_and_and_wire_options_are_lowered() -> None:
    spec = specification("3.1.1")
    spec["components"]["securitySchemes"] = {
        "bearer": {"type": "http", "scheme": "bearer"},
        "device": {"type": "apiKey", "in": "header", "name": "X-Device"},
    }
    operation = spec["paths"]["/users/{user_id}"]["get"]
    operation["security"] = [{"bearer": [], "device": []}, {"device": []}]
    operation["x-eazy-sdk"] = {
        "wire": {
            "queryOrder": ["expand"],
            "headerOrder": ["Authorization", "X-Device"],
            "exact": True,
            "protocol": "http/1.1",
        },
        "replay": {"idempotent": True},
    }
    ir = parse_openapi(spec)
    assert [item.name for item in ir.security_schemes] == ["bearer", "device"]
    assert ir.operations[0].wire is not None
    source = render_client(ir)
    assert "any_of(all_of(BEARER, DEVICE), all_of(DEVICE))" in source
    assert "query_order=('expand',)" in source
    assert "idempotent=True" in source


def test_input_is_the_only_parameter_declaration_and_normalizes_wire_names() -> None:
    spec = specification("3.1.1")
    operation = spec["paths"]["/users/{user_id}"]["get"]
    operation["parameters"] = [
        {
            "name": "user_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        },
        {"name": "user_id", "in": "query", "schema": {"type": "string"}},
        {"name": "options", "in": "query", "schema": {"type": "integer"}},
        {"name": "filter[name]", "in": "query", "schema": {"type": "string"}},
    ]
    ir = parse_openapi(spec)
    client = render_client(ir)

    assert "user_id: Required[Annotated[str, Path('user_id')]]" in client
    assert "user_id_query: Annotated[str | None, Query('user_id')]" in client
    assert "options_: Annotated[int | None, Query('options')]" in client
    assert "filter_name: Annotated[str | None, Query('filter[name]')]" in client
    assert "**request: Unpack[GetUserRequest]" in client
    assert "parameters=" not in client
    assert "body=" not in client
    assert "path_values" not in client
    assert "query_values" not in client
    assert "        /," not in client
    assert "EndpointContract" not in client
    assert client.count("**request: Unpack[GetUserRequest]") == 2


def test_query_array_requiring_repeated_keys_is_rejected_with_source_pointer() -> None:
    spec = specification("3.1.1")
    parameter = spec["paths"]["/users/{user_id}"]["get"]["parameters"][1]
    parameter.update(
        {
            "name": "tags",
            "schema": {"type": "array", "items": {"type": "string"}},
            "explode": True,
        }
    )

    with pytest.raises(UnsupportedOpenAPIError) as captured:
        parse_openapi(spec)

    assert captured.value.pointer.endswith("/parameters/1/explode")
    assert "repeated keys" in str(captured.value)


def test_inline_flat_json_body_becomes_pydantic_request_model() -> None:
    spec = specification("3.1.1")
    operation = spec["paths"]["/users/{user_id}"]["get"]
    operation["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["ticketCount"],
                    "properties": {
                        "ticketCount": {"type": "integer"},
                        "note": {"type": ["string", "null"]},
                    },
                }
            }
        },
    }
    ir = parse_openapi(spec)
    assert ir.operations[0].request_body is not None
    assert ir.operations[0].request_body.fields is not None
    client = render_client(ir)
    assert "class GetUserRequestBody(OpenAPIModel):" in client
    assert "serialize_by_alias=True" in client
    assert "ticket_count: Annotated[int, Field(alias='ticketCount')]" in client
    assert "note: str | None = None" in client
    assert "body: Required[Annotated[GetUserRequestBody, JsonBody(" in client


@pytest.mark.parametrize(
    ("media_type", "property_schema", "field", "marker"),
    [
        (
            "application/x-www-form-urlencoded",
            {"type": "string"},
            "username: str",
            "FormBody(",
        ),
        (
            "multipart/form-data",
            {"type": "string", "format": "binary"},
            "document: bytes",
            "MultipartBody(",
        ),
    ],
)
def test_flat_form_and_multipart_bodies_become_pydantic_request_models(
    media_type: str, property_schema: dict[str, Any], field: str, marker: str
) -> None:
    spec = specification("3.1.1")
    operation = spec["paths"]["/users/{user_id}"]["get"]
    operation["requestBody"] = {
        "required": True,
        "content": {
            media_type: {
                "schema": {
                    "type": "object",
                    "required": ["username" if media_type.endswith("urlencoded") else "document"],
                    "properties": {
                        "username" if media_type.endswith("urlencoded") else "document": (
                            property_schema
                        )
                    },
                }
            }
        },
    }
    source = render_client(parse_openapi(spec))
    assert "class GetUserRequestBody(OpenAPIModel):" in source
    assert field in source
    assert f"body: Required[Annotated[GetUserRequestBody, {marker}" in source


def test_optional_body_with_required_properties_keeps_root_boundary() -> None:
    spec = specification("3.1.1")
    operation = spec["paths"]["/users/{user_id}"]["get"]
    operation["requestBody"] = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                }
            }
        },
    }
    client = render_client(parse_openapi(spec))
    assert "body: Annotated[dict[str, Any] | None, JsonBody(" in client
    assert "JsonField('name')" not in client


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"nested": {"type": "object"}}},
        {"type": "object", "properties": {"name": {"type": "string", "minLength": 1}}},
        {"allOf": [{"$ref": "#/components/schemas/User"}]},
    ],
)
def test_nested_or_constrained_body_keeps_root_boundary(schema: dict[str, Any]) -> None:
    spec = specification("3.1.1")
    operation = spec["paths"]["/users/{user_id}"]["get"]
    operation["requestBody"] = {
        "required": True,
        "content": {"application/json": {"schema": schema}},
    }
    client = render_client(parse_openapi(spec))
    assert "body: Required[Annotated[" in client
    assert "JsonBody(" in client


def test_canonical_protection_flow_generates_typed_wire_injection(tmp_path: Path) -> None:
    spec = specification("3.1.1")
    spec["components"]["schemas"].update(
        {
            "Challenge": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
            "ProtectionResult": {
                "type": "object",
                "required": ["challenge", "answer"],
                "properties": {
                    "challenge": {"type": "string"},
                    "answer": {"type": "string"},
                },
            },
        }
    )
    spec["x-eazy-sdk"] = {
        "protectionFlows": {
            "login-protection": {
                "result": {"$ref": "#/components/schemas/ProtectionResult"},
                "acquire": "acquireCaptcha",
                "solve": True,
            }
        }
    }
    spec["paths"]["/captcha"] = {
        "get": {
            "operationId": "acquireCaptcha",
            "responses": {
                "200": {
                    "description": "challenge",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Challenge"}}
                    },
                }
            },
        }
    }
    spec["paths"]["/login"] = {
        "post": {
            "operationId": "login",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["email", "captchaChallenge", "captchaAnswer"],
                            "properties": {
                                "email": {"type": "string"},
                                "captchaChallenge": {"type": "string"},
                                "captchaAnswer": {"type": "string"},
                            },
                        }
                    }
                },
            },
            "responses": spec["paths"]["/users/{user_id}"]["get"]["responses"],
            "x-eazy-sdk": {
                "protections": [
                    {
                        "flow": "login-protection",
                        "outputs": {
                            "captchaChallenge": "challenge",
                            "captchaAnswer": "answer",
                        },
                    }
                ]
            },
        }
    }

    ir = parse_openapi(spec)
    login = next(item for item in ir.operations if item.operation_id == "login")
    assert login.protections[0].outputs[1].source == "answer"
    source = render_client(ir)
    assert "LOGIN_PROTECTION = SolverRequirement[Any, ProtectionResult]" in source
    assert "class LoginRequestBody(OpenAPIModel):" in source
    assert "captcha_challenge" not in source.split(
        "class _LoginProjectionTarget", 1
    )[0]
    assert "class _LoginProjectionTarget(OpenAPIModel):" in source
    assert "FromProtection(LOGIN_PROTECTION, 'challenge')" in source
    assert "class _LoginProjectionSource(TypedDict, total=False):" in source
    assert "def _project_login_body(" in source
    assert "_LOGIN_BODY_PROJECTION = BodyProjection(" in source
    assert "protections=(LOGIN_PROTECTION,)" in source
    assert "body=_LOGIN_BODY_PROJECTION" in source
    assert "wire_body=" not in source
    assert "protection_flow(LOGIN_PROTECTION" in source
    compile(source, "generated/client.py", "exec")
    spec_path = tmp_path / "protection.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    package = generate_package(
        spec,
        spec_path=spec_path,
        output_directory=tmp_path,
        package_name="protection_sdk",
    )
    typing = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package)],
        capture_output=True,
        text=True,
    )
    assert typing.returncode == 0, typing.stdout + typing.stderr


def test_canonical_extensions_lower_to_identity_based_ir_and_generated_dependencies(
    tmp_path: Path,
) -> None:
    spec = specification("3.1.1")
    spec["x-eazy-sdk"] = {
        "dependencies": {
            "device": {
                "result": {"$ref": "#/components/schemas/User"},
                "cache": "attempt",
                "secret": True,
                "bindings": [{"field": "id", "in": "header", "name": "X-Device", "required": True}],
            }
        },
        "signatureProfiles": {"vendor": {"implementation": "fixture.signing:VENDOR"}},
        "domainRules": [
            {
                "scope": {"hosts": ["api.test"]},
                "requires": ["device"],
            }
        ],
    }
    operation = spec["paths"]["/users/{user_id}"]["get"]
    operation["parameters"].append(
        {"name": "X-Device", "in": "header", "schema": {"type": "string"}}
    )
    operation["x-eazy-sdk"] = {
        "requires": [{"dependency": "device", "required": True}],
        "signatures": ["vendor"],
        "replay": {"idempotent": True},
    }

    ir = parse_openapi(spec)
    assert ir.dependencies[0].name == "device"
    assert ir.operations[0].requires[0].dependency == "device"
    assert ir.domain_rules[0].requires[0].dependency == "device"
    assert ir.signature_profiles[0].implementation == "fixture.signing:VENDOR"
    source = render_dependencies(ir) + render_client(ir)
    assert "RequestDependency.typed" in source
    assert "DependencySpec(DEVICE" in source
    assert "lambda" not in source
    assert "attrgetter" not in source

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "__init__.py").write_text("", encoding="utf-8")
    (fixture / "signing.py").write_text(
        "from eazy_sdk.request import (\n"
        "    SigningKeyRequirement, header_output, hmac_sha256, literal,\n"
        ")\n"
        "VENDOR = hmac_sha256(\n"
        "    key=SigningKeyRequirement('vendor'),\n"
        "    base=literal(b'vendor'),\n"
        "    output=header_output('X-Device'),\n"
        "    name='vendor',\n"
        ")\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "canonical.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    package = generate_package(
        spec, spec_path=spec_path, output_directory=tmp_path, package_name="canonical_sdk"
    )
    generated = {item.name for item in package.iterdir()}
    assert {"_model_base.py", "dependencies.py", "signatures.py", "py.typed"} <= generated
    assert "lambda" not in (package / "dependencies.py").read_text(encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        client_module = importlib.import_module("canonical_sdk.client")
        from eazy_sdk import ApiDefaults

        declaration = client_module.AsyncDefault.getUser.resolve(ApiDefaults())
        assert declaration.requires[0].dependency is client_module.DEVICE
        assert declaration.signing[0].identity.name == "vendor"
    finally:
        sys.path.remove(str(tmp_path))
        for name in tuple(sys.modules):
            if name == "canonical_sdk" or name.startswith("canonical_sdk."):
                del sys.modules[name]


@pytest.mark.parametrize(
    "legacy",
    ["signals", "reactions", "beforeCall", "wireProfiles", "signingRules", "parsers"],
)
def test_legacy_protection_extension_names_are_rejected(legacy: str) -> None:
    spec = specification("3.1.1")
    spec["x-eazy-sdk"] = {legacy: {}}
    with pytest.raises(UnsupportedOpenAPIError, match="unknown fields"):
        parse_openapi(spec)


def test_generation_is_deterministic_atomic_and_importable(tmp_path: Path) -> None:
    spec = specification("3.1.1")
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    package = generate_package(
        spec, spec_path=spec_path, output_directory=tmp_path, package_name="fixture_sdk"
    )
    first = {item.name: item.read_bytes() for item in package.iterdir() if item.is_file()}
    package = generate_package(
        spec, spec_path=spec_path, output_directory=tmp_path, package_name="fixture_sdk"
    )
    second = {item.name: item.read_bytes() for item in package.iterdir() if item.is_file()}
    assert first == second
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module("fixture_sdk")
        client_module = importlib.import_module("fixture_sdk.client")
        models_module = importlib.import_module("fixture_sdk.models")
        assert module.SyncAPI
        assert client_module.SyncDefault.getUser.declaration.operation_id == "getUser"
        assert models_module.Problem().model_dump(mode="json") == {}
        assert models_module.Problem(message=None).model_dump(mode="json") == {"message": None}
        from zapros import BaseHandler, Request, Response

        from eazy_sdk import Client

        class FixtureHandler(BaseHandler):
            def handle(self, request: Request) -> Response:
                assert str(request.url).endswith("/users/u?expand=full")
                return Response(
                    200,
                    (("content-type", "application/json"),),
                    content=b'{"id":"u"}',
                    request=request,
                )

        client = Client(base_url="https://api.test", handler=FixtureHandler())
        result = module.SyncAPI(client).default.getUser(user_id="u", expand="full")
        assert result.id == "u"
        typing = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", str(package)],
            capture_output=True,
            text=True,
        )
        assert typing.returncode == 0, typing.stdout + typing.stderr
    finally:
        sys.path.remove(str(tmp_path))
