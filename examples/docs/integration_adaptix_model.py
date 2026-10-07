"""Route one dataclass model through an application Adaptix retort."""

from __future__ import annotations

from dataclasses import dataclass

from adaptix import Retort, name_mapping
from eazy_sdk_adaptix import adaptix_models

from eazy_sdk import Http, HttpOperation, SyncApi, op
from eazy_sdk.models import default_model_adapters
from eazy_sdk.serialization import Serialization

from .guide_site import GuideSite, guide_client


# docs:integration-adaptix-model:start
# examples/docs/integration_adaptix_model.py
@dataclass(frozen=True, slots=True)
class Message:
    message_id: int
    subject: str


retort = Retort(recipe=[name_mapping(Message, map={"message_id": "id"})])
serialization = Serialization(
    models=adaptix_models(
        default_model_adapters(),
        retort=retort,
        types=(Message,),
        names={Message: {"message_id": "id"}},
    )
)


@dataclass(frozen=True, slots=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/42")


class MessagesApi(SyncApi):
    get = op(GetMessage)
# docs:integration-adaptix-model:end


def main() -> None:
    with guide_client(GuideSite()) as client:
        message = MessagesApi(client, serialization=serialization).get()
    print(f"adaptix: {message.message_id} {message.subject}")


if __name__ == "__main__":
    main()
