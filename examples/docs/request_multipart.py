"""Upload a named file as multipart/form-data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import Http, HttpOperation, SyncApi, op
from eazy_sdk.request import MultipartPart, markers
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class Upload:
    file_id: str
    filename: str


# docs:example:start
# examples/docs/request_multipart.py
@dataclass(frozen=True, slots=True, kw_only=True)
class UploadFile(HttpOperation[Upload]):
    __http__ = Http.post("/files", success={201: Upload})

    document: Annotated[MultipartPart, markers.Part("document")]


class FilesApi(SyncApi):
    upload = op(UploadFile)
# docs:example:end


def main() -> None:
    with request_client() as client:
        upload = FilesApi(client).upload(
            document=MultipartPart(
                b"Hello, Ada!",
                filename="avatar.txt",
                content_type="text/plain",
            )
        )

    print(f"uploaded: {upload.file_id} {upload.filename}")


if __name__ == "__main__":
    main()
