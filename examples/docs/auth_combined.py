"""Require two credentials as one security alternative."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Identity, SyncApi, op
from eazy_sdk.auth import ApiKeyScheme, BearerScheme, all_of, any_of
from examples.docs.request_site import request_client


class AuthResult(BaseModel):
    credential: str


# region docs: auth-combined
# examples/docs/auth_combined.py
DEVICE_KEY = ApiKeyScheme.header("X-Device-Key", name="device-key")
ACCESS_TOKEN = BearerScheme("mail-access")
DEVICE_AND_USER = any_of(all_of(DEVICE_KEY, ACCESS_TOKEN))


@dataclass(frozen=True, slots=True, kw_only=True)
class GetPrivateReport(HttpOperation[AuthResult]):
    __http__ = Http.get("/auth/combined", security=DEVICE_AND_USER)


class ReportsApi(SyncApi):
    get_private_report = op(GetPrivateReport)


def main() -> None:
    identity = Identity(
        auth=(
            DEVICE_KEY.static("device-7"),
            ACCESS_TOKEN.static("access-1"),
        )
    )
    with request_client() as client:
        result = ReportsApi(client, identity=identity).get_private_report()
    print(f"credentials: {result.credential}")
# endregion docs: auth-combined


if __name__ == "__main__":
    main()
