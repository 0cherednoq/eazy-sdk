"""Send a Bearer token through a declared scheme."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Identity, SyncApi, op
from eazy_sdk.auth import BearerScheme
from examples.docs.request_site import request_client


class AuthResult(BaseModel):
    credential: str


# region docs: auth-bearer
# examples/docs/auth_bearer.py
ACCESS_TOKEN = BearerScheme("mail-access")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetProfile(HttpOperation[AuthResult]):
    __http__ = Http.get("/auth/bearer", security=ACCESS_TOKEN)


class ProfileApi(SyncApi):
    get_profile = op(GetProfile)


def main() -> None:
    identity = Identity(auth=(ACCESS_TOKEN.static("access-1"),))
    with request_client() as client:
        result = ProfileApi(client, identity=identity).get_profile()
    print(f"token: {result.credential}")
# endregion docs: auth-bearer


if __name__ == "__main__":
    main()
