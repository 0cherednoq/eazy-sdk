"""One-shot WebSocket replay proofs.

Replay policy is deliberately separate from reconnect policy.  WS-03 only retries the
operation that observed a delivery failure; WS-04 owns autonomous session reconnect and
subscription recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class NeverReplay:
    max_replays: int = 0


@dataclass(frozen=True, slots=True)
class ReplayIfUnsent:
    max_replays: int = 1

    def __post_init__(self) -> None:
        if self.max_replays < 1:
            raise ValueError("ReplayIfUnsent.max_replays must be positive")


@dataclass(frozen=True, slots=True)
class ReplayWithDeduplication:
    deduplication_key: str
    max_replays: int = 1

    def __post_init__(self) -> None:
        if not self.deduplication_key:
            raise ValueError("deduplication key cannot be empty")
        if self.max_replays < 1:
            raise ValueError("ReplayWithDeduplication.max_replays must be positive")


type WsReplayPolicy = NeverReplay | ReplayIfUnsent | ReplayWithDeduplication


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    delays: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if any(delay < 0 for delay in self.delays):
            raise ValueError("reconnect delays cannot be negative")


class OverflowPolicy(Enum):
    FAIL = "fail"
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"


@dataclass(frozen=True, slots=True)
class NeverResubscribe:
    pass


@dataclass(frozen=True, slots=True)
class ResubscribeFromStart:
    pass


@dataclass(frozen=True, slots=True)
class RecoverBySequence:
    position_field: str

    def __post_init__(self) -> None:
        if not self.position_field:
            raise ValueError("sequence position field cannot be empty")


@dataclass(frozen=True, slots=True)
class RecoverByToken:
    token_field: str

    def __post_init__(self) -> None:
        if not self.token_field:
            raise ValueError("recovery token field cannot be empty")


type ResubscribePolicy = (
    NeverResubscribe | ResubscribeFromStart | RecoverBySequence | RecoverByToken
)


def replay_allowed(
    policy: WsReplayPolicy,
    *,
    may_have_been_sent: bool,
    replays: int,
) -> bool:
    if replays >= policy.max_replays:
        return False
    if isinstance(policy, ReplayWithDeduplication):
        return True
    return isinstance(policy, ReplayIfUnsent) and not may_have_been_sent


__all__ = [
    "NeverReplay",
    "NeverResubscribe",
    "OverflowPolicy",
    "ReconnectPolicy",
    "RecoverBySequence",
    "RecoverByToken",
    "ReplayIfUnsent",
    "ReplayWithDeduplication",
    "ResubscribeFromStart",
    "ResubscribePolicy",
    "WsReplayPolicy",
]
