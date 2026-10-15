"""The Switchboard web UI and its JSON API.

Five endpoints, each a thin wrapper over the engine. The interesting constraint is that
``/api/decode`` must be able to answer about a message it could not parse: a workbench
that says only "invalid" when handed real broken traffic is useless, so the response
carries the error text, how far the parser got, and whatever fields it read first.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.theme import attach_theme
from shared.webkit import App, BadRequest, Request, Response, run

from . import fields as F
from . import lifecycle, mutations, narrate, scenarios
from .codec import CodecError, Message, describe_mti, field_offsets, is_request, pack, unpack

STATIC = Path(__file__).with_name("static")
RESULTS = Path(__file__).with_name("results.json")

app = App("switchboard", static_dir=STATIC)
attach_theme(app)


# ------------------------------------------------------------------- endpoints


@app.route("GET", "/api/meta")
def meta(_request: Request) -> Response:
    return Response.json(
        {
            "scenarios": [
                {"key": s.key, "title": s.title, "description": s.description}
                for s in scenarios.SCENARIOS
            ],
            "rules": [
                {
                    "rule": r.rule,
                    "title": r.title,
                    "severity": r.severity,
                    "why": r.why,
                }
                for r in lifecycle.RULES
            ],
            "dictionary": [
                {"de": f.de, "name": f.name, "spec": f.spec, "var": f.var}
                for f in (F.get(de) for de in F.known())
            ],
            "currencies": {code: alpha for code, (alpha, _) in narrate.CURRENCIES.items()},
            "results": _results(),
        }
    )


@app.route("POST", "/api/decode")
def decode(request: Request) -> Response:
    payload = request.json()
    raw = str(payload.get("raw", "")).strip()
    if not raw:
        raise BadRequest("paste a raw message first")
    try:
        message = unpack(raw)
    except CodecError as exc:
        return Response.json(
            {
                "ok": False,
                "error": str(exc),
                "partial": _best_effort(raw),
                "length": len(raw),
            }
        )
    return Response.json({"ok": True, **_describe(message, raw)})


@app.route("POST", "/api/build")
def build(request: Request) -> Response:
    payload = request.json()
    key = str(payload.get("scenario", ""))
    if key not in scenarios.BY_KEY:
        raise BadRequest(f"unknown scenario {key!r}")
    context = scenarios.Context()
    for name in ("pan", "terminal", "currency", "account"):
        value = payload.get(name)
        if value:
            setattr(context, name, str(value))
    if payload.get("amount") not in (None, ""):
        try:
            context.amount = int(payload["amount"])
        except (TypeError, ValueError):
            raise BadRequest("amount must be a whole number of minor units") from None
        if context.amount < 0:
            raise BadRequest("amount must not be negative")
    try:
        flow = scenarios.build(key, context)
    except CodecError as exc:
        raise BadRequest(f"those values do not produce a valid message: {exc}") from None
    findings = lifecycle.check(flow)
    return Response.json(
        {
            "ok": True,
            "key": flow.key,
            "title": flow.title,
            "description": flow.description,
            "steps": [
                {
                    "direction": step.direction,
                    "label": step.label,
                    "note": step.note,
                    "raw": step.raw,
                    **_describe(step.message, step.raw),
                }
                for step in flow.steps
            ],
            "findings": [f.as_dict() for f in findings],
        }
    )


@app.route("POST", "/api/validate")
def validate(request: Request) -> Response:
    payload = request.json()
    lines = [line.strip() for line in str(payload.get("raws", "")).splitlines() if line.strip()]
    if not lines:
        raise BadRequest("paste one raw message per line")
    steps: list[scenarios.Step] = []
    parse_errors: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        try:
            message = unpack(line)
        except CodecError as exc:
            parse_errors.append({"line": index, "error": str(exc)})
            continue
        direction = scenarios.ACQ_TO_ISS if is_request(message.mti) else scenarios.ISS_TO_ACQ
        steps.append(scenarios.Step(direction, f"line {index}", message, raw=pack(message)))
    flow = scenarios.Flow("pasted", "Pasted flow", "Messages supplied by hand.", steps)
    findings = [] if not steps else [f.as_dict() for f in lifecycle.check(flow)]
    return Response.json(
        {
            "ok": not parse_errors,
            "parse_errors": parse_errors,
            "steps": [
                {
                    "direction": step.direction,
                    "label": step.label,
                    "mti": step.message.mti,
                    "narration": narrate.narrate(step.message),
                    "severity": narrate.severity(step.message),
                }
                for step in steps
            ],
            "findings": findings,
        }
    )


@app.route("GET", "/api/faults")
def faults(_request: Request) -> Response:
    return Response.json(_faults())


# --------------------------------------------------------------------- helpers


def _describe(message: Message, raw: str) -> dict[str, Any]:
    try:
        spans = field_offsets(raw) if raw else {}
    except CodecError:
        spans = {}
    return {
        "mti": message.mti,
        "mti_parts": describe_mti(message.mti),
        "is_request": is_request(message.mti),
        "narration": narrate.narrate(message),
        "severity": narrate.severity(message),
        "rows": [{**row, "span": spans.get(int(row["de"]))} for row in narrate.field_rows(message)],
        "raw": raw,
    }


def _best_effort(raw: str) -> dict[str, Any]:
    """What can still be said about a message that will not parse."""
    out: dict[str, Any] = {}
    head = raw[:4]
    try:
        out["mti"] = head
        out["mti_parts"] = describe_mti(head)
    except CodecError as exc:
        out["mti_error"] = str(exc)
    out["bitmap"] = raw[4:20]
    return out


@lru_cache(maxsize=1)
def _faults() -> dict[str, Any]:
    report = mutations.run()
    fuzz = mutations.fuzz()
    return {
        "mutation": report,
        "fuzz": fuzz,
        "catalogue": [
            {
                "name": m.name,
                "expected": m.expected,
                "description": m.description,
                "scenarios": list(m.scenarios),
            }
            for m in mutations.FLOW_MUTATORS
        ]
        + [
            {
                "name": m.name,
                "expected": "codec",
                "description": m.description,
                "scenarios": list(m.scenarios),
            }
            for m in mutations.CODEC_MUTATORS
        ],
    }


def _results() -> dict[str, Any] | None:
    if not RESULTS.is_file():
        return None
    try:
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main(port: int = 8101) -> None:
    run(app, port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the Switchboard workbench.")
    parser.add_argument("--port", type=int, default=8101)
    main(parser.parse_args().port)
