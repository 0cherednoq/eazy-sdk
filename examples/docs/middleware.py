"""Wrap one logical SDK call with call middleware."""

from dataclasses import dataclass
from pydantic import BaseModel

from eazy_sdk import ClientConfig, Hooks, Http, HttpOperation, SyncApi, op
from eazy_sdk.middleware import CallMiddlewareContext, NextCall, call_middleware
from examples.docs.guide_site import GuideSite, guide_client


class Result(BaseModel):
    value: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetStatus(HttpOperation[Result]):
    __http__ = Http.get("/middleware", operation_id="getStatus")


class StatusApi(SyncApi):
    get = op(GetStatus)


# region docs: middleware
# examples/docs/middleware.py
class TraceCalls:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def __call__[T](
        self,
        context: CallMiddlewareContext[T],
        call_next: NextCall[T],
    ) -> T:
        self.events.append(f"start:{context.operation.operation_id}")
        result = await call_next(context)
        self.events.append(f"finish:{context.operation.operation_id}")
        return result


def main() -> None:
    trace = TraceCalls()
    config = ClientConfig(hooks=Hooks(middleware=(call_middleware(trace),)))
    with guide_client(GuideSite(), config=config) as client:
        result = StatusApi(client).get()
    print(result.value)
    print(*trace.events, sep="\n")
# endregion docs: middleware


if __name__ == "__main__":
    main()
