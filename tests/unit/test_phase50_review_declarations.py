"""Review of phase 50: declarations that were quietly ignored instead of working or failing.

Five of them. A host written with capitals never matched, a document model readable by the
second configured parser was refused at compile time, ``Json(unwrap=..., extractor=...)``
dropped the pointer, ``success={200: (A, B)}`` was typed as valid and was not, and an unrelated
mixin with empty ``__slots__`` was reported as the operation base.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path as FilePath
from typing import Annotated

import httpx
import pytest
from eazy_sdk_html import CSS, ParselBackend
from eazy_sdk_xml import ElementTreeBackend
from pydantic import BaseModel, ConfigDict

from eazy_sdk import (
    Client,
    ClientConfig,
    Http,
    HttpOperation,
    Query,
    SyncApi,
    api,
)
from eazy_sdk.compile.input import inspect_operation_input
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.models import default_model_adapters
from eazy_sdk.response import ApiError, Json, Responses
from eazy_sdk.response.cases import JsonExtractor
from eazy_sdk.serialization import BackendCapabilityError, Serialization

ROOT = FilePath(__file__).resolve().parents[2]

BASE = "https://books.example"


@dataclass(frozen=True)
class Problem:
    detail: str


class Refused(ApiError[Problem]):
    pass


def _client(config: ClientConfig) -> Client:
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/page":
            return httpx.Response(
                200,
                content=b"<html><body><h1 class='title'>Dune</h1></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            404,
            content=b'{"detail": "no such book"}',
            headers={"content-type": "application/json"},
        )

    raw = httpx.Client(transport=httpx.MockTransport(serve), headers={}, cookies={})
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True), config=config)


class Books(SyncApi):
    @api.get("/thing")
    def get(self) -> Problem:
        raise NotImplementedError


# --- the client's host keys -----------------------------------------------------------


def test_client_error_hosts_are_matched_whatever_the_case() -> None:
    """The lookup lowercases the hostname, so the declaration is lowered where it is written."""

    config = ClientConfig(errors={"Books.Example": {404: Refused}})
    with _client(config) as client, pytest.raises(Refused):
        Books(client).get()


def test_one_host_written_twice_is_a_declaration_error() -> None:
    """Two spellings of one host are two answers to the same question."""

    with pytest.raises(TypeError) as failure:
        ClientConfig(errors={"Books.Example": {404: Refused}, "books.example": {410: Refused}})
    assert "names host 'books.example' twice" in str(failure.value)


# --- document models and several parsers ----------------------------------------------


@dataclass(frozen=True)
class CssTitle:
    """Only parsel reads a CSS selector; ElementTree speaks a subset of XPath."""

    title: Annotated[str, CSS("h1.title::text")]


@dataclass(frozen=True)
class AnyPage:
    """Every field optional: this model extracts from an error page just as happily."""

    title: Annotated[str | None, CSS("h1.title::text")] = None


def test_model_compiles_against_any_configured_backend() -> None:
    """``prepare`` accepts any parser that reads the model, and so must the discriminator check.

    ElementTree comes first and speaks no CSS; parsel, configured beside it, reads the page.
    """

    serialization = Serialization(documents=(ElementTreeBackend(), ParselBackend()))

    class Pages(SyncApi):
        @api.get("/page")
        def page(self) -> CssTitle:
            raise NotImplementedError

    with _client(ClientConfig()) as client:
        assert Pages(client, serialization=serialization).page().title == "Dune"


def test_document_model_with_nothing_required_is_still_refused() -> None:
    """A model that matches any page cannot tell a success from an error page."""

    serialization = Serialization(documents=(ElementTreeBackend(), ParselBackend()))

    class Pages(SyncApi):
        @api.get("/page")
        def page(self) -> AnyPage:
            raise NotImplementedError

    with _client(ClientConfig()) as client, pytest.raises(BackendCapabilityError) as failure:
        Pages(client, serialization=serialization).page()
    assert "matches any document" in str(failure.value)


# --- Json(unwrap=) and its extractor ---------------------------------------------------


def test_unwrap_with_a_custom_extractor_is_refused() -> None:
    """The pointer is what the standard extractor does; a custom one unwraps by itself."""

    with pytest.raises(ValueError) as failure:
        Json(Problem, unwrap="/data", extractor=JsonExtractor(pointer="/payload"))
    assert "pass one or the other" in str(failure.value)


def test_unwrap_alone_still_installs_the_pointer() -> None:
    """The ordinary form is untouched: ``unwrap=`` builds the extractor that reads it."""

    case = Json(Problem, unwrap="/data")
    assert isinstance(case.extractor, JsonExtractor)
    assert case.extractor.pointer == "/data"


# --- the operation base is a type, not a shape ----------------------------------------


class AuditMixin:
    """An unrelated mixin with the same empty ``__slots__`` an operation base has."""

    __slots__ = ()


def test_unrelated_empty_slots_mixin_is_not_the_operation_base() -> None:
    """D-03 is about the operation base; a mixin that merely looks like one is not it."""

    class Audited(AuditMixin, BaseModel, HttpOperation[Problem]):
        model_config = ConfigDict(frozen=True)
        __http__ = Http.get("/thing")

        page: Query[int] = 1

    schema = inspect_operation_input(
        Audited, operation_id="audited", path="/thing", models=default_model_adapters()
    )
    assert [field.python_name for field in schema.fields] == ["page"]


def test_operation_base_before_basemodel_is_still_refused() -> None:
    """The real D-03 keeps failing, and still names the base it means."""

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        class Wrong(HttpOperation[Problem], BaseModel):
            model_config = ConfigDict(frozen=True)
            __http__ = Http.get("/thing")

            page: Query[int] = 1

    with pytest.raises(PlanError) as failure:
        inspect_operation_input(
            Wrong, operation_id="wrong", path="/thing", models=default_model_adapters()
        )
    assert "must list BaseModel before HttpOperation" in str(failure.value)


# --- one status, several shapes: a list, never a tuple --------------------------------


SEVERAL_SHAPES = r'''# pyright: strict
from dataclasses import dataclass

from eazy_sdk import Http, HttpOperation


@dataclass(frozen=True)
class OrderV1:
    id: str


@dataclass(frozen=True)
class OrderV2:
    identifier: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Listed(HttpOperation[OrderV1]):
    __http__ = Http.get("/orders", success={200: [OrderV1, OrderV2]})


@dataclass(frozen=True, slots=True, kw_only=True)
class Tupled(HttpOperation[OrderV1]):
    __http__ = Http.get("/orders", success={200: (OrderV1, OrderV2)})
'''


def test_a_tuple_of_shapes_is_rejected_by_the_checker() -> None:
    """``_sequence`` unrolls a list; the type now says so instead of promising ``Sequence``."""

    with tempfile.TemporaryDirectory(prefix="phase50-review-typing-", dir=ROOT / "tests") as temp:
        source = FilePath(temp) / "shapes.py"
        source.write_text(SEVERAL_SHAPES, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", str(source)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert output.count("error:") == 1, output
    assert 'incompatible type "int": "tuple[type[OrderV1], type[OrderV2]]"' in output, output


def test_a_list_of_shapes_declares_two_cases() -> None:
    """The runtime meaning is unchanged: the list is what documents several shapes."""

    class Orders(SyncApi):
        @api.get("/orders", success={200: [Problem, CssTitle]})
        def get(self) -> Problem:
            raise NotImplementedError

    with _client(ClientConfig()) as client:
        sdk = Orders(client, serialization=Serialization(documents=(ParselBackend(),)))
        declaration = type(sdk).get.resolve_for(sdk)
    responses = declaration.responses
    assert isinstance(responses, Responses)
    assert len(responses.success) == 2


# --- what the operation declares about its service ------------------------------------


AUTHORING_SURFACE = r'''# pyright: strict
from dataclasses import dataclass

from eazy_sdk import Http, HttpOperation
from eazy_sdk.auth import BearerScheme


@dataclass(frozen=True)
class Order:
    id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Declared(HttpOperation[Order]):
    """The forms the authoring surface accepts."""

    __http__ = Http.get("/orders", security=BearerScheme(), requires=())


@dataclass(frozen=True, slots=True, kw_only=True)
class WrongSecurity(HttpOperation[Order]):
    __http__ = Http.get("/orders", security="bearer")


@dataclass(frozen=True, slots=True, kw_only=True)
class WrongSigning(HttpOperation[Order]):
    __http__ = Http.get("/orders", signing="hmac")


@dataclass(frozen=True, slots=True, kw_only=True)
class WrongRequires(HttpOperation[Order]):
    __http__ = Http.get("/orders", requires=("device",))
'''


def test_the_authoring_surface_is_checked() -> None:
    """``security=``/``signing=``/``requires=`` were ``object``: nothing was checked at all."""

    with tempfile.TemporaryDirectory(prefix="phase50-review-typing-", dir=ROOT / "tests") as temp:
        source = FilePath(temp) / "surface.py"
        source.write_text(AUTHORING_SURFACE, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", str(source)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert output.count("error:") == 3, output
    for keyword in ('"security"', '"signing"', '"requires"'):
        assert keyword in output, output
