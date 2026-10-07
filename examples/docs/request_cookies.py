"""Map an operation argument to a request cookie."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import UNSET, Http, HttpOperation, Omittable, SyncApi, op
from eazy_sdk.request import markers
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class Dashboard:
    experiment: str | None
    widgets: int


# docs:example:start
# examples/docs/request_cookies.py
@dataclass(frozen=True, slots=True, kw_only=True)
class GetDashboard(HttpOperation[Dashboard]):
    __http__ = Http.get("/dashboard")

    experiment: Annotated[Omittable[str], markers.Cookie("experiment")] = UNSET


class DashboardApi(SyncApi):
    get = op(GetDashboard)
# docs:example:end


def main() -> None:
    with request_client() as client:
        dashboard = DashboardApi(client).get(experiment="new-nav")

    print(f"experiment: {dashboard.experiment}; widgets: {dashboard.widgets}")


if __name__ == "__main__":
    main()
