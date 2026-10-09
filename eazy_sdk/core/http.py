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


def auth_cookie_set_name(scheme_name: str) -> str:
    """The layout name of the cookie set one auth scheme places; never a wire name.

    A set has no name of its own on the wire, only the names of the cookies in it, so the slot
    that carries it is named after the scheme. Two schemes placing a set each keep two slots.
    """

    return f"<auth-cookies:{scheme_name}>"
