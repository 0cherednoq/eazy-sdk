"""Runnable Requests integration example for the documentation."""

from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op

from .guide_site import GuideSite, guide_origin


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    subject: str


@dataclass(frozen=True, slots=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/42")


class MessageApi(SyncApi):
    message = op(GetMessage)


def load_message(base_url: str) -> Message:
    # docs:integration-requests:start
    # examples/docs/integration_requests.py
    with Client.requests(base_url=base_url) as client:
        return MessageApi(client).message()
    # docs:integration-requests:end


def main() -> None:
    site = GuideSite()
    with guide_origin(site) as base_url:
        message = load_message(base_url)
    print(f"message: {message.id} {message.subject}")


if __name__ == "__main__":
    main()
