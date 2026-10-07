"""Reserve every physical transport send with a rate limiter."""

from dataclasses import dataclass, field

from pydantic import BaseModel

from eazy_sdk import ClientConfig, Http, HttpOperation, Resilience, SyncApi, op
from eazy_sdk.ratelimit_runtime import RateLimitContext, RateLimitDecision
from examples.docs.guide_site import GuideSite, guide_client


class Result(BaseModel):
    value: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetLimited(HttpOperation[Result]):
    __http__ = Http.get("/rate")


class LimitedApi(SyncApi):
    get = op(GetLimited)


# region docs: reliability-rate-limit
# examples/docs/reliability_rate_limit.py
@dataclass(slots=True)
class CountingLimiter:
    reservations: list[str] = field(default_factory=list)

    def reserve(self, context: RateLimitContext) -> RateLimitDecision:
        self.reservations.append(f"{context.authority} attempt={context.attempt}")
        return RateLimitDecision(delay=0.0)


def main() -> None:
    limiter = CountingLimiter()
    config = ClientConfig(resilience=Resilience(rate_limiter=limiter))
    with guide_client(GuideSite(), config=config) as client:
        result = LimitedApi(client).get()
    print(f"result: {result.value}")
    print(f"reservation: {limiter.reservations[0]}")
# endregion docs: reliability-rate-limit


if __name__ == "__main__":
    main()
