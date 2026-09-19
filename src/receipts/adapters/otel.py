"""OpenTelemetry GenAI spans -> ledger.

Input is OTLP/JSON (`{"resourceSpans": [...]}`, as `otel-cli`, the collector's file exporter and
every OTLP backend emit) or a JSONL stream of individual spans. The GenAI semantic conventions
make the interesting content *opt-in*: `gen_ai.operation.name=execute_tool` spans carry the tool
name and call id, but `gen_ai.tool.call.arguments` / `.result` and the message events only exist
when the instrumentation sets `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. A span with
no captured result is therefore recorded with `flags.stderr_dropped`, which makes its claims
`unrecorded` -- the record is known-incomplete -- rather than `unwitnessed`.

Span status maps to outcome: `STATUS_CODE_ERROR` is positive evidence of failure, `UNSET` is not
evidence of success, so only an explicit OK (or a captured exit code) yields exit_code 0.

Owner: Ananya.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..ledger import chain, redact
from ..models import EventFlags, EventKind, LedgerEvent, Session
from ..parsers import is_piped
from .state import Builder, as_dict, as_list, as_text

_TOOL_ALIASES = {
    "bash": "Bash",
    "shell": "Bash",
    "run_command": "Bash",
    "terminal": "Bash",
    "edit": "Edit",
    "write": "Edit",
    "apply_patch": "Edit",
    "str_replace_editor": "Edit",
    "read": "Read",
    "view": "Read",
    "web_search": "WebSearch",
}


def _attributes(raw: object) -> dict[str, Any]:
    """OTLP attributes are [{key, value:{stringValue|intValue|...}}]; plain dicts also accepted."""
    if isinstance(raw, dict):
        return dict(raw)
    out: dict[str, Any] = {}
    for item in as_list(raw):
        attr = as_dict(item)
        key = str(attr.get("key", ""))
        value = attr.get("value")
        if not key:
            continue
        if isinstance(value, dict):
            for field in ("stringValue", "intValue", "doubleValue", "boolValue"):
                if field in value:
                    out[key] = int(value[field]) if field == "intValue" else value[field]
                    break
            else:
                out[key] = as_text(value.get("arrayValue") or value.get("kvlistValue") or value)
        else:
            out[key] = value
    return out


def _nano_ts(value: object, fallback: datetime) -> datetime:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, int | float) and value:
        return datetime.fromtimestamp(float(value) / 1e9, tz=UTC)
    return fallback


def _spans(payload: object) -> list[dict[str, Any]]:
    """Flatten OTLP resourceSpans/scopeSpans, or accept a bare span list."""
    if isinstance(payload, list):
        return [as_dict(s) for s in payload]
    root = as_dict(payload)
    if "resourceSpans" not in root:
        return [root] if root.get("spanId") or root.get("name") else []
    out: list[dict[str, Any]] = []
    for resource in as_list(root.get("resourceSpans")):
        res = as_dict(resource)
        res_attrs = _attributes(as_dict(res.get("resource")).get("attributes"))
        for scope in as_list(res.get("scopeSpans")):
            for span in as_list(as_dict(scope).get("spans")):
                s = as_dict(span)
                s["_resource"] = res_attrs
                out.append(s)
    return out


def _tool(attrs: dict[str, Any], span_name: str) -> str:
    name = str(attrs.get("gen_ai.tool.name") or attrs.get("tool.name") or span_name.split(" ")[-1])
    return _TOOL_ALIASES.get(name.lower(), name or "Tool")


def parse(path: str) -> tuple[Session, list[LedgerEvent], str | None]:
    with open(path, encoding="utf-8") as fh:
        text = fh.read().strip()
    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError:
        payload = [json.loads(line) for line in text.splitlines() if line.strip()]
    spans = sorted(_spans(payload), key=lambda s: int(s.get("startTimeUnixNano") or 0))
    if not spans:
        raise ValueError(f"{path}: no spans found")

    first = _attributes(spans[0].get("attributes")) | as_dict(spans[0].get("_resource"))
    session_id = str(
        first.get("gen_ai.conversation.id")
        or first.get("session.id")
        or spans[0].get("traceId")
        or "otel-unknown"
    )
    epoch = datetime.fromtimestamp(0, tz=UTC)
    started = _nano_ts(spans[0].get("startTimeUnixNano"), epoch)
    cwd = str(first.get("process.working_directory") or "") or None

    b = Builder(session_id, cwd)
    report: str | None = None
    ended = started
    model: str | None = None

    for span in spans:
        attrs = _attributes(span.get("attributes")) | as_dict(span.get("_resource"))
        start = _nano_ts(span.get("startTimeUnixNano"), started)
        end = _nano_ts(span.get("endTimeUnixNano"), start)
        ended = max(ended, end)
        model = model or (
            str(attrs.get("gen_ai.request.model")) if attrs.get("gen_ai.request.model") else None
        )
        operation = str(attrs.get("gen_ai.operation.name") or span.get("name") or "")
        status = as_dict(span.get("status"))
        failed = str(status.get("code", "")).endswith("ERROR")

        if operation != "execute_tool" and not str(attrs.get("gen_ai.tool.name") or ""):
            # inference spans carry the model's own words: report material, never evidence
            output = as_text(attrs.get("gen_ai.output.messages") or attrs.get("gen_ai.completion"))
            if output:
                b.store_output(b.add(kind=EventKind.TEXT, ts=end), output)
                report = output
            continue

        tool = _tool(attrs, str(span.get("name", "")))
        arguments = attrs.get("gen_ai.tool.call.arguments")
        command = str(
            as_dict(arguments).get("command") if isinstance(arguments, dict) else arguments or ""
        )
        piped = bool(command) and is_piped(command)
        call_input = redact({"command": command} if command else {"arguments": as_text(arguments)})
        b.add(
            kind=EventKind.CALL,
            ts=start,
            tool=tool,
            input=call_input,
            flags=EventFlags(piped=piped, sidechain=bool(attrs.get("gen_ai.agent.id"))),
        )

        result = attrs.get("gen_ai.tool.call.result")
        exit_raw = attrs.get("process.exit_code")
        exit_code = (
            int(exit_raw)
            if isinstance(exit_raw, int)
            else (1 if failed else 0 if str(status.get("code", "")).endswith("OK") else None)
        )
        event = b.add(
            kind=EventKind.RESULT,
            ts=end,
            tool=tool,
            input=call_input,
            exit_code=exit_code,
            duration_ms=int((end - start).total_seconds() * 1000),
            flags=EventFlags(
                piped=piped,
                error=failed,
                # content capture is opt-in in the convention; say so rather than guess
                stderr_dropped=result is None,
            ),
        )
        if result is not None:
            b.store_output(event, as_text(result))

    events = chain(b.events)
    captured = sum(1 for e in events if e.kind is EventKind.RESULT and not e.flags.stderr_dropped)
    results = sum(1 for e in events if e.kind is EventKind.RESULT)
    meta = Session(
        id=session_id,
        source="otel",
        agent=str(first.get("gen_ai.agent.name") or first.get("service.name") or "otel"),
        model=model,
        started=started,
        ended=ended,
        cwd=cwd,
        n_events=len(events),
        ledger_root_hash=events[-1].hash if events else "",
        # completeness is the known weak point of trace-based evidence: score it, don't assume it
        integrity_score=round(captured / results, 2) if results else 0.5,
    )
    return meta, events, report
