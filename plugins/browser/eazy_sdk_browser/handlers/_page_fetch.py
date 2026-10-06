"""Shared browser-side fetch declaration and result conversion."""

from __future__ import annotations

from base64 import b64decode, b64encode
from typing import Any

from eazy_sdk_browser.fetch import PageFetchError, PageReply, PageRequest


def request_spec(request: PageRequest) -> dict[str, Any]:
    return {
        "method": request.method.upper(),
        "url": request.url,
        "headers": dict(request.headers),
        "body": b64encode(request.body).decode("ascii") if request.body is not None else None,
        "credentials": request.credentials,
        "timeout_ms": int(request.timeout * 1000),
    }


def parse_reply(request: PageRequest, reply: dict[str, Any]) -> PageReply:
    if not reply.get("ok"):
        raise PageFetchError(request.url, str(reply.get("error", "неизвестная причина")))
    return PageReply(
        status=int(reply["status"]),
        url=str(reply["url"]),
        headers=tuple((str(name), str(value)) for name, value in reply["headers"]),
        body=b64decode(reply["body"]),
        redirected=bool(reply["redirected"]),
    )


FETCH_SCRIPT = """
async (spec) => {
  const decode = (value) => {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  };
  const encode = (buffer) => {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
  };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), spec.timeout_ms);
  try {
    const response = await fetch(spec.url, {
      method: spec.method,
      headers: spec.headers,
      body: spec.body === null ? undefined : decode(spec.body),
      credentials: spec.credentials,
      redirect: 'follow',
      signal: controller.signal,
    });
    return {
      ok: true,
      status: response.status,
      url: response.url,
      redirected: response.redirected,
      headers: Array.from(response.headers.entries()),
      body: encode(await response.arrayBuffer()),
    };
  } catch (error) {
    return { ok: false, error: String((error && error.message) || error) };
  } finally {
    clearTimeout(timer);
  }
}
"""


__all__ = ["FETCH_SCRIPT", "parse_reply", "request_spec"]
