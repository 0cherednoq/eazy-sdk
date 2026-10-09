"""HTTP-only operation metadata shared by the HTTP compiler and serializers."""

from dataclasses import dataclass
from enum import Enum


class RequestLocation(Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    COOKIE = "cookie"
    BODY = "body"


@dataclass(frozen=True, slots=True)
class ManagedCookieSetDescriptor:
    """Layout marker for a dynamic, validated private cookie set."""
