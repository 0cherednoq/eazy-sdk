"""Call a JSON-RPC service through the shared HTTP runtime."""

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import AsyncApi, JsonField, op
from eazy_sdk.protocols import JsonRpc, Rpc, RpcOperation
from examples.docs.guide_site import GuideSite, async_guide_client


class Receipt(BaseModel):
    reference: str


# region docs: protocols
# examples/docs/protocols.py
class BillingService:
    protocol = JsonRpc(path="/rpc")


@dataclass(frozen=True, slots=True, kw_only=True)
class Charge(RpcOperation[Receipt]):
    __http__ = Rpc.method("billing.charge")

    account: JsonField[str]
    amount: JsonField[int]


class BillingApi(BillingService, AsyncApi):
    charge = op(Charge)


async def main() -> None:
    site = GuideSite()
    async with async_guide_client(site) as client:
        receipt = await BillingApi(client).charge(account="account-7", amount=1290)
    print(f"receipt: {receipt.reference}")
    print(site.calls[0])
# endregion docs: protocols


if __name__ == "__main__":
    asyncio.run(main())
