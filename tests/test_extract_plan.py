import pytest
from pydantic import BaseModel

from eazy_sdk.exceptions import ExtractionValidationError, MissingExtractedValueError
from eazy_sdk.extraction.extract_plan import HtmlExtractPlan
from eazy_sdk.extraction.html_inspector import HtmlInspector
from eazy_sdk.extraction.urls import ExtractedUrl

HTML = """
<html><head><meta name="csrf-token" content="meta-tok"></head><body>
  <input type="hidden" name="csrf_token" value="hidden-tok">
  <a href="/page">x</a>
  <span data-sitekey="site-123"></span>
</body></html>
"""


class LoginArtifacts(BaseModel):
    csrf_token: str
    captcha_site_key: str | None = None
    urls: list[ExtractedUrl]


def inspector() -> HtmlInspector:
    return HtmlInspector(HTML, base_url="https://host")


def test_plan_into_dict_without_model() -> None:
    plan: HtmlExtractPlan[None] = (
        HtmlExtractPlan.builder()
        .hidden_input("csrf_token", name="csrf_token", required=True)
        .meta_content("meta", name="csrf-token")
        .build()
    )
    out = plan.run(inspector())
    assert isinstance(out, dict)
    assert out["csrf_token"] == "hidden-tok"
    assert out["meta"] == "meta-tok"


def test_plan_into_pydantic_model() -> None:
    plan = (
        HtmlExtractPlan.builder(model=LoginArtifacts)
        .hidden_input("csrf_token", name="csrf_token", required=True)
        .regex("captcha_site_key", r'data-sitekey="([^"]+)"', required=False)
        .all_urls("urls")
        .build()
    )
    artifacts = plan.run(inspector())
    assert isinstance(artifacts, LoginArtifacts)
    assert artifacts.csrf_token == "hidden-tok"
    assert artifacts.captcha_site_key == "site-123"
    assert any(u.url == "https://host/page" for u in artifacts.urls)


def test_required_missing_raises() -> None:
    plan: HtmlExtractPlan[None] = (
        HtmlExtractPlan.builder().hidden_input("x", name="does_not_exist", required=True).build()
    )
    with pytest.raises(MissingExtractedValueError):
        plan.run(inspector())


def test_meta_content_builder_requires_a_key() -> None:
    with pytest.raises(ValueError, match="name= or property="):
        HtmlExtractPlan.builder().meta_content("x")


def test_form_action_returns_none_when_no_form() -> None:
    plan: HtmlExtractPlan[None] = HtmlExtractPlan.builder().form_action("action").build()
    out = plan.run(inspector())
    assert isinstance(out, dict)
    assert out["action"] is None


def test_extraction_validation_error_carries_pydantic_errors() -> None:
    # no fields → required model fields missing → ValidationError
    plan = HtmlExtractPlan.builder(model=LoginArtifacts).build()
    with pytest.raises(ExtractionValidationError) as ei:
        plan.run(inspector())
    assert ei.value.context is not None
    assert ei.value.context.pydantic_errors is not None
