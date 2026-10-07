"""Parse a response with the built-in Pydantic model adapter."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from eazy_sdk import Http, HttpOperation, SyncApi, op

from .guide_site import GuideSite, guide_client


# docs:integration-pydantic-model:start
# examples/docs/integration_pydantic_model.py
class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: int = Field(validation_alias="id", serialization_alias="id")
    subject: str


@dataclass(frozen=True, slots=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/42")


class MessagesApi(SyncApi):
    get = op(GetMessage)
# docs:integration-pydantic-model:end


def main() -> None:
    with guide_client(GuideSite()) as client:
        message = MessagesApi(client).get()
    print(f"pydantic: {message.message_id} {message.subject}")


if __name__ == "__main__":
    main()
