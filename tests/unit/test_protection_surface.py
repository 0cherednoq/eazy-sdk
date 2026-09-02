from __future__ import annotations

import hashlib

import pytest

import eazy_sdk.protection as protection
from eazy_sdk.protection import ReplayPolicy, ReplaySafety, idempotency_key, safe_method

REMOVED_PROTECTION_API = {
    "BeforeCall",
    "CaptchaStep",
    "ReactionBudget",
    "ReactionBudgetExceeded",
    "ReplayAction",
    "ResponseReaction",
    "SolutionFreshness",
    "SolutionTarget",
    "SolverRegistry",
    "body_field",
    "cookie_target",
    "header_target",
    "query_target",
    "react",
    "solution_patch",
    "validate_before_call_cycles",
}


def test_replay_policy_rejects_invalid_budgets_and_proofs_at_construction() -> None:
    with pytest.raises(ValueError, match="budget cannot be negative"):
        safe_method(max_replays=-1)
    for invalid in ("", " ", "Idempotency Key", "Idempotency:Key", "ключ"):
        with pytest.raises(ValueError, match="valid HTTP field name"):
            idempotency_key(invalid)
    with pytest.raises(ValueError, match="only to idempotency-key safety"):
        ReplayPolicy(1, ReplaySafety.SAFE_METHOD, proof_name="Idempotency-Key")

    assert safe_method(max_replays=0).max_replays == 0
    assert idempotency_key("Idempotency-Key").proof_name == "Idempotency-Key"


def test_removed_protection_api_is_absent_from_module_and_exact_exports() -> None:
    assert not REMOVED_PROTECTION_API.intersection(protection.__all__)
    assert all(not hasattr(protection, name) for name in REMOVED_PROTECTION_API)
    assert protection.__all__ == sorted(protection.__all__)
    fingerprint = hashlib.sha256("\n".join(protection.__all__).encode()).hexdigest()
    assert fingerprint == "5e48d18b9752eb066b16f2141f09dd61b9ed04d4f2b9e6c539812c526b9ea4ee"
