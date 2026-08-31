"""HTTP-only operation metadata shared by the HTTP compiler and serializers."""

from enum import Enum


class RequestLocation(Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    COOKIE = "cookie"
    BODY = "body"
