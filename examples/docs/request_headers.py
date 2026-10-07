"""Map operation fields to HTTP request headers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import UNSET, Http, HttpOperation, Omittable, SyncApi, op
from eazy_sdk.request import markers
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class Report:
    locale: str
    request_id: str | None


# docs:example:start
# examples/docs/request_headers.py
@dataclass(frozen=True, slots=True, kw_only=True)
class GetReport(HttpOperation[Report]):
    __http__ = Http.get("/reports")

    locale: Annotated[str, markers.Header("Accept-Language")] = "en"
    request_id: Annotated[Omittable[str], markers.Header("X-Request-ID")] = UNSET


class ReportsApi(SyncApi):
    get = op(GetReport)
# docs:example:end


def main() -> None:
    with request_client() as client:
        report = ReportsApi(client).get(locale="ru")

    print(f"locale: {report.locale}; request id: {report.request_id}")


if __name__ == "__main__":
    main()
