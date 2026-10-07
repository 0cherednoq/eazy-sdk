"""Attach an API key declared as an authentication scheme."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Identity, SyncApi, op
from eazy_sdk.auth import ApiKeyScheme
from examples.docs.request_site import request_client


class AuthResult(BaseModel):
    credential: str


# region docs: auth-api-key
# examples/docs/auth_api_key.py
REPORTING_KEY = ApiKeyScheme.header("X-API-Key", name="reporting-key")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetReports(HttpOperation[AuthResult]):
    __http__ = Http.get("/auth/api-key", security=REPORTING_KEY)


class ReportsApi(SyncApi):
    get_reports = op(GetReports)


def main() -> None:
    identity = Identity(auth=(REPORTING_KEY.static("key-demo-123"),))
    with request_client() as client:
        result = ReportsApi(client, identity=identity).get_reports()
    print(f"API key: {result.credential}")
# endregion docs: auth-api-key


if __name__ == "__main__":
    main()
