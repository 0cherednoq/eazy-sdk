"""Phase 54: the fallback answers a response that every declared case declined.

The scenario is the one Slack's own specification is written in: 174 methods answer 200 with
either ``{"ok": true, …}`` or ``{"ok": false, "error": …}``, and the failure half is declared
under ``default``. Until this phase, the fallback was chosen before parsing, so the success case
stayed a candidate on the strength of its status, declined the body once it saw it, and the call
ended as an unexpected response instead of the error the service declared.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import msgspec
import pytest

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import ApiError, Json, MalformedResponseError

BASE = "https://slack.example"


class Ok(msgspec.Struct):
    ok: bool
    channel: str = ""


class Failure(msgspec.Struct):
    ok: bool
    error: str = ""


class SlackFailed(ApiError[Failure]):
    """The failure half of the envelope, declared once as the operation's fallback."""


def _serve(payload: object, status: int = 200) -> Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )

    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True))


@dataclass(frozen=True, slots=True, kw_only=True)
class PostMessage(HttpOperation[Ok]):
    __http__ = Http.post(
        "/api/chat.postMessage",
        success={200: Json(Ok, accept=lambda value: value.ok)},
        fallback=(Json(Failure), SlackFailed),
    )


class Chat(SyncApi):
    post_message = op(PostMessage)


# --- 54.1: the fallback is reconsidered after parsing ------------------------------------------


def test_fallback_is_used_when_the_criterion_rejects_every_candidate() -> None:
    """The declared failure is raised, where the call used to end as an unexpected response.

    ``errors=`` refuses ``DEFAULT`` — that is what ``fallback=`` is for — so before this there
    was no way to declare this response at all in the dictionary form.
    """

    with (
        _serve({"ok": False, "error": "invalid_auth"}) as client,
        pytest.raises(SlackFailed) as raised,
    ):
        Chat(client).post_message()
    assert raised.value.error == Failure(ok=False, error="invalid_auth")


def test_a_matched_case_still_wins_over_the_fallback() -> None:
    """The fallback is read only when nothing claimed the response."""

    with _serve({"ok": True, "channel": "C1"}) as client:
        assert Chat(client).post_message() == Ok(ok=True, channel="C1")


def test_a_malformed_candidate_keeps_its_outcome_instead_of_the_fallback() -> None:
    """A case that claimed the response and could not read it is not papered over.

    The body has no ``ok`` at all: the success case owns the response and the declaration or the
    body is wrong. Answering with the fallback error here would report an API failure for what is
    a broken response.
    """

    with _serve({"channel": "C1"}) as client, pytest.raises(MalformedResponseError):
        Chat(client).post_message()


def test_the_fallback_is_read_once_when_the_status_already_selected_it() -> None:
    """A status no case declares reaches the fallback before parsing, and only once.

    Reading it twice would put two matches in front of arbitration and turn the plainest
    declaration into an ambiguous response.
    """

    with (
        _serve({"ok": False, "error": "fatal_error"}, status=500) as client,
        pytest.raises(SlackFailed) as raised,
    ):
        Chat(client).post_message()
    assert raised.value.error == Failure(ok=False, error="fatal_error")
