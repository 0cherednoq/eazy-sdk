"""Render an inventory as the Markdown page the model reads first."""

from __future__ import annotations

from typing import Any

_PLACES = (("path_params", "Path"), ("query", "Query"), ("body", "Body"), ("header", "Headers"))


def _origin(origin: dict[str, Any]) -> str:
    source = origin.get("from", "")
    if source == "set-cookie":
        return f"cookie `{origin['cookie']}` set by #{origin['exchange']}"
    if source == "location":
        where = f" query `{origin['query']}`" if "query" in origin else ""
        return f"`Location`{where} of #{origin['exchange']}"
    if source == "response header":
        return f"header `{origin['header']}` of #{origin['exchange']}"
    if source == "response body":
        if "json_pointer" in origin:
            return f"body of #{origin['exchange']} at `{origin['json_pointer']}`"
        hint = f", regex `{origin['regex']}`" if "regex" in origin else ""
        times = origin.get("occurrences", 1)
        many = f", {times} occurrences" if times > 1 else ""
        return f"body of #{origin['exchange']}{hint}{many}: `{origin.get('context', '')}`"
    if source == "web storage":
        return f"{origin['area']}Storage key `{origin['key']}` of {origin['origin']}"
    if source == "the client":
        return f"the client, first sent in #{origin['first_sent_in']}"
    if source == "unknown":
        return "unknown: in no earlier response"
    return str(source)


def _parameters(operation: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    origins = operation.get("value_origins", {})
    for place, title in _PLACES:
        items = operation.get(place)
        if not items:
            continue
        lines.append(
            f"- **{title}**"
            + (f" ({', '.join(operation['body_encoding'])})" if place == "body" else "")
        )
        for item in items:
            values = ", ".join(f"`{value}`" for value in item["values"])
            extra = f" (+{item['more_values']} more)" if item.get("more_values") else ""
            hint = f" — {item['hint']} hint" if item.get("hint") else ""
            looks = "/".join(item["looks_like"])
            kind = (
                item["behaviour"] if looks == item["behaviour"] else f"{item['behaviour']}, {looks}"
            )
            lines.append(
                f"  - `{item['name']}`: {kind}, in {item['samples']} samples{hint}: {values}{extra}"
            )
            origin = origins.get(f"{place}:{item['name']}")
            if origin:
                lines.append(f"    - comes from {_origin(origin)}")
    return lines


def _schema_lines(schema: dict[str, Any], indent: int, budget: list[int]) -> list[str]:
    lines: list[str] = []
    pad = "  " * indent
    for name, item in schema.get("properties", {}).items():
        if budget[0] <= 0:
            lines.append(f"{pad}- …")
            return lines
        budget[0] -= 1
        kind = "|".join(item["type"])
        if "format" in item:
            kind += f" ({'/'.join(item['format'])})"
        if "length" in item:
            low, high = item["length"]
            kind += f" [{low}]" if low == high else f" [{low}..{high}]"
        optional = ", optional" if item.get("optional") else ""
        lines.append(f"{pad}- `{name}`: {kind}{optional}")
        nested = item.get("items", item) if "array" in item["type"] else item
        if nested.get("properties"):
            lines += _schema_lines(nested, indent + 1, budget)
    return lines


def _responses(operation: dict[str, Any]) -> list[str]:
    lines = ["- **Responses**"]
    for variant in operation["responses"]:
        head = f"  - {variant['status']} `{variant['media_type'] or 'no content type'}`"
        head += f", exchanges {variant['exchanges']}, {max(variant['body_bytes'])} bytes"
        if variant.get("lossy"):
            head += ", LOSSY TEXT"
        lines.append(head)
        if "location" in variant:
            lines.append(f"    - Location: `{variant['location']}`")
            lines += [f"      - e.g. `{example}`" for example in variant["location_examples"]]
        if "body_marks" in variant:
            lines.append(f"    - body marks: `{variant['body_marks']}`")
        if "sets_cookies" in variant:
            lines.append(f"    - sets cookies: {', '.join(variant['sets_cookies'])}")
        html = variant.get("html")
        if html:
            if "title" in html:
                lines.append(f"    - title: `{html['title']}`")
            if "meta_refresh" in html:
                lines.append(f"    - meta refresh to `{html['meta_refresh']}`")
            for form in html.get("forms", []):
                names = ", ".join(f"{f['name']}:{f['type']}" for f in form["fields"])
                lines.append(f"    - form {form['method']} `{form['action']}`: {names}")
            if html.get("protection_markers"):
                lines.append("    - anti-bot or captcha markers in the page")
        schema = variant.get("json_schema")
        if schema:
            top = "|".join(schema["type"])
            lines.append(f"    - JSON {top}:")
            source = schema.get("items", schema) if "array" in schema["type"] else schema
            lines += _schema_lines(source, 3, [250])
    return lines


def render(inventory: dict[str, Any]) -> str:
    """Render the whole inventory."""
    out: list[str] = ["# Capture inventory", ""]
    source = inventory["source"]
    out.append(f"Source: {source['kind']}, {source['exchanges']} exchanges.")
    out.append("")
    if inventory["warnings"]:
        out.append("## Warnings")
        out += [f"- {warning}" for warning in inventory["warnings"]] + [""]
    out.append("## Hosts")
    out += [f"- `{host['host']}`: {host['exchanges']} exchanges" for host in inventory["hosts"]]
    out.append("")
    out.append("## Scenarios")
    for scenario in inventory["scenarios"]:
        out.append(f"- **{scenario['name']}**: exchanges {scenario['exchanges']}")
        out += [f"  - {action}" for action in scenario["actions"][:40]]
    out.append("")
    if inventory["redirect_chains"]:
        out.append("## Redirect chains")
        for chain in inventory["redirect_chains"]:
            steps = " → ".join(
                f"#{step['exchange']} {step['status']} `{step['url']}`"
                + (f" ({step['then']})" if "then" in step else "")
                for step in chain
            )
            out.append(f"- {steps}")
        out.append("")
    out.append("## Cookies")
    for cookie in inventory["cookies"]:
        line = f"- `{cookie['name']}`: set by {cookie['set_by'] or 'no response'}"
        line += f", domain {', '.join(cookie['domains']) or '?'}"
        line += f", sent to {', '.join(cookie['sent_to']) or 'nobody'}"
        if cookie.get("note"):
            line += f" — {cookie['note']}"
        out.append(line)
    out.append("")
    if inventory["envelopes"]:
        out.append("## Envelope candidates")
        for envelope in inventory["envelopes"]:
            out.append(
                f"- `{envelope['host']}`: keys {envelope['keys']}, marks {envelope['marks']} "
                f"({envelope['confidence']})"
            )
        out.append("")
    out.append("## Operations")
    for operation in inventory["operations"]:
        out.append(f"### {operation['id']}")
        out.append(f"- exchanges {operation['exchanges']}")
        if operation.get("caused_by"):
            out.append(f"- caused by: {'; '.join(operation['caused_by'])}")
        out += _parameters(operation)
        if operation.get("body_order"):
            out.append(f"- body field order: {', '.join(operation['body_order'])}")
        for names in operation.get("same_value", []):
            out.append(f"- same value under several names: {', '.join(names)}")
        if operation.get("cookies_sent"):
            out.append(f"- cookies sent: {', '.join(operation['cookies_sent'])}")
        if operation.get("pagination_hint"):
            hint = operation["pagination_hint"]
            out.append(f"- pagination hint: request {hint['request']}, response {hint['response']}")
        out += _responses(operation)
        out.append("")
    if inventory["noise"]:
        out.append("## Left out as noise")
        out += [f"- {reason}: exchanges {seqs}" for reason, seqs in inventory["noise"].items()]
        out.append("")
    out.append("## Out of scope signals")
    out += [f"- {signal}" for signal in inventory["out_of_scope"]] or ["- none"]
    out.append("")
    return "\n".join(out)
