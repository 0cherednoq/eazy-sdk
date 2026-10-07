"""Transport a JWT as an opaque Bearer credential."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Identity, SyncApi, op
from eazy_sdk.auth import BearerScheme
from examples.docs.request_site import request_client


class AuthResult(BaseModel):
    credential: str


# region docs: auth-jwt
# examples/docs/auth_jwt.py
JWT = BearerScheme("mail-jwt")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetProfile(HttpOperation[AuthResult]):
    __http__ = Http.get("/auth/jwt", security=JWT)


class ProfileApi(SyncApi):
    get_profile = op(GetProfile)


def main() -> None:
    credential = "eyJhbGciOiJIUzI1NiJ9.demo.signature"
    identity = Identity(auth=(JWT.static(credential),))
    with request_client() as client:
        result = ProfileApi(client, identity=identity).get_profile()
    print(f"credential kind: {result.credential}")
# endregion docs: auth-jwt


if __name__ == "__main__":
    main()
