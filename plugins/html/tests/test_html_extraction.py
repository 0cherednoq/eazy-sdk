from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Annotated, Any

import msgspec
import pytest
from eazy_sdk_html import CSS, ExtractionCompileError, ExtractionError, Scope, XPath, parse_html
from pydantic import BaseModel

from eazy_sdk.response import Html, NormalizedResponse, ResponseContext, Responses, Success
from eazy_sdk.response.cases import SuccessOutcome

HTML_FIXTURE = b"""
<html><body>
  <h1>Catalog</h1>
  <article class="product"><span class="name">Alpha</span><b class="price">10.5</b></article>
  <article class="product"><span class="name">Beta</span><b class="price">20</b></article>
  <a class="tag" href="/a">first</a><a class="tag" href="/b">second</a>
</body></html>
"""


def test_offline_html_module_import_does_not_load_zapros_or_http_transports() -> None:
    program = (
        "import sys; from eazy_sdk_html import parse_html; "
        "blocked=('zapros', 'httpx', 'requests', 'curl_cffi'); "
        "assert not [name for name in sys.modules if name.split('.')[0] in blocked]"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", program],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@dataclass
class DataclassProduct:
    name: Annotated[str, CSS(".name::text")]
    price: Annotated[float, XPath(".//*[contains(@class, 'price')]/text()")]


@dataclass
class DataclassPage:
    title: Annotated[str, CSS("h1::text")]
    products: Annotated[list[DataclassProduct], Scope(CSS(".product"))]
    tags: Annotated[list[str], CSS(".tag::attr(href)")]
    subtitle: Annotated[str | None, CSS("h2::text")] = None


class PydanticProduct(BaseModel):
    name: Annotated[str, CSS(".name::text")]
    price: Annotated[float, CSS(".price::text")]


class PydanticPage(BaseModel):
    title: Annotated[str, CSS("h1::text")]
    products: Annotated[list[PydanticProduct], Scope(CSS(".product"))]
    tags: Annotated[list[str], CSS(".tag::attr(href)")]
    subtitle: Annotated[str | None, CSS("h2::text")] = None


class MsgspecProduct(msgspec.Struct):
    name: Annotated[str, CSS(".name::text")]
    price: Annotated[float, CSS(".price::text")]


class MsgspecPage(msgspec.Struct):
    title: Annotated[str, CSS("h1::text")]
    products: Annotated[list[MsgspecProduct], Scope(CSS(".product"))]
    tags: Annotated[list[str], CSS(".tag::attr(href)")]
    subtitle: Annotated[str | None, CSS("h2::text")] = None


@pytest.mark.parametrize("model", [DataclassPage, PydanticPage, MsgspecPage])
def test_offline_html_uses_one_schema_for_all_model_libraries(model: type[Any]) -> None:
    page = parse_html(HTML_FIXTURE, model)

    assert page.title == "Catalog"
    assert [product.name for product in page.products] == ["Alpha", "Beta"]
    assert [product.price for product in page.products] == [10.5, 20.0]
    assert page.tags == ["/a", "/b"]
    assert page.subtitle is None


def test_html_response_representation_uses_the_offline_document_pipeline() -> None:
    response = NormalizedResponse(
        200,
        "https://example.test/catalog",
        "GET",
        [("Content-Type", "text/html; charset=utf-8")],
        HTML_FIXTURE,
    )

    outcome: Any = Responses(success=(Success(200, Html(DataclassPage)),)).inspect(
        ResponseContext(response)
    )

    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value.products[1].name == "Beta"


@dataclass
class MissingSelector:
    value: str


@dataclass
class ConflictingSelector:
    value: Annotated[str, CSS(".one::text"), XPath("//one/text()")]


@dataclass
class MissingRequiredValue:
    value: Annotated[str, CSS(".missing::text")]


@pytest.mark.parametrize("model", [MissingSelector, ConflictingSelector])
def test_html_schema_reports_invalid_metadata_with_model_path(model: type[object]) -> None:
    with pytest.raises(ExtractionCompileError, match=rf"{model.__name__}.value"):
        parse_html(HTML_FIXTURE, model)


def test_html_runtime_error_contains_the_full_field_path_and_selector() -> None:
    with pytest.raises(ExtractionError) as captured:
        parse_html(HTML_FIXTURE, MissingRequiredValue)

    assert captured.value.path == ("MissingRequiredValue", "value")
    assert captured.value.selector == CSS(".missing::text")


def test_nested_list_error_contains_model_fields_and_item_index() -> None:
    invalid = b"""
    <html><body><h1>Catalog</h1>
      <article class="product"><span class="name">Alpha</span><b class="price">10</b></article>
      <article class="product"><b class="price">20</b></article>
    </body></html>
    """

    with pytest.raises(ExtractionError) as captured:
        parse_html(invalid, DataclassPage)

    assert captured.value.path == ("DataclassPage", "products", "1", "name")
    assert captured.value.selector == CSS(".name::text")
