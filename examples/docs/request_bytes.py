"""Send an already encoded byte string as the request body."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import Http, HttpOperation, Path, SyncApi, op
from eazy_sdk.request import markers
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class Upload:
    stored: bool
    bytes: int


# docs:example:start
# examples/docs/request_bytes.py
@dataclass(frozen=True, slots=True, kw_only=True)
class PutObject(HttpOperation[Upload]):
    __http__ = Http.put("/objects/{name}", success={201: Upload})

    name: Path[str]
    payload: Annotated[
        bytes,
        markers.BytesBody(content_type="application/octet-stream"),
    ]


class ObjectsApi(SyncApi):
    put = op(PutObject)
# docs:example:end


def main() -> None:
    with request_client() as client:
        upload = ObjectsApi(client).put(name="report.bin", payload=b"mail")

    print(f"stored: {upload.stored}; bytes: {upload.bytes}")


if __name__ == "__main__":
    main()
