from __future__ import annotations

import subprocess
import sys
from pathlib import Path as FilePath
from typing import Annotated, Any, NotRequired, TypedDict, Unpack, cast

import pytest

import eazy_sdk.request as request_api
from eazy_sdk import ApiDefaults, SyncApi, api
from eazy_sdk._internal import PlanError, RequestLocation
from eazy_sdk.request import (
    Cookie,
    Form,
    Header,
    JsonBody,
    JsonField,
    Part,
    Path,
    Query,
    QueryString,
)
from eazy_sdk.response import Responses

RESPONSES: Responses[object] = Responses(success=())


class MuseumApi(SyncApi):
    @api.get("/museums/{museumId}", operation_id="museum", responses=RESPONSES)
    def museum(
        self,
        *,
        museum_id: Annotated[str, Path("museumId")],
        start_date: Annotated[str | None, Query("startDate")] = None,
        page: Annotated[int | None, Query()] = None,
        trace: Annotated[str | None, Header("X-Trace")] = None,
        session: Annotated[str | None, Cookie("session-id")] = None,
    ) -> object:
        raise NotImplementedError


class FlatBodyApi(SyncApi):
    @api.post("/museums", operation_id="jsonField", responses=RESPONSES)
    def json_field(self, *, value: Annotated[str, JsonField()]) -> object:
        raise NotImplementedError

    @api.post("/museums", operation_id="formField", responses=RESPONSES)
    def form_field(self, *, value: Annotated[str, Form()]) -> object:
        raise NotImplementedError

    @api.post("/museums", operation_id="partField", responses=RESPONSES)
    def part_field(self, *, value: Annotated[str, Part()]) -> object:
        raise NotImplementedError


class CreatePost(TypedDict):
    owner_id: Annotated[int, Path()]
    user_id: Annotated[int, JsonField("userId")]
    title: Annotated[str, JsonField()]
    body: Annotated[str, JsonField()]
    summary: NotRequired[Annotated[str, JsonField()]]


class MissingPlacement(TypedDict):
    title: str


class ReservedOptions(TypedDict):
    options: Annotated[str, JsonField()]


class UnpackedBodyApi(SyncApi):
    @api.post("/users/{owner_id}/posts", operation_id="unpackedBody", responses=RESPONSES)
    def create_post(
        self,
        **request: Unpack[CreatePost],
    ) -> object:
        raise NotImplementedError


def test_compiles_method_fields_in_declaration_order() -> None:
    compiled = cast(Any, MuseumApi.museum).resolve(ApiDefaults()).compile()
    assert tuple(compiled.input_slots) == (
        "museum_id",
        "start_date",
        "page",
        "trace",
        "session",
    )
    assert tuple(
        (field.python_name, field.wire_name, field.location, field.required)
        for field in compiled.input_fields
    ) == (
        ("museum_id", "museumId", RequestLocation.PATH, True),
        ("start_date", "startDate", RequestLocation.QUERY, False),
        ("page", "page", RequestLocation.QUERY, False),
        ("trace", "X-Trace", RequestLocation.HEADER, False),
        ("session", "session-id", RequestLocation.COOKIE, False),
    )


@pytest.mark.parametrize("name", ["json_field", "form_field", "part_field"])
def test_compiles_each_flat_body_field_marker(name: str) -> None:
    descriptor = getattr(FlatBodyApi, name)
    compiled = descriptor.resolve(ApiDefaults()).compile()
    assert compiled.input_fields[0].location is RequestLocation.BODY
    assert compiled.input_fields[0].wire_name == "value"


def test_compiles_root_body_from_method_annotation() -> None:
    class Api(SyncApi):
        @api.post("/museums", operation_id="root", responses=RESPONSES)
        def root(self, *, body: Annotated[list[str], JsonBody()]) -> object:
            raise NotImplementedError

    compiled = cast(Any, Api.root).resolve(ApiDefaults()).compile()
    assert compiled.body_slot is compiled.input_slots["body"]
    assert compiled.body_slot.validator(["modern", "history"]) == ["modern", "history"]


def test_unpacks_typed_dict_fields_into_the_operation_shape() -> None:
    compiled = cast(Any, UnpackedBodyApi.create_post).resolve(ApiDefaults()).compile()

    assert tuple(compiled.input_slots) == ("owner_id", "user_id", "title", "body", "summary")
    assert tuple(
        (field.python_name, field.wire_name, field.required)
        for field in compiled.input_fields
    ) == (
        ("owner_id", "owner_id", True),
        ("user_id", "userId", True),
        ("title", "title", True),
        ("body", "body", True),
        ("summary", "summary", False),
    )


def test_unpacked_typed_dict_call_binds_keyword_values_without_a_wrapper_dict() -> None:
    class RecordingClient:
        values: dict[str, object] | None = None

        def _execute_operation(
            self,
            declaration: object,
            values: dict[str, object],
            **_: object,
        ) -> object:
            self.values = values
            return object()

    client = RecordingClient()
    api = UnpackedBodyApi(client)  # type: ignore[arg-type]
    api.create_post(
        owner_id=7,
        user_id=9,
        title="Typed calls",
        body="No wrapper mapping",
    )

    assert client.values == {
        "owner_id": 7,
        "user_id": 9,
        "title": "Typed calls",
        "body": "No wrapper mapping",
    }


def test_rejects_variadic_kwargs_without_typed_dict_unpack() -> None:
    with pytest.raises(PlanError, match=r"must be Unpack\[TypedDict\]"):

        class Api(SyncApi):
            @api.post("/posts", operation_id="kwargs", responses=RESPONSES)
            def create(self, /, **request: object) -> object:
                raise NotImplementedError


def test_rejects_unpacked_typed_dict_fields_without_placements() -> None:
    with pytest.raises(PlanError, match="no placement"):

        class Api(SyncApi):
            @api.post("/posts", operation_id="missingPlacement", responses=RESPONSES)
            def create(self, **request: Unpack[MissingPlacement]) -> object:
                raise NotImplementedError


def test_rejects_reserved_options_in_unpacked_typed_dict() -> None:
    with pytest.raises(PlanError, match="reserved field 'options'"):

        class Api(SyncApi):
            @api.post("/posts", operation_id="reservedOptions", responses=RESPONSES)
            def create(self, **request: Unpack[ReservedOptions]) -> object:
                raise NotImplementedError


def test_mypy_preserves_required_and_known_unpacked_keywords(tmp_path: FilePath) -> None:
    source = tmp_path / "unpacked_api.py"
    source.write_text(
        """\
from typing import Annotated, TypedDict, Unpack

from eazy_sdk import SyncApi, api
from eazy_sdk.request import JsonField
from eazy_sdk.response import Responses

class CreatePost(TypedDict):
    title: Annotated[str, JsonField()]
    body: Annotated[str, JsonField()]

class PostsApi(SyncApi):
    @api.post("/posts", responses=Responses[object](success=()))
    def create(self, **request: Unpack[CreatePost]) -> object:
        raise NotImplementedError

def invalid_calls(api: PostsApi) -> None:
    api.create(title="missing body")
    api.create(title="known", body="known", extra="unknown")
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(source)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert 'Missing named argument "body"' in result.stdout
    assert 'Unexpected keyword argument "extra"' in result.stdout


def test_rejects_unknown_or_multiple_placements() -> None:
    with pytest.raises(PlanError, match="multiple placements"):

        class Multiple(SyncApi):
            @api.get("/museums", operation_id="multiple", responses=RESPONSES)
            def operation(self, *, value: Annotated[str, Query(), Header("X")]) -> object:
                raise NotImplementedError

    with pytest.raises(PlanError, match="no placement"):

        class Missing(SyncApi):
            @api.get("/museums", operation_id="missing", responses=RESPONSES)
            def operation(self, *, value: str) -> object:
                raise NotImplementedError


def test_rejects_invalid_body_combinations() -> None:
    with pytest.raises(PlanError, match="incompatible body field codecs"):

        class Mixed(SyncApi):
            @api.post("/museums", operation_id="mixed", responses=RESPONSES)
            def operation(
                self,
                *,
                json: Annotated[str, JsonField()],
                form: Annotated[str, Form()],
            ) -> object:
                raise NotImplementedError

    with pytest.raises(PlanError, match="root body with body fields"):

        class RootAndField(SyncApi):
            @api.post("/museums", operation_id="rootField", responses=RESPONSES)
            def operation(
                self,
                *,
                body: Annotated[list[str], JsonBody()],
                name: Annotated[str, JsonField()],
            ) -> object:
                raise NotImplementedError

    with pytest.raises(PlanError, match="QueryString with query fields"):

        class RawAndQuery(SyncApi):
            @api.get("/museums", operation_id="rawQuery", responses=RESPONSES)
            def operation(
                self,
                *,
                raw: Annotated[str, QueryString()],
                page: Annotated[int, Query()],
            ) -> object:
                raise NotImplementedError


@pytest.mark.parametrize(
    ("path", "message"),
    [("/museums/{missing}", "missing"), ("/museums", "unknown")],
)
def test_rejects_path_fields_that_do_not_match_template(path: str, message: str) -> None:
    with pytest.raises(PlanError, match=message):

        class Api(SyncApi):
            @api.get(path, operation_id="museum", responses=RESPONSES)
            def operation(self, *, museum_id: Annotated[str, Path("museumId")]) -> object:
                raise NotImplementedError


def test_parameterless_method_has_an_empty_shape() -> None:
    class HealthApi(SyncApi):
        @api.get("/health", operation_id="health", responses=RESPONSES)
        def health(self) -> object:
            raise NotImplementedError

    compiled = cast(Any, HealthApi.health).resolve(ApiDefaults()).compile()
    assert compiled.input_fields == ()
    assert compiled.plan.shape.slots == ()


@pytest.mark.parametrize(
    "removed_name",
    [
        "QueryParameter",
        "QueryStringParameter",
        "PathParameter",
        "HeaderParameter",
        "CookieParameter",
    ],
)
def test_long_parameter_names_are_not_public(removed_name: str) -> None:
    assert not hasattr(request_api, removed_name)


def test_descriptors_do_not_repeat_python_type_or_requiredness() -> None:
    query_factory: Any = Query
    json_body_factory: Any = JsonBody
    with pytest.raises(TypeError):
        query_factory("page", int)
    with pytest.raises(TypeError):
        query_factory("page", required=True)
    with pytest.raises(TypeError):
        json_body_factory(dict[str, object])
