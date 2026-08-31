"""WebSocket frame, codec and protocol errors."""


class WebSocketError(Exception):
    pass


class FrameTooLargeError(WebSocketError):
    def __init__(self, *, actual: int, limit: int, kind: str) -> None:
        self.actual = actual
        self.limit = limit
        self.kind = kind
        super().__init__(f"{kind} frame is {actual} bytes; limit is {limit}")


class FrameTypeError(WebSocketError):
    pass


class ProtocolConfigurationError(WebSocketError):
    pass


class ProtocolEnvelopeError(WebSocketError):
    pass


class WsClientClosedError(WebSocketError):
    pass


class WsConnectError(WebSocketError):
    pass


class WsQueueOverflowError(WebSocketError):
    pass


class DeliveryNotSentError(WebSocketError):
    pass


class DeliveryUnknownError(WebSocketError):
    pass


class WsCallTimeoutError(DeliveryUnknownError):
    pass


class SubscriptionError(WebSocketError):
    pass


class SubscriptionDisconnectedError(SubscriptionError):
    pass


class SubscriptionOverflowError(SubscriptionError):
    pass


class RecoveryGapError(SubscriptionError):
    def __init__(
        self,
        *,
        expected: object,
        received: object,
        generation: int,
    ) -> None:
        self.expected = expected
        self.received = received
        self.generation = generation
        super().__init__(f"expected sequence {expected}, received {received}")


class MessageSchemaError(WebSocketError):
    pass


class UnexpectedMessageError(MessageSchemaError):
    pass


class MalformedMessageError(MessageSchemaError):
    pass


class AmbiguousMessageError(MessageSchemaError):
    pass


class RemoteMessageError(MessageSchemaError):
    def __init__(self, error: object, *, discriminator: str | None) -> None:
        self.error = error
        self.discriminator = discriminator
        super().__init__(f"documented WebSocket error reply: {discriminator or 'unknown'}")


class GraphqlOperationError(MessageSchemaError):
    def __init__(self, errors: object) -> None:
        self.errors = errors
        super().__init__("GraphQL operation terminated with an error message")
