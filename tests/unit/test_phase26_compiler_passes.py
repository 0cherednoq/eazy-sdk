from __future__ import annotations

from typing import Any, Unpack, cast

from eazy_sdk import SyncApi, api
from eazy_sdk._internal.http_compiler import compile_endpoint
from tests.unit.test_phase21_body_projection import PROJECTION, RESPONSES, PublicBody
from tests.unit.test_phase26_runtime_baseline import _contract


def test_compiler_pass_diagnostics_are_ordered_and_fingerprint_stays_frozen() -> None:
    compiled: Any = compile_endpoint(_contract())

    assert tuple(item.name for item in compiled.pass_diagnostics) == (
        "input-layout",
        "projection-private-writers",
        "crypto-signing-graph",
        "response-capabilities",
        "capabilities-fingerprint",
    )
    assert compiled.pass_diagnostics[0].item_count == 0
    assert compiled.pass_diagnostics[2].details == ("bind", "prepare")
    assert compiled.plan.fingerprint == (
        "3e954fe8fad3118ff4cdd6860bd9e35d426e352df748bc52edd3eb6a941fb46d"
    )


def test_projection_flows_through_the_same_compiler_pass_sequence() -> None:
    class ProjectionApi(SyncApi):
        @api.post("/project", body=PROJECTION, responses=RESPONSES)
        def operation(self, **request: Unpack[PublicBody]) -> object:
            raise NotImplementedError

    compiled: Any = cast(Any, ProjectionApi.operation).resolve(ProjectionApi.defaults).compile()

    input_pass, writer_pass, graph_pass, response_pass, fingerprint_pass = (
        compiled.pass_diagnostics
    )
    assert input_pass.item_count == 1
    assert writer_pass.details == ("signature-paths:0",)
    assert graph_pass.details == ("bind", "body.projection", "prepare")
    assert fingerprint_pass.details == ("context:9", "slots:1")
    assert response_pass.item_count == 0
