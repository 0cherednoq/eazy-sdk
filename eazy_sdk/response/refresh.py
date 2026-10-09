"""A redirect written into a page: ``<meta http-equiv="refresh">``.

Some sites move the client on with a page instead of a status: a ``200`` whose body is a stub
that tells the browser to go elsewhere at once. A client that only follows ``3xx`` stops at the
stub. This module reads where such a page points, and nothing else::

    <meta http-equiv="refresh" content="0;URL='https://auth.mail.example/sdc?from=...'"/>

Only what a parser can answer is answered here. A page that moves on through a script
(``window.location = ...``) needs the script run, which is a browser's job.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from .normalized import NormalizedResponse

__all__ = ["MAX_REFRESH_DELAY", "meta_refresh_target"]

MAX_REFRESH_DELAY = 5.0
"""A longer delay is a page meant to be read first, not a redirect."""

_DOCUMENT_MEDIA = frozenset({"text/html", "application/xhtml+xml"})
_HEAD_BYTES = 64 * 1024
_NOSCRIPT = re.compile(r"<noscript\b.*?</noscript\s*>", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_META = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_HTTP_EQUIV = re.compile(r"""http-equiv\s*=\s*["']?\s*refresh\b""", re.IGNORECASE)
_CONTENT = re.compile(
    r"""content\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s>]+))""",
    re.IGNORECASE,
)
_REFRESH = re.compile(
    r"""^\s*(?P<delay>\d+(?:\.\d*)?)\s*[;,]\s*url\s*=\s*(?P<url>.+?)\s*$""",
    re.IGNORECASE | re.DOTALL,
)


def meta_refresh_target(response: NormalizedResponse[object]) -> str | None:
    """The absolute address a page sends the client to, or ``None`` when it sends it nowhere.

    ``None`` for anything that is not an HTML page, for a tag inside ``<noscript>`` (a browser
    that runs scripts never follows it), for a delay longer than :data:`MAX_REFRESH_DELAY`, and
    for a page that points at itself.
    """

    if response.content_type not in _DOCUMENT_MEDIA:
        return None
    text = response.body[:_HEAD_BYTES].decode("utf-8", errors="replace")
    text = _NOSCRIPT.sub("", _COMMENT.sub("", text))
    for tag in _META.finditer(text):
        target = _target_of(tag.group(0))
        if target is None:
            continue
        resolved = urljoin(response.url, target)
        return None if resolved == response.url else resolved
    return None


def _target_of(tag: str) -> str | None:
    if _HTTP_EQUIV.search(tag) is None:
        return None
    content = _CONTENT.search(tag)
    if content is None:
        return None
    value = content.group("double") or content.group("single") or content.group("bare") or ""
    refresh = _REFRESH.match(html.unescape(value))
    if refresh is None or float(refresh.group("delay")) > MAX_REFRESH_DELAY:
        return None
    url = refresh.group("url").strip()
    # The address may carry quotes of its own inside the attribute: URL='...'.
    if len(url) >= 2 and url[0] == url[-1] and url[0] in "'\"":
        url = url[1:-1]
    return url or None
