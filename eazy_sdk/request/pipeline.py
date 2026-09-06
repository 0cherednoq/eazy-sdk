"""The order a request's representation is built in, declared once.

Reading the old code, the answer to "is the signature computed before or after the body is
encrypted?" lived in three files, and adding a stage meant editing all three. It is one
declaration now: :data:`REQUEST_PIPELINE`. The executor knows how to run each stage and
nothing about their order, so the two cannot drift apart, and a test walks the sequence the
executor actually ran and compares it with the one declared here.

The set of stages is fixed on purpose. A pipeline whose stages users can invent is a second
hand-rolled language, and this SDK already learned that lesson once; what a declaration may
do is leave a stage out, and then it simply does not run.
"""

from __future__ import annotations

from enum import Enum


class RequestStage(Enum):
    """One step from a declared operation to the bytes that leave."""

    PROJECTION = "projection"
    """Public schema becomes wire schema, if the operation declares a projection."""

    ENVELOPE = "envelope"
    """The service's protocol envelope wraps the document — see :mod:`eazy_sdk.protocols`.

    It sits between the projection and payload crypto because an envelope is part of what the
    server reads, and therefore part of what a signature covers.
    """

    DOCUMENT_CRYPTO = "document-crypto"
    """Named fields are encrypted while the body is still a structure."""

    ENCODE = "encode"
    """The structure becomes bytes under the operation's ``JsonPolicy``."""

    ENCODED_CRYPTO = "encoded-crypto"
    """The whole encoded payload is encrypted, content type and all."""

    SIGN = "sign"
    """Signatures read the bytes that are about to leave — always last."""


REQUEST_PIPELINE: tuple[RequestStage, ...] = (
    RequestStage.PROJECTION,
    RequestStage.ENVELOPE,
    RequestStage.DOCUMENT_CRYPTO,
    RequestStage.ENCODE,
    RequestStage.ENCODED_CRYPTO,
    RequestStage.SIGN,
)

if REQUEST_PIPELINE[-1] is not RequestStage.SIGN:  # pragma: no cover - guards the invariant
    raise AssertionError("signing must be the last stage: it signs the bytes that leave")

if set(REQUEST_PIPELINE) != set(RequestStage):  # pragma: no cover - guards the invariant
    raise AssertionError("every declared stage has a place in the pipeline")


__all__ = ["REQUEST_PIPELINE", "RequestStage"]
