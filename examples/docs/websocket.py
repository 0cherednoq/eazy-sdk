"""Declare send, call and subscription WebSocket operations."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import op
from eazy_sdk.websocket import AsyncWsApi, Ws, WsCall, WsSend, WsSubscribe


class PriceEvent(BaseModel):
    value: int


# region docs: websocket
# examples/docs/websocket.py
@dataclass(frozen=True, slots=True, kw_only=True)
class Notify(WsSend):
    __ws__ = Ws.send("notify")
    value: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Lookup(WsCall[str]):
    __ws__ = Ws.call("lookup")
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Prices(WsSubscribe[PriceEvent]):
    __ws__ = Ws.subscribe("prices")
    symbol: str


class MarketApi(AsyncWsApi):
    notify = op(Notify)
    lookup = op(Lookup)
    prices = op(Prices)


def main() -> None:
    for operation in (Notify, Lookup, Prices):
        print(f"{operation.__ws__.kind.value}: {operation.__ws__.discriminator}")
# endregion docs: websocket


if __name__ == "__main__":
    main()
