"""Record what a person does on a site: the traffic and the actions, on one timeline.

    uv run --with pydoll-python python capture_record.py --url https://site/ --out captures/

A visible Chrome opens on a clean profile. The person signs in and does whatever the SDK has to
repeat; the recording ends when the browser is closed, "Finish" is pressed in the overlay, or
Enter is pressed in the terminal. Everything is written as it happens, so a crash loses nothing
already recorded.

The browser is driven by Pydoll: a plain installed Chrome over CDP, no WebDriver. The traffic
comes from the Network domain. Actions are heard by a script in an isolated world of every page
and handed over through a CDP binding that exists in that world only: the page's own scripts
see neither the listener nor the binding. A binding delivers each action the moment it happens,
so the click that leaves a page is not lost with the page. It needs the ``Runtime`` domain;
on Chrome 154 a page cannot tell that it is enabled (the console-getter checks answer the same
either way), on much older builds it could. The mark overlay is an element in the page and a
site can see it: ``--no-overlay`` removes it, ``--no-actions`` removes the script, the binding
and the ``Runtime`` domain altogether and leaves the traffic.

The directory holds live credentials. It is written under a ``.gitignore`` of its own, and the
model never reads it: only the redacted copy made by ``capture_redact.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

RECORDING_FORMAT = "eazy-recording/1"
WORLD = "eazy_capture"
_HOST_ID = "__eazy_capture_overlay"
_BINDING = "__eazy_capture_emit"
_TABS_SECONDS = 0.5
_COOKIES_SECONDS = 3.0
_ACTION_WINDOW = 2.0
_NO_BODY_TYPES = {"Image", "Media", "Font"}
_CONTROL_TAGS = {"button", "input", "select", "textarea", "label", "summary"}
_MAX_LABEL = 32
_MAX_APPEARED = 12
_REDIRECT_LOW, _REDIRECT_HIGH = 300, 399
_DROPPED_HEADERS = {"content-encoding", "transfer-encoding"}

# Runs in an isolated world of every frame, before the page's own scripts.
_PAGE_SCRIPT = r"""
(() => {
  if (window.__eazy) return;
  const HOST = '%(host)s', OVERLAY = %(overlay)s;
  const BINDING = '%(binding)s';
  const state = window.__eazy = {queue: [], dirty: new Map(), stored: ''};
  const pump = () => {
    while (state.queue.length) {
      if (typeof window[BINDING] !== 'function') return;
      try { window[BINDING](state.queue[0]); } catch (error) { return; }
      state.queue.shift();
    }
  };
  const squash = s => String(s || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const inOverlay = e => e.composedPath && e.composedPath().some(n => n && n.id === HOST);
  const labelOf = el => {
    if (!el || !el.getAttribute) return '';
    const own = el.getAttribute('aria-label') || el.getAttribute('title') || el.placeholder;
    if (own) return squash(own);
    if (el.labels && el.labels.length) return squash(el.labels[0].innerText);
    if (el.tagName === 'INPUT') return squash(['submit', 'button'].includes(el.type) ? el.value : '');
    if (el.tagName === 'FORM') return '';
    return squash(el.innerText);
  };
  const selectorOf = el => {
    if (!el || !el.tagName) return '';
    if (el.id) return '#' + el.id;
    const tag = el.tagName.toLowerCase();
    if (el.getAttribute('name')) return tag + '[name="' + el.getAttribute('name') + '"]';
    const parts = [];
    for (let node = el, depth = 0; node && node.tagName && depth < 4; node = node.parentElement, depth++) {
      if (node.id) { parts.unshift('#' + node.id); break; }
      const same = node.parentElement
        ? [...node.parentElement.children].filter(c => c.tagName === node.tagName) : [];
      const nth = same.length > 1 ? ':nth-of-type(' + (same.indexOf(node) + 1) + ')' : '';
      parts.unshift(node.tagName.toLowerCase() + nth);
    }
    return parts.join(' > ');
  };
  const describe = el => el && el.tagName ? {
    tag: el.tagName.toLowerCase(), type: el.type || '', name: el.getAttribute('name') || '',
    id: el.id || '', role: el.getAttribute('role') || '', label: labelOf(el),
    selector: selectorOf(el), href: el.tagName === 'A' ? el.href : '',
  } : {tag: ''};
  const push = (kind, data) => {
    state.queue.push(JSON.stringify(Object.assign(
      {kind, at: Date.now(), url: location.href, top: window === window.top}, data)));
    pump();
  };
  const flush = () => {
    for (const [el, value] of state.dirty)
      push('action', {action: 'input', target: describe(el), value});
    state.dirty.clear();
  };
  const on = (name, handler) => addEventListener(name, e => {
    try { if (!inOverlay(e)) handler(e); } catch (error) {}
  }, true);
  on('input', e => {
    const el = e.target;
    if (el && 'value' in el && !['checkbox', 'radio', 'file'].includes(el.type))
      state.dirty.set(el, String(el.value));
  });
  on('change', e => {
    const el = e.target;
    if (!el || !el.tagName) return;
    if (['checkbox', 'radio'].includes(el.type))
      push('action', {action: 'change', target: describe(el), checked: !!el.checked});
    else if (el.type === 'file')
      push('action', {action: 'upload', target: describe(el), files: [...el.files].map(f => f.name)});
    else if (el.tagName === 'SELECT')
      push('action', {action: 'change', target: describe(el), value: String(el.value)});
    else flush();
  });
  on('focusout', flush);
  on('click', e => {
    flush();
    soon();
    const raw = e.target;
    const el = raw && raw.closest
      ? raw.closest('a,button,input,select,textarea,label,summary,[role=button],[role=link],[role=tab],[role=menuitem]') || raw
      : raw;
    push('action', {action: 'click', target: describe(el)});
  });
  on('submit', e => {
    flush();
    soon();
    const form = e.target;
    const fields = form && form.elements
      ? [...form.elements].filter(f => f.name).map(f => ({name: f.name, type: f.type || ''})) : [];
    push('action', {action: 'submit', target: describe(form), fields,
      to: form && form.action ? String(form.action) : ''});
  });
  on('keydown', e => {
    if (e.key !== 'Enter' && e.key !== 'Escape') return;
    flush();
    push('action', {action: 'keydown', key: e.key, target: describe(e.target)});
  });
  const storage = () => {
    const read = area => { const out = {}; try {
      for (let i = 0; i < area.length; i++) out[area.key(i)] = String(area.getItem(area.key(i))).slice(0, 4000);
    } catch (error) {} return out; };
    let found;
    try { found = {origin: location.origin, local: read(localStorage), session: read(sessionStorage)}; }
    catch (error) { return null; }
    const text = JSON.stringify(found);
    if (text === state.stored) return null;
    state.stored = text;
    return found;
  };
  const NOTICE = '[role=alert],[role=status],[aria-live],[class*="error" i],' +
    '[class*="alert" i],[class*="warning" i],[class*="notif" i],[class*="invalid" i]';
  const shown = el => !!(el && el.getClientRects && el.getClientRects().length);
  const look = () => {
    if (window !== window.top || !document.body) return;
    const notices = [];
    try {
      for (const el of document.querySelectorAll(NOTICE)) {
        if (!shown(el) || el.id === HOST) continue;
        const text = String(el.innerText || '').replace(/\s+/g, ' ').trim();
        if (text.length >= 3 && text.length <= 300 && !notices.includes(text)) notices.push(text);
        if (notices.length >= 8) break;
      }
    } catch (error) {}
    const lines = String(document.body.innerText || '').split('\n')
      .map(s => s.replace(/\s+/g, ' ').trim()).filter(s => s.length >= 3 && s.length <= 300);
    const heading = document.querySelector('h1,h2');
    push('page', {title: squash(document.title), notices, lines: lines.slice(0, 400),
      heading: shown(heading) ? squash(heading.innerText) : ''});
  };
  // What the page shows once it has settled: after it loads and after each action.
  const soon = () => { setTimeout(look, 700); setTimeout(look, 2500); };
  addEventListener('load', soon, true);
  soon();
  const report = () => {
    if (window !== window.top) return;
    const stored = storage();
    if (stored) push('storage', {stored});
  };
  addEventListener('pagehide', () => { flush(); report(); }, true);
  addEventListener('load', report, true);
  setInterval(() => { pump(); report(); }, 2000);
  report();
  if (!OVERLAY || window !== window.top) return;
  const build = () => {
    if (document.getElementById(HOST) || !document.documentElement) return;
    const host = document.createElement('div');
    host.id = HOST;
    // `all` first: written after them it would reset the position it is meant to keep.
    host.style.cssText = 'all:initial;position:fixed;bottom:8px;right:8px;z-index:2147483647';
    const root = host.attachShadow({mode: 'closed'});
    root.innerHTML = '<div style="font:12px system-ui;background:#111c;color:#fff;padding:6px 8px;' +
      'border-radius:8px;display:flex;gap:6px;align-items:center">' +
      '<span data-a="fold" title="hide or show" style="color:#f55;cursor:pointer">● REC</span>' +
      '<span data-a="tools" style="display:flex;gap:6px;align-items:center">' +
      '<input placeholder="scenario name" style="width:130px;font:12px system-ui;padding:2px 4px">' +
      '<button data-a="mark" style="font:12px system-ui">Mark</button>' +
      '<button data-a="finish" style="font:12px system-ui">Finish</button></span></div>';
    // The panel sits over the page: a click on "REC" folds it away from what it covers.
    const tools = root.querySelector('[data-a=tools]');
    root.querySelector('[data-a=fold]').addEventListener('click', () => {
      tools.style.display = tools.style.display === 'none' ? 'flex' : 'none';
    });
    const input = root.querySelector('input');
    const mark = () => {
      const label = input.value.trim();
      if (!label) return;
      push('mark', {label});
      input.value = '';
    };
    root.querySelector('[data-a=mark]').addEventListener('click', mark);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') mark(); e.stopPropagation(); });
    root.querySelector('[data-a=finish]').addEventListener('click', () => push('finish', {}));
    document.documentElement.appendChild(host);
  };
  build();
  addEventListener('DOMContentLoaded', build);
})();
"""


@dataclass(slots=True)
class Options:
    """How to record."""

    url: str
    out: Path
    chrome: str | None = None
    profile: Path | None = None
    proxy: str | None = None
    screenshots: bool = False
    overlay: bool = True
    actions: bool = True
    headless: bool = False
    max_body: int = 5_000_000


@dataclass(slots=True)
class Hop:
    """One request of a redirect chain and its response."""

    seq: int
    request_id: str
    method: str
    url: str
    wall: float
    request_headers: list[tuple[str, str]]
    meta: dict[str, Any]
    request_body: bytes | None = None
    status: int = 0
    response_headers: list[tuple[str, str]] = field(default_factory=list)
    response_body: bytes | None = None
    body_omitted: str = ""
    failed: str = ""
    notes: list[str] = field(default_factory=list)


def _pairs(headers: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """Turn a CDP header object into pairs; a repeated header arrives joined by newlines."""
    out: list[tuple[str, str]] = []
    for name, value in (headers or {}).items():
        if str(name).startswith(":"):
            continue
        out += [(str(name), part) for part in str(value).split("\n")]
    return out


def _parse_head(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in text.splitlines()[1:]:
        name, separator, value = line.partition(":")
        if separator and name.strip():
            out.append((name.strip(), value.strip()))
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", text).strip("_")[:80] or "site"


class Journal:
    """Writes the recording directory as things happen."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.started = time.time()
        self._events = 0
        self._exchanges = 0
        self.typed: list[dict[str, Any]] = []
        for name in ("exchanges", "bodies", "storage", "screenshots"):
            (directory / name).mkdir(parents=True, exist_ok=True)
        (directory / ".gitignore").write_text("*\n", encoding="utf-8")
        self._timeline = (directory / "timeline.jsonl").open("a", encoding="utf-8", newline="\n")

    def next_exchange(self) -> int:
        self._exchanges += 1
        return self._exchanges

    @property
    def exchanges(self) -> int:
        return self._exchanges

    def event(self, kind: str, wall: float | None = None, **data: Any) -> None:
        """Append one event to the timeline."""
        self._events += 1
        moment = time.time() if wall is None else wall
        record = {"seq": self._events, "t": round(moment - self.started, 3), "kind": kind, **data}
        self._timeline.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._timeline.flush()

    def write_json(self, relative: str, document: object) -> None:
        path = self.directory / relative
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def typed_value(self, value: str, target: Mapping[str, Any]) -> int:
        """Keep a typed value out of the timeline; return its index in ``secrets.local.json``."""
        described = {key: target.get(key, "") for key in ("type", "name", "label", "id")}
        self.typed.append({"value": value, "field": described})
        self.write_json("secrets.local.json", {"format": RECORDING_FORMAT, "typed": self.typed})
        return len(self.typed) - 1

    def write_hop(self, hop: Hop) -> None:
        """Write an exchange; called again whenever more of it becomes known."""
        stem = f"{hop.seq:04d}"
        request: dict[str, Any] = {
            "method": hop.method,
            "url": hop.url,
            "headers": [list(pair) for pair in hop.request_headers],
        }
        if hop.request_body:
            (self.directory / "bodies" / f"{stem}.req.bin").write_bytes(hop.request_body)
            request["body_file"] = f"bodies/{stem}.req.bin"
        response: dict[str, Any] | None = None
        if hop.status or hop.response_headers:
            response = {
                "status": hop.status,
                "headers": [
                    list(pair)
                    for pair in hop.response_headers
                    if pair[0].lower() not in _DROPPED_HEADERS
                ],
            }
            if hop.response_body is not None:
                (self.directory / "bodies" / f"{stem}.resp.bin").write_bytes(hop.response_body)
                response["body_file"] = f"bodies/{stem}.resp.bin"
            if hop.body_omitted:
                response["body_omitted"] = hop.body_omitted
        document = {
            "seq": hop.seq,
            "time": datetime.fromtimestamp(hop.wall, UTC).isoformat(timespec="milliseconds"),
            "request": request,
            "response": response,
            "meta": hop.meta,
            "failed": hop.failed,
            "notes": hop.notes,
        }
        self.write_json(f"exchanges/{stem}.json", document)

    def close(self) -> None:
        self._timeline.close()


class TabRecorder:
    """Listens to one tab: its traffic and, through the isolated world, its actions."""

    def __init__(self, tab: Any, journal: Journal, options: Options, stop: asyncio.Event) -> None:
        self.tab = tab
        self.id = str(getattr(tab, "target_id", id(tab)))[:8]
        self._journal = journal
        self._options = options
        self._stop = stop
        self._hops: dict[str, list[Hop]] = {}
        self._request_extra: dict[str, list[Mapping[str, Any]]] = {}
        self._response_extra: dict[str, list[Mapping[str, Any]]] = {}
        self._actions: list[tuple[float, str]] = []
        self._seen: dict[str, set[str]] = {}
        self._last_page = ""
        self._recent: list[Hop] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self.alive = True

    # --- setup ---------------------------------------------------------------------------

    async def attach(self) -> None:
        tab = self.tab
        handlers: dict[str, Callable[[Mapping[str, Any]], None]] = {
            "Network.requestWillBeSent": self._on_request,
            "Network.requestWillBeSentExtraInfo": self._on_request_extra,
            "Network.responseReceived": self._on_response,
            "Network.responseReceivedExtraInfo": self._on_response_extra,
            "Network.loadingFinished": self._on_finished,
            "Network.loadingFailed": self._on_failed,
            "Network.webSocketCreated": self._on_socket,
            "Network.webSocketFrameSent": self._on_socket_frame,
            "Network.webSocketFrameReceived": self._on_socket_frame,
        }
        for name, handler in handlers.items():
            await tab.on(name, self._guard(handler))
        await tab.enable_network_events()
        await self._command("Network.setBypassServiceWorker", bypass=True)
        # A page that redirects at once (a meta-refresh stub) is gone before its body can be
        # asked for. Documents are therefore held at the response and read there.
        await tab.on("Fetch.requestPaused", self._guard(self._on_paused))
        await self._command(
            "Fetch.enable",
            patterns=[{"urlPattern": "*", "resourceType": "Document", "requestStage": "Response"}],
        )
        # Without the Page domain Chrome runs new-document scripts only for navigations the
        # debugger itself started: a link or a form would open a page with no listener.
        await tab.enable_page_events()
        if self._options.actions:
            await tab.enable_runtime_events()
            await tab.on("Runtime.bindingCalled", self._guard(self._on_binding))
            await self._command("Runtime.addBinding", name=_BINDING, executionContextName=WORLD)
            source = _PAGE_SCRIPT % {
                "host": _HOST_ID,
                "binding": _BINDING,
                "overlay": "true" if self._options.overlay else "false",
            }
            await self._command(
                "Page.addScriptToEvaluateOnNewDocument",
                source=source,
                worldName=WORLD,
                runImmediately=True,
            )

    def _guard(self, handler: Callable[[Mapping[str, Any]], None]) -> Callable[[Any], None]:
        def run(event: Any) -> None:
            try:
                handler(event.get("params", {}) if isinstance(event, Mapping) else {})
            except Exception as error:  # noqa: BLE001 - one bad event must not end a recording
                self._journal.event("recorder_error", where=handler.__name__, error=repr(error))

        return run

    async def _command(self, method: str, **params: Any) -> dict[str, Any]:
        response = await self.tab.execute_command({"method": method, "params": params})
        return dict(response.get("result", {})) if isinstance(response, Mapping) else {}

    def _spawn(self, work: Awaitable[None]) -> None:
        async def run() -> None:
            # A tab closed under a command is the normal end of its life, not an error.
            with contextlib.suppress(Exception):
                await work

        task = asyncio.ensure_future(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- network -------------------------------------------------------------------------

    def _current(self, request_id: str) -> Hop | None:
        hops = self._hops.get(request_id)
        return hops[-1] if hops else None

    def _on_request(self, params: Mapping[str, Any]) -> None:
        request = params.get("request") or {}
        url = str(request.get("url", ""))
        if urlsplit(url).scheme not in {"http", "https"}:
            return
        request_id = str(params.get("requestId", ""))
        redirect = params.get("redirectResponse")
        previous = self._current(request_id)
        if redirect and previous is not None:
            previous.status = previous.status or int(redirect.get("status") or 0)
            if not previous.response_headers:
                previous.response_headers = _pairs(redirect.get("headers"))
            self._journal.write_hop(previous)
        wall = float(params.get("wallTime") or time.time())
        initiator = params.get("initiator") or {}
        meta: dict[str, Any] = {
            "resourceType": str(params.get("type", "")),
            "initiator": str(initiator.get("type", "")),
            "tab": self.id,
            "userGesture": bool(params.get("hasUserGesture")),
        }
        if initiator.get("url"):
            meta["initiatorUrl"] = str(initiator["url"])
        post = request.get("postData")
        hop = Hop(
            seq=self._journal.next_exchange(),
            request_id=request_id,
            method=str(request.get("method", "GET")),
            url=url + str(request.get("urlFragment") or ""),
            wall=wall,
            request_headers=_pairs(request.get("headers")),
            meta=meta,
            request_body=post.encode("utf-8") if isinstance(post, str) else None,
        )
        self._hops.setdefault(request_id, []).append(hop)
        self._reconcile(request_id)
        self._journal.write_hop(hop)
        self._journal.event(
            "request",
            wall,
            exchange=hop.seq,
            tab=self.id,
            method=hop.method,
            url=hop.url,
            resource=meta["resourceType"],
        )
        if request.get("hasPostData") and hop.request_body is None:
            self._spawn(self._read_post(hop))
        self._recent = [*self._recent[-200:], hop]
        self._associate()

    def _on_paused(self, params: Mapping[str, Any]) -> None:
        self._spawn(self._release(params))

    async def _release(self, params: Mapping[str, Any]) -> None:
        paused = str(params.get("requestId", ""))
        try:
            status = int(params.get("responseStatusCode") or 0)
            hop = self._current(str(params.get("networkId", "")))
            if hop is not None and status and not _REDIRECT_LOW <= status <= _REDIRECT_HIGH:
                result = await self._command("Fetch.getResponseBody", requestId=paused)
                raw = result.get("body")
                if isinstance(raw, str):
                    body = base64.b64decode(raw) if result.get("base64Encoded") else raw.encode()
                    if len(body) <= self._options.max_body:
                        hop.response_body = body
                        self._journal.write_hop(hop)
        except Exception as error:  # noqa: BLE001 - the page must load whatever happened here
            self._journal.event("recorder_error", where="document body", error=repr(error))
        finally:
            await self._command("Fetch.continueRequest", requestId=paused)

    def _associate(self) -> None:
        """Name, for every recent request, the last action that came before it."""
        for hop in self._recent:
            before = [a for a in self._actions if 0 <= hop.wall - a[0] <= _ACTION_WINDOW]
            label = before[-1][1] if before else None
            if label and hop.meta.get("action") != label:
                hop.meta["action"] = label
                self._journal.write_hop(hop)

    async def _read_post(self, hop: Hop) -> None:
        result = await self._command("Network.getRequestPostData", requestId=hop.request_id)
        if isinstance(result.get("postData"), str):
            hop.request_body = result["postData"].encode("utf-8")
            self._journal.write_hop(hop)

    def _on_request_extra(self, params: Mapping[str, Any]) -> None:
        request_id = str(params.get("requestId", ""))
        self._request_extra.setdefault(request_id, []).append(params)
        self._reconcile(request_id, write=True)

    def _on_response_extra(self, params: Mapping[str, Any]) -> None:
        request_id = str(params.get("requestId", ""))
        self._response_extra.setdefault(request_id, []).append(params)
        self._reconcile(request_id, write=True)

    def _reconcile(self, request_id: str, *, write: bool = False) -> None:
        """Give each hop the exact headers the browser reported for it, in order."""
        hops = self._hops.get(request_id, [])
        sent = self._request_extra.get(request_id, [])
        received = self._response_extra.get(request_id, [])
        for index, hop in enumerate(hops):
            if index < len(sent):
                hop.request_headers = _pairs(sent[index].get("headers"))
            if index < len(received):
                extra = received[index]
                text = extra.get("headersText")
                hop.response_headers = (
                    _parse_head(str(text)) if text else _pairs(extra.get("headers"))
                )
                hop.status = int(extra.get("statusCode") or hop.status)
            if write:
                self._journal.write_hop(hop)

    def _on_response(self, params: Mapping[str, Any]) -> None:
        hop = self._current(str(params.get("requestId", "")))
        if hop is None:
            return
        response = params.get("response") or {}
        hop.status = hop.status or int(response.get("status") or 0)
        if not hop.response_headers:
            hop.response_headers = _pairs(response.get("headers"))
        if response.get("fromDiskCache") or response.get("fromPrefetchCache"):
            hop.notes.append("served from the browser cache")
        self._journal.write_hop(hop)
        self._journal.event("response", exchange=hop.seq, tab=self.id, status=hop.status)

    def _on_finished(self, params: Mapping[str, Any]) -> None:
        hop = self._current(str(params.get("requestId", "")))
        if hop is None:
            return
        if hop.response_body is not None:
            return  # a document: its body was read while the response was held
        if hop.meta.get("resourceType") in _NO_BODY_TYPES:
            hop.body_omitted = f"{hop.meta['resourceType']} bodies are not recorded"
            self._journal.write_hop(hop)
            return
        size = int(params.get("encodedDataLength") or 0)
        if size > self._options.max_body:
            hop.body_omitted = f"{size} bytes on the wire, above the limit"
            self._journal.write_hop(hop)
            return
        self._spawn(self._read_body(hop))

    async def _read_body(self, hop: Hop) -> None:
        try:
            result = await self._command("Network.getResponseBody", requestId=hop.request_id)
        except Exception as error:  # noqa: BLE001 - the browser dropped the body
            hop.body_omitted = f"the browser no longer had the body: {error}"
        else:
            raw = result.get("body")
            if isinstance(raw, str):
                body = base64.b64decode(raw) if result.get("base64Encoded") else raw.encode("utf-8")
                if len(body) > self._options.max_body:
                    hop.body_omitted = f"{len(body)} bytes, above the limit"
                else:
                    hop.response_body = body
            else:
                hop.body_omitted = "the browser returned no body"
        self._journal.write_hop(hop)

    def _on_failed(self, params: Mapping[str, Any]) -> None:
        hop = self._current(str(params.get("requestId", "")))
        if hop is None:
            return
        hop.failed = str(params.get("errorText") or "failed")
        if params.get("canceled"):
            hop.failed += " (cancelled)"
        self._journal.write_hop(hop)
        self._journal.event("request_failed", exchange=hop.seq, tab=self.id, reason=hop.failed)

    def _on_socket(self, params: Mapping[str, Any]) -> None:
        self._journal.event("websocket", tab=self.id, phase="open", url=str(params.get("url", "")))

    def _on_socket_frame(self, params: Mapping[str, Any]) -> None:
        payload = str((params.get("response") or {}).get("payloadData", ""))
        self._journal.event("websocket", tab=self.id, phase="frame", size=len(payload))

    # --- actions -------------------------------------------------------------------------

    def _on_binding(self, params: Mapping[str, Any]) -> None:
        if params.get("name") != _BINDING:
            return
        event = json.loads(str(params.get("payload", "{}")))
        if isinstance(event, dict):
            self._take(event)

    def _take(self, event: dict[str, Any]) -> None:
        wall = float(event.pop("at", time.time() * 1000)) / 1000
        kind = str(event.pop("kind", "action"))
        if kind == "finish":
            self._journal.event("finish", wall, tab=self.id)
            self._stop.set()
        elif kind == "mark":
            self._journal.event("mark", wall, tab=self.id, label=str(event.get("label", "")))
        elif kind == "page":
            self._page(event, wall)
        elif kind == "storage":
            stored = event.get("stored") or {}
            origin = str(stored.get("origin", ""))
            self._journal.write_json(f"storage/{_slug(origin)}.json", stored)
            keys = sorted([*stored.get("local", {}), *stored.get("session", {})])
            self._journal.event("storage", wall, tab=self.id, origin=origin, keys=keys)
        else:
            self._action(event, wall)
            self._associate()

    def _page(self, event: dict[str, Any], wall: float) -> None:
        """Journal what the page shows, as the lines that were not there before.

        The whole text of a page is content of the person's account and never leaves this
        process. What goes to the timeline is the title, the heading, the message elements
        and the lines that appeared on this address since it was last looked at: after a
        refused sign-in that is the refusal, in the site's own words.
        """
        parts = urlsplit(str(event.get("url", "")))
        address = f"{parts.netloc}{parts.path}"
        lines = [str(line) for line in event.get("lines") or []]
        known = self._seen.get(address)
        appeared = [line for line in lines if line not in known] if known is not None else []
        self._seen[address] = set(lines)
        shown = {
            "title": str(event.get("title", "")),
            "heading": str(event.get("heading", "")),
            "notices": [str(item) for item in event.get("notices") or []],
            "appeared": appeared[:_MAX_APPEARED],
            "appeared_total": len(appeared),
            "first_view": known is None,
        }
        key = json.dumps([address, shown["title"], shown["notices"], shown["appeared"]])
        if key == self._last_page or (known is not None and not appeared and not shown["notices"]):
            return
        self._last_page = key
        self._journal.event("page", wall, tab=self.id, url=str(event.get("url", "")), **shown)

    def _action(self, event: dict[str, Any], wall: float) -> None:
        target = event.get("target") or {}
        if isinstance(event.get("value"), str):
            value = event.pop("value")
            event["length"] = len(value)
            if value:
                event["value_ref"] = self._journal.typed_value(value, target)
        # Only the text of a control names an action: the text of a list row or of a whole
        # form is content of the person's account and must not travel with every request.
        text = str(target.get("label") or "")
        control = target.get("tag") in _CONTROL_TAGS or bool(target.get("role"))
        short = bool(text) and len(text) <= _MAX_LABEL and (control or target.get("tag") == "a")
        name = text if short else target.get("name") or target.get("selector") or ""
        if event.get("action") == "submit" and event.get("to"):
            name = urlsplit(str(event["to"])).path
        label = f"{event.get('action', 'action')} {name}".strip()
        self._actions = [*self._actions[-50:], (wall, label)]
        self._journal.event("action", wall, tab=self.id, **event)

    async def mark(self, label: str) -> None:
        self._journal.event("mark", tab=self.id, label=label)
        if self._options.screenshots:
            path = self._journal.directory / "screenshots" / f"{_slug(label)}.png"
            with contextlib.suppress(Exception):
                await self.tab.take_screenshot(str(path))

    async def settle(self) -> None:
        """Wait for bodies still being read."""
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=3)


class Recorder:
    """One recording session over every tab of the browser."""

    def __init__(self, browser: Any, journal: Journal, options: Options) -> None:
        self._browser = browser
        self._journal = journal
        self._options = options
        self.stop = asyncio.Event()
        self._tabs: dict[str, TabRecorder] = {}
        self._cookies: dict[tuple[str, str], str] = {}

    async def adopt(self, tab: Any) -> TabRecorder:
        key = str(getattr(tab, "target_id", id(tab)))
        if key not in self._tabs:
            recorder = TabRecorder(tab, self._journal, self._options, self.stop)
            self._tabs[key] = recorder
            await recorder.attach()
        return self._tabs[key]

    async def watch_tabs(self) -> None:
        """Adopt new tabs and popups; end the recording when the last tab is gone."""
        while not self.stop.is_set():
            try:
                tabs = await self._browser.get_opened_tabs()
            except Exception:  # noqa: BLE001 - the browser was closed
                self.stop.set()
                return
            seen = {str(getattr(tab, "target_id", id(tab))) for tab in tabs}
            for tab in tabs:
                with contextlib.suppress(Exception):
                    await self.adopt(tab)
            for key, recorder in self._tabs.items():
                recorder.alive = key in seen
            if not seen:
                self.stop.set()
                return
            await asyncio.sleep(_TABS_SECONDS)

    async def snapshot_cookies(self, name: str) -> None:
        cookies = await self._browser.get_cookies()
        self._journal.write_json(f"storage/{name}.json", cookies)
        for cookie in cookies:
            key = (str(cookie.get("name")), str(cookie.get("domain")))
            value = str(cookie.get("value"))
            if self._cookies.get(key) != value:
                self._cookies[key] = value
                self._journal.event(
                    "cookie", name=key[0], domain=key[1], httpOnly=bool(cookie.get("httpOnly"))
                )

    async def watch_cookies(self) -> None:
        while not self.stop.is_set():
            with contextlib.suppress(Exception):
                await self.snapshot_cookies("cookies-latest")
            await asyncio.sleep(_COOKIES_SECONDS)

    async def read_terminal(self) -> None:
        """Take marks from the terminal: a line is a mark, an empty line ends the recording."""
        if not sys.stdin or not sys.stdin.isatty():
            return
        loop = asyncio.get_running_loop()
        while not self.stop.is_set():
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                # End of input, not an empty line: there is no terminal to read (a background
                # run; on Windows the null device even claims to be a terminal).
                return
            label = line.strip()
            if not label:
                self.stop.set()
                return
            for recorder in self._tabs.values():
                if recorder.alive:
                    await recorder.mark(label)
                    break

    async def finish(self) -> None:
        for recorder in self._tabs.values():
            await recorder.settle()
        with contextlib.suppress(Exception):
            await self.snapshot_cookies("cookies-end")


def _browser_options(options: Options) -> Any:
    from pydoll.browser.options import ChromiumOptions

    chromium = ChromiumOptions()  # type: ignore[no-untyped-call]
    chromium.headless = options.headless
    # A cold Chrome on a busy machine takes longer than Pydoll's ten seconds.
    chromium.start_timeout = 40
    if options.chrome:
        chromium.binary_location = options.chrome
    if options.profile is not None:
        chromium.add_argument(f"--user-data-dir={options.profile.resolve()}")
    if options.proxy:
        chromium.add_argument(f"--proxy-server={options.proxy}")
    return chromium


async def record(
    options: Options,
    driver: Callable[[Any, Callable[[str], Awaitable[None]]], Awaitable[None]] | None = None,
) -> Path:
    """Record until the browser is closed, or until ``driver`` has finished acting.

    ``driver`` stands in for the person in tests: it receives the first tab and a function
    that places a mark, and acts on the page.
    """
    from pydoll.browser.chromium import Chrome

    host = urlsplit(options.url).hostname or "site"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    directory = options.out / f"{_slug(host)}-{stamp}"
    journal = Journal(directory)
    try:
        async with Chrome(options=_browser_options(options)) as browser:
            tab = await browser.start()
            recorder = Recorder(browser, journal, options)
            version = await browser.get_version()
            journal.write_json(
                "meta.json",
                {
                    "format": RECORDING_FORMAT,
                    "started": datetime.fromtimestamp(journal.started, UTC).isoformat(),
                    "url": options.url,
                    "browser": dict(version) if isinstance(version, Mapping) else str(version),
                    "actions": options.actions,
                    "overlay": options.overlay and options.actions,
                    "profile": "persistent" if options.profile else "clean",
                },
            )
            first = await recorder.adopt(tab)
            await recorder.snapshot_cookies("cookies-start")
            workers = [
                asyncio.ensure_future(work)
                for work in (
                    recorder.watch_tabs(),
                    recorder.watch_cookies(),
                    recorder.read_terminal(),
                )
            ]
            journal.event("navigation", tab=first.id, url=options.url, reason="recorder")
            with contextlib.suppress(Exception):
                await tab.go_to(options.url)
            if driver is not None:
                try:
                    await driver(tab, first.mark)
                finally:
                    recorder.stop.set()
            await recorder.stop.wait()
            await recorder.finish()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
    finally:
        journal.event("end", exchanges=journal.exchanges)
        journal.close()
    return directory


def main(argv: Sequence[str] | None = None) -> int:
    """Open the browser and record until the person is done."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="where the browser opens")
    parser.add_argument("--out", type=Path, default=Path("captures"))
    parser.add_argument("--chrome", help="path to the Chrome binary, when it is not found")
    parser.add_argument("--profile", type=Path, help="keep and reuse a browser profile")
    parser.add_argument("--proxy", help="proxy server for the browser")
    parser.add_argument("--screenshots", action="store_true", help="take one at every mark")
    parser.add_argument("--no-overlay", action="store_true", help="marks from the terminal only")
    parser.add_argument("--no-actions", action="store_true", help="traffic only, nothing injected")
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    options = Options(
        url=args.url,
        out=args.out,
        chrome=args.chrome,
        profile=args.profile,
        proxy=args.proxy,
        screenshots=args.screenshots,
        overlay=not args.no_overlay,
        actions=not args.no_actions,
        headless=args.headless,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    print("The browser is opening. Do what the SDK has to repeat, then close the browser.")
    print("A line typed here is a scenario mark; an empty line ends the recording.")
    directory = asyncio.run(record(options))
    count = len(list((directory / "exchanges").glob("*.json")))
    print(f"recorded {count} exchanges into {directory}")
    print("This directory holds live passwords, cookies and tokens. Do not share or commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
