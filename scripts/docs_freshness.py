"""Freshness control for the Eazy SDK documentation site.

Every non-draft docs page declares in frontmatter the public ``eazy_sdk``
symbols it documents (``sources``) and a fingerprint of their public API
surface (``api_fingerprint``). This script detects pages whose recorded
fingerprint no longer matches the current API:

    uv run python scripts/docs_freshness.py check [--json]
    uv run python scripts/docs_freshness.py update [PAGE ...]

``check`` exits non-zero when any page is stale. ``update`` recomputes and
rewrites ``api_fingerprint`` (run it only AFTER the page content has been
brought up to date). Per-symbol hashes are kept in
``docs-site/api-fingerprints.lock.json`` so ``check`` can name the exact
symbols that changed.

The fingerprint covers signatures of public callables and names/annotations
of public attributes. It deliberately does NOT cover semantics: a behavior
change behind an unchanged signature will not be detected.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOCS_DIR = REPO_ROOT / "docs-site" / "src" / "content" / "docs"
DEFAULT_LOCK_FILE = REPO_ROOT / "docs-site" / "api-fingerprints.lock.json"

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_PAGE_SUFFIXES = {".md", ".mdx"}


class SymbolResolutionError(Exception):
    """A dotted path from ``sources`` does not resolve to a live object."""


@dataclass(slots=True)
class PageMeta:
    """Frontmatter fields relevant to freshness, for one docs page."""

    path: Path
    rel: str
    draft: bool
    freshness: bool
    sources: list[str]
    api_fingerprint: str | None


@dataclass(slots=True)
class PageReport:
    """Staleness verdict for one page."""

    page: str
    stale_symbols: list[str]
    missing_symbols: list[str]


@dataclass(slots=True)
class LockEntry:
    """Per-symbol hashes recorded by ``update`` for one page."""

    fingerprint: str
    symbols: dict[str, str]


# --- frontmatter ----------------------------------------------------------


def _parse_frontmatter_fields(block: str) -> tuple[bool, bool, list[str], str | None]:
    """Extract draft/freshness/sources/api_fingerprint from a frontmatter block.

    Minimal YAML subset on purpose (stdlib only): top-level scalar keys and a
    top-level ``sources`` list of strings; nested mappings are ignored.
    """
    draft = False
    freshness = True
    sources: list[str] = []
    fingerprint: str | None = None
    current_key: str | None = None
    for raw in block.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        is_top_level = not raw.startswith((" ", "\t"))
        if is_top_level and not stripped.startswith("- "):
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key
            if key == "draft":
                draft = value.lower() == "true"
            elif key == "freshness":
                freshness = value.lower() != "false"
            elif key == "api_fingerprint":
                fingerprint = value.strip("'\"") or None
        elif stripped.startswith("- ") and current_key == "sources":
            sources.append(stripped[2:].strip().strip("'\""))
    return draft, freshness, sources, fingerprint


def iter_pages(docs_dir: Path) -> Iterator[PageMeta]:
    """Yield metadata for every docs page under *docs_dir*."""
    for path in sorted(docs_dir.rglob("*")):
        if path.suffix not in _PAGE_SUFFIXES or not path.is_file():
            continue
        match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
        if match is None:
            continue
        draft, freshness, sources, fingerprint = _parse_frontmatter_fields(match.group(1))
        yield PageMeta(
            path=path,
            rel=path.relative_to(docs_dir).as_posix(),
            draft=draft,
            freshness=freshness,
            sources=sources,
            api_fingerprint=fingerprint,
        )


def _eligible(meta: PageMeta) -> bool:
    return not meta.draft and meta.freshness


# --- API surface fingerprinting -------------------------------------------


def resolve_symbol(dotted: str) -> object:
    """Resolve a dotted path to a module/class/function, importing lazily."""
    parts = dotted.split(".")
    last_error: Exception | None = None
    for prefix_len in range(len(parts), 0, -1):
        module_name = ".".join(parts[:prefix_len])
        try:
            obj: object = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            last_error = error
            continue
        try:
            for attr in parts[prefix_len:]:
                obj = getattr(obj, attr)
        except AttributeError as error:
            raise SymbolResolutionError(f"{dotted!r}: {error}") from error
        return obj
    raise SymbolResolutionError(f"{dotted!r}: no importable module prefix") from last_error


def _signature_of(obj: object) -> str:
    try:
        signature = str(inspect.signature(obj))  # type: ignore[arg-type]
        return re.sub(r"0x[0-9A-Fa-f]+", "0x…", signature)
    except ValueError, TypeError:
        return "<no-signature>"


def _member_surface(member: object) -> str:
    if inspect.isfunction(member) or inspect.ismethod(member):
        return f"def{_signature_of(member)}"
    if isinstance(member, property):
        return "property"
    if inspect.isdatadescriptor(member):
        return "descriptor"
    return f"attr:{type(member).__name__}"


def _public_annotations(obj: type | ModuleType) -> dict[str, str]:
    try:
        annotations = inspect.get_annotations(obj)
    except Exception:  # lazy (PEP 649) annotations may fail arbitrarily
        return {}
    return {name: str(value) for name, value in annotations.items() if not name.startswith("_")}


def symbol_surface(obj: object) -> dict[str, object]:
    """Build a JSON-able description of the symbol's public API surface."""
    if inspect.isclass(obj):
        members: dict[str, str] = {}
        for name, member in inspect.getmembers(obj):
            if name.startswith("_") and name not in {"__init__", "__call__"}:
                continue
            members[name] = _member_surface(member)
        return {
            "kind": "class",
            "name": obj.__qualname__,
            "members": members,
            "annotations": _public_annotations(obj),
        }
    if inspect.ismodule(obj):
        exported = getattr(obj, "__all__", None)
        names = (
            list(exported)
            if exported is not None
            else [name for name in vars(obj) if not name.startswith("_")]
        )
        return {"kind": "module", "exports": sorted(str(name) for name in names)}
    if callable(obj):
        return {"kind": "function", "signature": _signature_of(obj)}
    return {"kind": "object", "type": type(obj).__qualname__}


def _sha256(payload: object) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def symbol_fingerprint(dotted: str) -> str:
    """Fingerprint of one symbol's current public API surface."""
    return "sha256:" + _sha256(symbol_surface(resolve_symbol(dotted)))


def page_fingerprint(symbol_hashes: Mapping[str, str]) -> str:
    """Combined fingerprint over all of a page's source symbols."""
    return "sha256:" + _sha256(dict(sorted(symbol_hashes.items())))


# --- lock file -------------------------------------------------------------


def load_lock(lock_file: Path) -> dict[str, LockEntry]:
    if not lock_file.exists():
        return {}
    raw: object = json.loads(lock_file.read_text(encoding="utf-8"))
    lock: dict[str, LockEntry] = {}
    if isinstance(raw, dict):
        for page, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            fingerprint = entry.get("fingerprint")
            symbols = entry.get("symbols")
            if isinstance(fingerprint, str) and isinstance(symbols, dict):
                lock[str(page)] = LockEntry(
                    fingerprint=fingerprint,
                    symbols={str(key): str(value) for key, value in symbols.items()},
                )
    return lock


def save_lock(lock_file: Path, lock: Mapping[str, LockEntry]) -> None:
    payload = {
        page: {"fingerprint": entry.fingerprint, "symbols": dict(sorted(entry.symbols.items()))}
        for page, entry in sorted(lock.items())
    }
    lock_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# --- commands ---------------------------------------------------------------


def _collect_hashes(sources: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    hashes: dict[str, str] = {}
    missing: list[str] = []
    for dotted in sources:
        try:
            hashes[dotted] = symbol_fingerprint(dotted)
        except SymbolResolutionError:
            missing.append(dotted)
    return hashes, missing


def run_check(docs_dir: Path, lock_file: Path, *, json_output: bool) -> int:
    lock = load_lock(lock_file)
    reports: list[PageReport] = []
    checked = 0
    for meta in iter_pages(docs_dir):
        if not _eligible(meta):
            continue
        checked += 1
        hashes, missing = _collect_hashes(meta.sources)
        current = page_fingerprint(hashes)
        fresh = not missing and bool(meta.sources) and meta.api_fingerprint == current
        if fresh:
            continue
        locked = lock.get(meta.rel)
        if locked is not None:
            stale_symbols = sorted(
                dotted for dotted, digest in hashes.items() if locked.symbols.get(dotted) != digest
            )
        else:
            # No granular history recorded yet: be conservative, name all sources.
            stale_symbols = sorted(hashes)
        reports.append(
            PageReport(page=meta.rel, stale_symbols=stale_symbols, missing_symbols=missing)
        )
    if json_output:
        payload = {
            "checked": checked,
            "stale": [
                {
                    "page": report.page,
                    "stale_symbols": report.stale_symbols,
                    "missing_symbols": report.missing_symbols,
                }
                for report in reports
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif reports:
        print(f"STALE: {len(reports)} of {checked} page(s) out of date")
        for report in reports:
            details: list[str] = []
            if report.stale_symbols:
                details.append("changed: " + ", ".join(report.stale_symbols))
            if report.missing_symbols:
                details.append("missing: " + ", ".join(report.missing_symbols))
            print(f"  - {report.page}" + (f" ({'; '.join(details)})" if details else ""))
        print("Update the page content, then run: python scripts/docs_freshness.py update <page>")
    else:
        print(f"OK: {checked} page(s) fresh")
    return 1 if reports else 0


def _write_fingerprint(path: Path, fingerprint: str) -> None:
    """Rewrite only the ``api_fingerprint`` frontmatter line, byte-preserving the rest."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError(f"{path}: no frontmatter block")
    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        raise ValueError(f"{path}: unterminated frontmatter block")
    new_line_body = f'api_fingerprint: "{fingerprint}"'
    for index in range(1, closing):
        body = lines[index].rstrip("\r\n")
        if body.startswith("api_fingerprint:"):
            lines[index] = new_line_body + lines[index][len(body) :]
            break
    else:
        ending = "\r\n" if "\r\n" in lines[0] else "\n"
        lines.insert(closing, new_line_body + ending)
    path.write_text("".join(lines), encoding="utf-8", newline="")


def _normalize_page_arg(arg: str, docs_dir: Path) -> str:
    candidate = Path(arg)
    if candidate.is_absolute():
        return candidate.resolve().relative_to(docs_dir.resolve()).as_posix()
    return candidate.as_posix().removeprefix("./")


def run_update(docs_dir: Path, lock_file: Path, pages: Sequence[str]) -> int:
    lock = load_lock(lock_file)
    by_rel = {meta.rel: meta for meta in iter_pages(docs_dir) if _eligible(meta)}
    lock = {page: entry for page, entry in lock.items() if page in by_rel}
    if pages:
        requested = [_normalize_page_arg(page, docs_dir) for page in pages]
        unknown = [rel for rel in requested if rel not in by_rel]
        if unknown:
            print("Unknown or ineligible page(s): " + ", ".join(unknown), file=sys.stderr)
            return 1
        targets = [by_rel[rel] for rel in requested]
    else:
        targets = list(by_rel.values())
    failures = 0
    for meta in targets:
        if not meta.sources:
            print(f"ERROR {meta.rel}: empty `sources` on a freshness-tracked page", file=sys.stderr)
            failures += 1
            continue
        hashes, missing = _collect_hashes(meta.sources)
        if missing:
            print(f"ERROR {meta.rel}: unresolvable sources: {', '.join(missing)}", file=sys.stderr)
            failures += 1
            continue
        fingerprint = page_fingerprint(hashes)
        _write_fingerprint(meta.path, fingerprint)
        lock[meta.rel] = LockEntry(fingerprint=fingerprint, symbols=hashes)
        print(f"updated {meta.rel}")
    save_lock(lock_file, lock)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, prog="docs_freshness.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("check", "report pages whose recorded API fingerprint is out of date"),
        ("update", "recompute and rewrite api_fingerprint for pages (after updating content)"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
        sub.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    subparsers.choices["check"].add_argument(
        "--json", action="store_true", dest="json_output", help="machine-readable output"
    )
    subparsers.choices["update"].add_argument(
        "pages", nargs="*", help="pages to update (relative to docs dir); default: all"
    )
    args = parser.parse_args(argv)
    docs_dir: Path = args.docs_dir
    lock_file: Path = args.lock_file
    if args.command == "check":
        return run_check(docs_dir, lock_file, json_output=args.json_output)
    return run_update(docs_dir, lock_file, args.pages)


if __name__ == "__main__":
    sys.exit(main())
