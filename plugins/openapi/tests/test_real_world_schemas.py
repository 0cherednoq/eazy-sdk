from __future__ import annotations

import hashlib
import importlib
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from eazy_sdk_openapi import analyze_openapi, parse_openapi
from eazy_sdk_openapi.cli import load_document
from eazy_sdk_openapi.generator import generate_package
from eazy_sdk_openapi.ir import UnsupportedOpenAPIError

from eazy_sdk import Client, ClientConfig
from eazy_sdk.auth import Auth
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.models import default_model_adapters


def client_from_httpx(raw: httpx.Client, *, config: ClientConfig) -> Client:
    return Client(
        base_url=str(raw.base_url),
        handler=HttpxHandler(raw, owns_client=True),
        config=config,
    )


@dataclass(frozen=True, slots=True)
class RealWorldCase:
    fixture: str
    package: str
    sha256: str
    operations: int
    issue_counts: dict[str, int]


CASES = (
    RealWorldCase(
        "museum-openapi.yaml",
        "museum_sdk",
        "25861fd6f830d483b92003c9657a7d90fcfce8a427d0ee7bcb8bd4aabd178af2",
        8,
        {"raw-response-media": 1, "webhooks": 1},
    ),
)

ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures" / "real_world"
SNAPSHOTS = ROOT / "snapshots" / "real_world"


@pytest.fixture(scope="module")
def generated_packages(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    output = tmp_path_factory.mktemp("real-world-openapi")
    packages: dict[str, Path] = {}
    for case in CASES:
        source = FIXTURES / case.fixture
        packages[case.package] = generate_package(
            load_document(source),
            spec_path=source,
            output_directory=output,
            package_name=case.package,
        )
    return packages


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.package)
def test_upstream_schema_is_pinned_and_all_path_operations_are_lowered(
    case: RealWorldCase,
) -> None:
    source = FIXTURES / case.fixture
    assert hashlib.sha256(source.read_bytes()).hexdigest() == case.sha256

    document = load_document(source)
    ir = parse_openapi(document)
    report = analyze_openapi(document)

    assert len(ir.operations) == case.operations
    assert report.path_operations == report.generated_operations == case.operations
    assert report.fully_supported is False
    assert Counter(issue.feature for issue in report.issues) == case.issue_counts
    assert all(issue.pointer.startswith("#/") for issue in report.issues)


def test_real_schemas_cover_binary_and_reject_repeated_query_arrays() -> None:
    museum = parse_openapi(load_document(FIXTURES / "museum-openapi.yaml"))
    ticket = next(item for item in museum.operations if item.operation_id == "getTicketCode")
    success_types = [
        (case.media_type, case.type_expression) for case in ticket.responses if case.success
    ]
    assert success_types == [("image/png", "bytes")]

    with pytest.raises(UnsupportedOpenAPIError) as captured:
        parse_openapi(load_document(FIXTURES / "petstore-openapi.yaml"))
    assert captured.value.pointer.endswith("/~1pet~1findByTags/get/parameters/0/explode")


@pytest.mark.integration
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.package)
def test_generation_matches_snapshot_and_passes_strict_mypy(
    case: RealWorldCase, generated_packages: dict[str, Path]
) -> None:
    package = generated_packages[case.package]
    actual = {
        path.name: path.read_text(encoding="utf-8")
        for path in package.iterdir()
        if path.is_file() and path.name != "py.typed"
    }
    expected = {
        path.name.removesuffix(".snap"): path.read_text(encoding="utf-8")
        for path in (SNAPSHOTS / case.package).glob("*.snap")
    }
    assert actual == expected
    assert "from . import models as _models" not in actual["client.py"]
    assert "contracts.py" not in actual
    assert "from . import models as _models" not in actual["client.py"]
    assert "    responses=Responses(success=" not in actual["client.py"]
    assert "parameters=" not in actual["client.py"]
    assert "path_values" not in actual["client.py"]
    assert "query_values" not in actual["client.py"]
    assert "inputs.py" not in actual
    assert "EndpointContract" not in actual["client.py"]
    assert "TypedDict" in actual["client.py"]
    assert "Unpack" in actual["client.py"]
    if case.package == "museum_sdk":
        assert "class AsyncTickets(AsyncApi):" in actual["client.py"]
        assert "tickets = api_group(AsyncTickets)" in actual["client.py"]
        assert "Error as ErrorModel" in actual["client.py"]
        assert "    @api.post(" in actual["client.py"]
        assert "    async def buyMuseumTickets(" in actual["client.py"]
        assert "class GetMuseumHoursRequest(TypedDict, total=False):" in actual["client.py"]
        assert "start_date: Annotated[str | None, Query('startDate')]" in actual["client.py"]
        assert "**request: Unpack[GetMuseumHoursRequest]" in actual["client.py"]
    assert all(
        path.read_bytes().endswith(b"\n") and not path.read_bytes().endswith(b"\n\n")
        for path in package.iterdir()
        if path.is_file() and path.name != "py.typed"
    )
    assert (package / "py.typed").read_bytes() == b""

    typing = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert typing.returncode == 0, typing.stdout + typing.stderr


@pytest.mark.integration
def test_response_value_type_is_visible_to_generated_sdk_consumers(
    generated_packages: dict[str, Path],
) -> None:
    package = generated_packages["museum_sdk"]
    consumer = package.parent / "museum_consumer.py"
    consumer.write_text(
        "from typing import assert_type\n"
        "from museum_sdk import AsyncAPI\n"
        "from museum_sdk.models import BuyMuseumTickets, MuseumTicketsConfirmation\n"
        "\n"
        "async def check(api: AsyncAPI, body: BuyMuseumTickets) -> None:\n"
        "    envelope = await api.tickets.buyMuseumTickets.with_response(body=body)\n"
        "    assert_type(envelope.value, MuseumTicketsConfirmation)\n"
        "    value = await api.tickets.buyMuseumTickets(body=body)\n"
        "    assert_type(value, MuseumTicketsConfirmation)\n",
        encoding="utf-8",
        newline="\n",
    )
    typing = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(consumer)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert typing.returncode == 0, typing.stdout + typing.stderr


@pytest.mark.integration
def test_generated_required_and_normalized_kwargs_are_checked_by_mypy(
    generated_packages: dict[str, Path],
) -> None:
    package = generated_packages["museum_sdk"]
    consumer = package.parent / "museum_invalid_consumer.py"
    consumer.write_text(
        "from museum_sdk import AsyncAPI\n"
        "\n"
        "async def check(api: AsyncAPI) -> None:\n"
        "    await api.tickets.getTicketCode()\n"
        "    await api.tickets.getTicketCode(ticketId='wire-name-is-not-python-key')\n",
        encoding="utf-8",
        newline="\n",
    )
    typing = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(consumer)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert typing.returncode != 0
    assert 'Missing named argument "ticket_id"' in typing.stdout
    assert 'Unexpected keyword argument "ticketId"' in typing.stdout


@pytest.mark.integration
def test_generated_patch_model_preserves_unset_and_explicit_null(
    generated_packages: dict[str, Path],
) -> None:
    package_root = next(iter(generated_packages.values())).parent
    sys.path.insert(0, str(package_root))
    try:
        models = importlib.import_module("museum_sdk.models")
        registry = default_model_adapters()

        assert registry.dump(models.SpecialEventFields()) == {}
        assert registry.dump(models.SpecialEventFields(name=None)) == {"name": None}
    finally:
        sys.path.remove(str(package_root))
        _forget_generated_modules()


@pytest.mark.integration
def test_generated_clients_execute_json_oauth_and_binary_responses(
    generated_packages: dict[str, Path],
) -> None:
    package_root = next(iter(generated_packages.values())).parent
    sys.path.insert(0, str(package_root))
    try:
        museum = importlib.import_module("museum_sdk")
        museum_auth = importlib.import_module("museum_sdk.auth")
        museum_requests: list[httpx.Request] = []

        def museum_handler(request: httpx.Request) -> httpx.Response:
            museum_requests.append(request)
            return httpx.Response(
                200,
                content=b"\x89PNG\r\nfixture",
                headers={"Content-Type": "image/png"},
                request=request,
            )

        museum_providers = _static_auth(museum_auth.MUSEUM_PLACEHOLDER_AUTH, ("visitor", "secret"))
        museum_http = httpx.Client(
            base_url="https://museum.test",
            transport=httpx.MockTransport(museum_handler),
            headers={},
            cookies={},
        )
        with client_from_httpx(
            museum_http,
            config=ClientConfig(auth=museum_providers),
        ) as client:
            image = museum.SyncAPI(client).tickets.getTicketCode(ticket_id="ticket-7")
        assert image == b"\x89PNG\r\nfixture"
        assert museum_requests[0].url.path == "/tickets/ticket-7/qr"
        assert museum_requests[0].headers["authorization"].startswith("Basic ")

    finally:
        sys.path.remove(str(package_root))
        _forget_generated_modules()


def _static_auth(scheme: Any, credentials: Any) -> Auth:
    return cast(Auth, scheme.static(credentials))


def _forget_generated_modules() -> None:
    for name in tuple(sys.modules):
        if name == "museum_sdk" or name.startswith("museum_sdk."):
            del sys.modules[name]
        if name == "petstore_sdk" or name.startswith("petstore_sdk."):
            del sys.modules[name]
