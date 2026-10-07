"""Provide application-owned request data through Identity."""

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Identity, Omittable, SyncApi, UNSET, op
from eazy_sdk.dependencies import DependencyContext, DependencyRegistry, dependency, field
from eazy_sdk.request import markers
from examples.docs.guide_site import GuideSite, guide_client


class Result(BaseModel):
    value: str


@dataclass(frozen=True, slots=True)
class Device:
    id: str


@dataclass(frozen=True, slots=True)
class DeviceProvider:
    device: Device

    def resolve(self, context: DependencyContext) -> Device:
        return self.device


# region docs: dependencies
# examples/docs/dependencies.py
DEVICE = dependency(
    Device,
    name="device",
    apply=(field("id").to_header("X-Device-Id"),),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class GetDevice(HttpOperation[Result]):
    __http__ = Http.get("/device", requires=(DEVICE,))

    device_id: Annotated[Omittable[str], markers.Header("X-Device-Id")] = UNSET


class DeviceApi(SyncApi):
    get = op(GetDevice)


def main() -> None:
    registry = DependencyRegistry()
    registry.register(DEVICE.dependency, DeviceProvider(Device("device-7")))
    identity = Identity(dependencies=registry)
    with guide_client(GuideSite()) as client:
        result = DeviceApi(client, identity=identity).get()
    print(f"device: {result.value}")
# endregion docs: dependencies


if __name__ == "__main__":
    main()
