from __future__ import annotations

import hashlib

import pytest

import eazy_sdk.protection as protection
import eazy_sdk.protection.advanced as advanced
from eazy_sdk.protection import ReplayPolicy, idempotency_key, safe_method
from eazy_sdk.protection.advanced import ReplaySafety

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
    "BodyAccess",
    "CapableChallengeSolver",
    "NetworkIdentity",
    "NetworkIdentityProvider",
    "ProtectionCapabilities",
    "ProtectionBundle",
    "ResponseSignal",
    "SolverRequirement",
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
    assert fingerprint == "2a1499d46443aa533cd47e53232b9ba9753fbff805c0512562dbf41945a1b877"


def test_advanced_authoring_surface_has_one_exact_allowlist() -> None:
    assert advanced.__all__ == sorted(advanced.__all__)
    fingerprint = hashlib.sha256("\n".join(advanced.__all__).encode()).hexdigest()
    assert fingerprint == "3ac9e56f27a1179a070abd9b30450bf2735b48068b78d94eaefa9b0e0860e837"
    assert "_inspect_signals" not in advanced.__all__
    assert "_private_bindings_patch" not in advanced.__all__
    assert "_ensure_replay_allowed" not in advanced.__all__
