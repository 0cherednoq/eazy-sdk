"""Follow a redirect inside the SDK attempt loop."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, SyncApi, op
from eazy_sdk.clients import CallOptions
from examples.docs.guide_site import GuideSite, guide_client


class Result(BaseModel):
    value: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetRedirected(HttpOperation[Result]):
    __http__ = Http.get("/redirect")


class RedirectApi(SyncApi):
    get = op(GetRedirected)


# region docs: reliability-redirect
# examples/docs/reliability_redirect.py
def main() -> None:
    site = GuideSite()
    with guide_client(site) as client:
        operation = RedirectApi(client).get
        result = operation.send(
            operation.request(),
            options=CallOptions(max_attempts=2, max_redirects=1),
        )
    print(f"result: {result.value}")
    print(*site.calls, sep="\n")
# endregion docs: reliability-redirect


if __name__ == "__main__":
    main()
