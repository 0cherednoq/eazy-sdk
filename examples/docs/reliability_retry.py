"""Retry a temporary server response under a safe policy."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import ClientConfig, Http, HttpOperation, Resilience, RetryPolicy, SyncApi, op
from examples.docs.guide_site import GuideSite, guide_client


class Result(BaseModel):
    value: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetHealth(HttpOperation[Result]):
    __http__ = Http.get("/health")


class HealthApi(SyncApi):
    get = op(GetHealth)


# region docs: reliability-retry
# examples/docs/reliability_retry.py
def main() -> None:
    site = GuideSite()
    config = ClientConfig(
        resilience=Resilience(
            retry=RetryPolicy.safe(max_attempts=2, base_delay=0.0, max_delay=0.0)
        )
    )
    with guide_client(site, config=config) as client:
        result = HealthApi(client).get()
    print(f"result: {result.value}")
    print(f"attempts: {site.health_calls}")
# endregion docs: reliability-retry


if __name__ == "__main__":
    main()
