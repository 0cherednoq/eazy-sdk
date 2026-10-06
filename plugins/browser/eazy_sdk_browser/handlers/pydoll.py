"""Native Pydoll 3 adapter for an externally owned :class:`Tab`.

The adapter owns only its CDP callbacks and in-process response buffer.  It never
starts or closes a browser, context, or tab; orchestration belongs to the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
from base64 import b64decode
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self, cast
from urllib.parse import urldefrag

from pydoll.browser.tab import Tab
from pydoll.constants import Key
from pydoll.elements.web_element import WebElement
from pydoll.protocol.network.events import NetworkEvent
from pydoll.protocol.network.types import CookieSameSite
from pydoll.protocol.page.events import PageEvent

from eazy_sdk.handlers import CapabilityLevel, TransportError
from eazy_sdk_browser.errors import BrowserDeclarationError
from eazy_sdk_browser.fetch import PageFetchError, PageReply, PageRequest
from eazy_sdk_browser.handlers._page_fetch import FETCH_SCRIPT, parse_reply, request_spec
from eazy_sdk_browser.handlers._response_buffer import ResponseBuffer
from eazy_sdk_browser.handlers.capture import DEFAULT_CAPTURE, CapturePolicy
from eazy_sdk_browser.locators import Locator, Pick
from eazy_sdk_browser.network import ResponseView
from eazy_sdk_browser.profile import BrowserProfile
from eazy_sdk_browser.state import BrowserCookie, BrowserState, Origin

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from pydoll.protocol.network.types import Cookie, CookieParam

    from eazy_sdk_browser.driver import Element, LoadState

PYDOLL_PROFILE = BrowserProfile(
    "pydoll",
    network=CapabilityLevel.CAPTURE_VERIFIED,
    session_state=CapabilityLevel.BEST_EFFORT,
    page_requests=CapabilityLevel.CAPTURE_VERIFIED,
    navigation_events=CapabilityLevel.CAPTURE_VERIFIED,
    shadow_dom=CapabilityLevel.UNSUPPORTED,
    rich_text=CapabilityLevel.CAPTURE_VERIFIED,
)

_POLL = 0.05
_TEXT = re.compile(r'^text="((?:\\.|[^"\\])*)"$')
_HAS_TEXT = re.compile(r'^(.*):has-text\("((?:\\.|[^"\\])*)"\)$')
_TEXTUAL_MIME = ("text/", "json", "javascript", "xml", "svg", "form-urlencoded")
_SAME_SITE = {
    "Lax": CookieSameSite.LAX,
    "None": CookieSameSite.NONE,
    "Strict": CookieSameSite.STRICT,
}


@dataclass(frozen=True, slots=True)
class _PendingResponse:
    url: str
    status: int
    headers: tuple[tuple[str, str], ...]
    declared_length: int
    mime_type: str


class PydollElement:
    """One short-lived Pydoll element in the Eazy element protocol."""

    def __init__(self, tab: Tab, element: WebElement) -> None:
        self._tab = tab
        self._element = element

    async def fill(self, value: str) -> None:
        await self._call("fill", self._fill(value))

    async def _fill(self, value: str) -> None:
        await self._element.clear()  # type: ignore[no-untyped-call]
        await self._element.type_text(value)

    async def click(self) -> None:
        await self._call("click", self._element.click())

    async def hover(self) -> None:
        await self._call("hover", self._element.hover())

    async def check(self) -> None:
        await self._checked(True)

    async def uncheck(self) -> None:
        await self._checked(False)

    async def _checked(self, desired: bool) -> None:
        script = f"""
const kind = String(this.type || '').toLowerCase();
if (kind !== 'checkbox' && kind !== 'radio') throw new TypeError('element is not checkable');
if (this.checked !== {str(desired).lower()}) {{
  this.checked = {str(desired).lower()};
  this.dispatchEvent(new Event('input', {{ bubbles: true }}));
  this.dispatchEvent(new Event('change', {{ bubbles: true }}));
}}
"""
        await self._script("check" if desired else "uncheck", script)

    async def select(self, option: str) -> None:
        encoded = json.dumps(option)
        await self._script(
            "select",
            f"""
const wanted = {encoded};
const candidate = Array.from(this.options || []).find(
  option => option.value === wanted || option.text === wanted
);
if (!candidate) throw new Error(`option not found: ${{wanted}}`);
this.value = candidate.value;
this.dispatchEvent(new Event('input', {{ bubbles: true }}));
this.dispatchEvent(new Event('change', {{ bubbles: true }}));
""",
        )

    async def press(self, key: str) -> None:
        async def action() -> None:
            await self._element.focus()  # type: ignore[no-untyped-call]
            await self._tab.keyboard.press(_key(key))

        await self._call("press", action())

    async def text(self) -> str:
        return str(await self._call("text", self._element.text())).strip()

    async def value(self) -> str:
        response = await self._script("value", "return String(this.value ?? '')")
        return str(_script_value(response) or "")

    async def attribute(self, name: str) -> str | None:
        response = await self._script("attribute", f"return this.getAttribute({json.dumps(name)})")
        value = _script_value(response)
        return None if value is None else str(value)

    async def visible(self) -> bool:
        return bool(
            await self._call(
                "visible",
                self._element.is_visible(),  # type: ignore[no-untyped-call]
            )
        )

    async def set_html(self, html: str) -> None:
        await self._script(
            "set_html",
            f"""
this.innerHTML = {json.dumps(html)};
this.dispatchEvent(new Event('input', {{ bubbles: true }}));
this.dispatchEvent(new Event('change', {{ bubbles: true }}));
""",
        )

    async def _script(self, phase: str, script: str) -> Mapping[str, Any]:
        return cast(
            "Mapping[str, Any]",
            await self._call(
                phase,
                self._element.execute_script(script, return_by_value=True, await_promise=True),
            ),
        )

    async def _call[T](self, phase: str, awaitable: Awaitable[T]) -> T:
        try:
            return await awaitable
        except TransportError:
            raise
        except Exception as error:
            raise TransportError("pydoll", phase, 1, error) from error


class PydollDriver:
    """Adapt an existing Pydoll 3 ``Tab`` without taking ownership of it."""

    def __init__(
        self,
        tab: Tab,
        *,
        context_key: object | None = None,
        capture: CapturePolicy | None = DEFAULT_CAPTURE,
    ) -> None:
        self._tab = tab
        self._context_key = object() if context_key is None else context_key
        self._capture = capture
        self._response_buffer = ResponseBuffer()
        self._pending_responses: dict[str, _PendingResponse] = {}
        self._pending_tasks: set[asyncio.Task[None]] = set()
        self._callback_ids: list[int] = []
        self._navigations = 0
        self._main_frame_id: str | None = None
        self._attached = True
        try:
            self._attach_task: asyncio.Task[None] | None = asyncio.create_task(self._attach())
        except RuntimeError:
            self._attach_task = None

    @property
    def profile(self) -> BrowserProfile:
        if self._capture is not None:
            return PYDOLL_PROFILE
        return replace(PYDOLL_PROFILE, network=CapabilityLevel.UNSUPPORTED)

    @property
    def tab(self) -> Tab:
        return self._tab

    async def find(self, locator: Locator) -> PydollElement | None:
        await self._ready("find")
        if locator.patience <= 0:
            return await self._probe(locator, phase="find")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + locator.patience
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                async with asyncio.timeout(remaining):
                    found = await self._probe(locator, phase="find")
            except TimeoutError:
                return None
            if found is not None:
                return found
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(_POLL, remaining))

    async def peek(self, locator: Locator) -> PydollElement | None:
        await self._ready("peek")
        return await self._probe(locator, phase="peek")

    async def peek_all(self, locator: Locator) -> list[Element]:
        await self._ready("peek_all")
        scope = await self._scope(locator.frames, phase="peek_all")
        if scope is None:
            return []
        for selector in locator.selectors:
            elements = await self._candidate(scope, selector, phase="peek_all")
            visible = await self._visible(elements, phase="peek_all")
            if visible:
                return [PydollElement(self._tab, element) for element in visible]
        return []

    async def _probe(self, locator: Locator, *, phase: str) -> PydollElement | None:
        scope = await self._scope(locator.frames, phase=phase)
        if scope is None:
            return None
        groups = await asyncio.gather(
            *(self._candidate(scope, selector, phase=phase) for selector in locator.selectors)
        )
        visible_groups = await asyncio.gather(
            *(self._visible(group, phase=phase) for group in groups)
        )
        for visible in visible_groups:
            if visible:
                chosen = visible[-1] if locator.pick is Pick.last else visible[0]
                return PydollElement(self._tab, chosen)
        return None

    async def _scope(self, frames: tuple[str, ...], *, phase: str) -> Tab | WebElement | None:
        scope: Tab | WebElement = self._tab
        for selector in frames:
            candidates = await self._candidate(scope, selector, phase=phase)
            if not candidates:
                return None
            frame = candidates[0]
            try:
                if await frame.iframe_context() is None:
                    return None
            except Exception as error:
                raise TransportError("pydoll", phase, 1, error) from error
            scope = frame
        return scope

    async def _candidate(
        self, scope: Tab | WebElement, selector: str, *, phase: str
    ) -> list[WebElement]:
        try:
            exact = _TEXT.fullmatch(selector)
            contains = _HAS_TEXT.fullmatch(selector)
            if exact is not None:
                wanted = _unescape(exact.group(1))
                raw = await scope.find(
                    xpath=f"//*[normalize-space(.) = {_xpath_literal(wanted)}]",
                    timeout=0,
                    find_all=True,
                    raise_exc=False,
                )
                return [node for node in _elements(raw) if _normal(await node.text()) == wanted]
            if contains is not None:
                base = contains.group(1) or "*"
                wanted = _normal(_unescape(contains.group(2)))
                raw = await scope.query(base, timeout=0, find_all=True, raise_exc=False)
                return [node for node in _elements(raw) if wanted in _normal(await node.text())]
            if _looks_playwright_only(selector):
                msg = f"selector {selector!r} is not supported by pydoll driver"
                raise BrowserDeclarationError(msg)
            raw = await scope.query(selector, timeout=0, find_all=True, raise_exc=False)
            return _elements(raw)
        except BrowserDeclarationError:
            raise
        except Exception as error:
            raise TransportError("pydoll", phase, 1, error) from error

    async def _visible(self, elements: Sequence[WebElement], *, phase: str) -> list[WebElement]:
        try:
            states = await asyncio.gather(
                *(element.is_visible() for element in elements)  # type: ignore[no-untyped-call]
            )
        except Exception as error:
            raise TransportError("pydoll", phase, 1, error) from error
        return [element for element, visible in zip(elements, states, strict=True) if visible]

    async def set_html(self, locator: Locator, html: str) -> bool:
        found = await self.find(locator)
        if found is None:
            return False
        await found.set_html(html)
        return True

    async def location(self) -> str:
        await self._ready("location")
        return str(await self._transport("location", self._tab.current_url()))

    async def page_text(self) -> str:
        await self._ready("page_text")
        response = await self._execute("page_text", "document.body ? document.body.innerText : ''")
        return str(_script_value(response) or "")

    async def goto(self, url: str, *, wait: LoadState = "load", within: float) -> None:
        await self._ready("goto")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        try:
            async with asyncio.timeout(within):
                current = await self._tab.current_url()
                if current != url and urldefrag(current).url == urldefrag(url).url:
                    await self._tab.execute_script(f"location.href = {json.dumps(url)}")
                    return
                await self._tab.go_to(url, timeout=max(1, math.ceil(within)))
                if wait == "networkidle":
                    remaining = max(0.001, deadline - loop.time())
                    await self._tab.wait_for_network_idle(timeout=remaining)
                # Pydoll go_to waits for its configured load state.  For
                # domcontentloaded this is a documented, safe strengthening.
        except Exception as error:
            raise TransportError("pydoll", "goto", 1, error) from error

    def navigations(self) -> int:
        return self._navigations

    def mark(self) -> int:
        return self._response_buffer.mark()

    async def wait_response(
        self, url_contains: str, *, within: float, since: int = 0
    ) -> ResponseView | None:
        await self._ready("wait_response")
        return await self._response_buffer.wait(url_contains, within=within, since=since)

    async def export_state(self) -> BrowserState:
        await self._ready("export_state")
        raw_cookies = cast(
            "list[Cookie]", await self._transport("export_state", self._tab.get_cookies())
        )
        response = await self._execute(
            "export_state",
            "({origin: location.origin, items: Object.entries(localStorage)})",
        )
        state = cast("Mapping[str, Any]", _script_value(response) or {})
        origin = str(state.get("origin", ""))
        items = tuple((str(name), str(value)) for name, value in state.get("items", ()))
        origins = (Origin(origin=origin, items=items),) if origin and origin != "null" else ()
        return BrowserState(
            cookies=tuple(_cookie_in(item) for item in raw_cookies), origins=origins
        )

    async def add_cookies(self, cookies: tuple[BrowserCookie, ...]) -> None:
        await self._ready("add_cookies")
        if cookies:
            raw = cast("list[CookieParam]", [_cookie_out(cookie) for cookie in cookies])
            await self._transport("add_cookies", self._tab.set_cookies(raw))

    def context_key(self) -> object:
        return self._context_key

    async def fetch(self, request: PageRequest, *, frames: tuple[str, ...] = ()) -> PageReply:
        await self._ready("fetch")
        spec = json.dumps(request_spec(request), ensure_ascii=False)
        expression = f"({FETCH_SCRIPT})({spec})"
        try:
            async with asyncio.timeout(request.timeout):
                response = await self._evaluate_fetch(expression, frames)
        except PageFetchError:
            raise
        except TimeoutError as error:
            raise PageFetchError(request.url, "timeout") from error
        except Exception as error:
            raise TransportError("pydoll", "fetch", 1, error) from error
        reply = cast("dict[str, Any]", _script_value(response))
        return parse_reply(request, reply)

    async def _evaluate_fetch(self, expression: str, frames: tuple[str, ...]) -> Mapping[str, Any]:
        if not frames:
            return await self._tab.execute_script(
                expression, return_by_value=True, await_promise=True
            )
        scope = await self._scope(frames, phase="fetch")
        if scope is None or isinstance(scope, Tab):
            raise PageFetchError(frames[-1], f"фрейм {frames[-1]!r} не найден")
        root_raw = await scope.query("body", timeout=0, raise_exc=False)
        roots = _elements(root_raw)
        if not roots:
            raise PageFetchError(frames[-1], "документ фрейма не найден")
        return await roots[0].execute_script(
            f"return {expression}", return_by_value=True, await_promise=True
        )

    async def aclose(self) -> None:
        if not self._attached:
            return
        self._attached = False
        task = self._attach_task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        for callback_id in tuple(self._callback_ids):
            with contextlib.suppress(Exception):  # external tab may already be closed
                await self._tab.remove_callback(callback_id)
        self._callback_ids.clear()
        if self._pending_tasks:
            await asyncio.gather(*tuple(self._pending_tasks), return_exceptions=True)
        self._pending_responses.clear()

    async def __aenter__(self) -> Self:
        await self._ready("attach")
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def _ready(self, phase: str) -> None:
        if not self._attached:
            cause = RuntimeError("pydoll adapter is closed")
            raise TransportError("pydoll", phase, 1, cause) from cause
        if self._attach_task is None:
            self._attach_task = asyncio.create_task(self._attach())
        try:
            await self._attach_task
        except Exception as error:
            raise TransportError("pydoll", phase, 1, error) from error

    async def _attach(self) -> None:
        await self._tab.enable_page_events()  # type: ignore[no-untyped-call]
        if not self._attached:
            return
        self._callback_ids.append(await self._tab.on(PageEvent.FRAME_NAVIGATED, self._navigated))
        self._callback_ids.append(
            await self._tab.on(PageEvent.NAVIGATED_WITHIN_DOCUMENT, self._within_document)
        )
        if self._capture is None:
            return
        await self._tab.enable_network_events()  # type: ignore[no-untyped-call]
        if not self._attached:
            return
        self._callback_ids.append(
            await self._tab.on(NetworkEvent.RESPONSE_RECEIVED, self._response_received)
        )
        self._callback_ids.append(
            await self._tab.on(NetworkEvent.LOADING_FINISHED, self._loading_finished)
        )

    def _navigated(self, event: Mapping[str, Any]) -> None:
        if not self._attached:
            return
        frame = cast("Mapping[str, Any]", event.get("params", {})).get("frame", {})
        if not isinstance(frame, Mapping) or frame.get("parentId"):
            return
        self._main_frame_id = str(frame.get("id", "")) or self._main_frame_id
        self._navigations += 1

    def _within_document(self, event: Mapping[str, Any]) -> None:
        if not self._attached:
            return
        params = cast("Mapping[str, Any]", event.get("params", {}))
        frame_id = str(params.get("frameId", ""))
        if self._main_frame_id is None or frame_id == self._main_frame_id:
            self._main_frame_id = frame_id or self._main_frame_id
            self._navigations += 1

    def _response_received(self, event: Mapping[str, Any]) -> None:
        capture = self._capture
        if not self._attached or capture is None:
            return
        params = cast("Mapping[str, Any]", event.get("params", {}))
        resource_type = str(params.get("type", "")).lower()
        if resource_type not in capture.resource_types:
            return
        response = cast("Mapping[str, Any]", params.get("response", {}))
        headers = tuple(
            (str(name), str(value)) for name, value in response.get("headers", {}).items()
        )
        self._pending_responses[str(params.get("requestId", ""))] = _PendingResponse(
            url=str(response.get("url", "")),
            status=int(response.get("status", 0)),
            headers=headers,
            declared_length=_declared_length(headers),
            mime_type=str(response.get("mimeType", "")),
        )

    def _loading_finished(self, event: Mapping[str, Any]) -> None:
        if not self._attached:
            return
        params = cast("Mapping[str, Any]", event.get("params", {}))
        request_id = str(params.get("requestId", ""))
        pending = self._pending_responses.pop(request_id, None)
        if pending is None:
            return
        task = asyncio.create_task(self._store_response(request_id, pending))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _store_response(self, request_id: str, pending: _PendingResponse) -> None:
        capture = self._capture
        if capture is None:
            return
        dropped = pending.declared_length > capture.max_body_bytes
        body = b""
        if not dropped:
            try:
                raw = await self._tab.get_network_response_body(request_id)
            except Exception:  # noqa: BLE001 -- a vanished CDP body is not a transport action
                return
            body = _body_bytes(raw, pending.mime_type)
            dropped = len(body) > capture.max_body_bytes
        self._response_buffer.add(
            ResponseView(
                url=pending.url,
                status=pending.status,
                body=b"" if dropped else body,
                headers=pending.headers,
                body_dropped=dropped,
            ),
            max_total_bytes=capture.max_total_bytes,
        )

    async def _execute(self, phase: str, script: str) -> Mapping[str, Any]:
        return cast(
            "Mapping[str, Any]",
            await self._transport(
                phase,
                self._tab.execute_script(script, return_by_value=True, await_promise=True),
            ),
        )

    async def _transport[T](self, phase: str, awaitable: Awaitable[T]) -> T:
        try:
            return await awaitable
        except TransportError:
            raise
        except Exception as error:
            raise TransportError("pydoll", phase, 1, error) from error


def _elements(raw: WebElement | list[WebElement] | None) -> list[WebElement]:
    if raw is None:
        return []
    return raw if isinstance(raw, list) else [raw]


def _normal(value: str) -> str:
    return " ".join(value.split())


def _unescape(value: str) -> str:
    return _normal(cast("str", json.loads(f'"{value}"')))


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    joined = ', "\'", '.join(f"'{part}'" for part in parts)
    return f"concat({joined})"


def _looks_playwright_only(selector: str) -> bool:
    return any(token in selector for token in (">>", ":visible", ":text(", ":text-is("))


def _script_value(response: Mapping[str, Any]) -> object:
    result = cast("Mapping[str, Any]", response.get("result", {}))
    remote = cast("Mapping[str, Any]", result.get("result", {}))
    return remote.get("value")


def _key(value: str) -> Key:
    normalized = value.replace("-", "").replace("_", "").upper()
    member = Key.__members__.get(normalized)
    if member is not None:
        return member
    for candidate in Key:
        if candidate.value[0] == value:
            return candidate
    msg = f"Pydoll does not support key {value!r}"
    raise BrowserDeclarationError(msg)


def _declared_length(headers: tuple[tuple[str, str], ...]) -> int:
    for name, value in headers:
        if name.lower() == "content-length" and value.isdigit():
            return int(value)
    return 0


def _body_bytes(body: str, mime_type: str) -> bytes:
    if any(marker in mime_type.lower() for marker in _TEXTUAL_MIME):
        return body.encode("utf-8")
    try:
        return b64decode(body, validate=True)
    except ValueError:
        return body.encode("utf-8")


def _cookie_in(raw: Cookie) -> BrowserCookie:
    expires = float(raw.get("expires", 0) or 0)
    same_site = raw.get("sameSite", "")
    return BrowserCookie(
        name=str(raw.get("name", "")),
        value=str(raw.get("value", "")),
        domain=str(raw.get("domain", "")),
        path=str(raw.get("path", "/")),
        expires_at=datetime.fromtimestamp(expires, tz=UTC) if expires > 0 else None,
        secure=bool(raw.get("secure", False)),
        http_only=bool(raw.get("httpOnly", False)),
        same_site=str(getattr(same_site, "value", same_site) or ""),
    )


def _cookie_out(cookie: BrowserCookie) -> dict[str, Any]:
    if not cookie.domain:
        msg = f"кука {cookie.name!r} без домена: браузеру некуда её положить"
        raise ValueError(msg)
    raw: dict[str, Any] = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "secure": cookie.secure,
        "httpOnly": cookie.http_only,
    }
    if cookie.expires_at is not None:
        raw["expires"] = cookie.expires_at.timestamp()
    same_site = _SAME_SITE.get(cookie.same_site)
    if same_site is not None:
        raw["sameSite"] = same_site
    return raw


__all__ = ["PYDOLL_PROFILE", "PydollDriver", "PydollElement"]
