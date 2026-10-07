"""Send and receive XML through the XML body codec."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk_xml import ElementTreeXmlCodec, XmlBody, XmlResponse

from eazy_sdk import Http, HttpOperation, SyncApi, op
from eazy_sdk.response import Extracted

from .guide_site import GuideSite, guide_client


# docs:integration-xml-document:start
# examples/docs/integration_xml_document.py
codec = ElementTreeXmlCodec(root_name="order")


@dataclass(frozen=True, slots=True)
class Order:
    id: int


@dataclass(frozen=True, slots=True)
class OrderResult:
    id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateOrder(HttpOperation[OrderResult]):
    __http__ = Http.post(
        "/orders",
        success={201: Extracted(OrderResult, using=XmlResponse(codec))},
    )
    order: Annotated[Order, XmlBody(codec)]


class OrdersApi(SyncApi):
    create = op(CreateOrder)
# docs:integration-xml-document:end


def main() -> None:
    with guide_client(GuideSite()) as client:
        result = OrdersApi(client).create(order=Order(id=42))
    print(f"xml order: {result.id}")


if __name__ == "__main__":
    main()
