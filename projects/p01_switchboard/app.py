"""The Switchboard web UI and its JSON API.

Five endpoints, each a thin wrapper over the engine. The interesting constraint is that
``/api/decode`` must be able to answer about a message it could not parse: a workbench
that says only "invalid" when handed real broken traffic is useless, so the response
carries the error text, how far the parser got, and whatever fields it read first.
"""

from __future__ import annotations

import json
import pathlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.datapage import Block, Figure, dig, figures_from  # noqa: F401
from shared.datapage import Page as DataPage
from shared.datapage import attach as attach_datasets
from shared.iopage import Page, Row, Section, attach_io
from shared.theme import attach_theme
from shared.webkit import App, BadRequest, Request, Response, attach_metrics, run

from . import binary, corpus, emv, lifecycle, mutations, narrate, scenarios
from . import fields as F
from .codec import CodecError, Message, describe_mti, field_offsets, is_request, pack, unpack

STATIC = Path(__file__).with_name("static")
RESULTS = Path(__file__).with_name("results.json")

app = App("switchboard", static_dir=STATIC)
attach_theme(app)
attach_metrics(app)

SOURCE = "https://github.com/hammasbuilds/fintech-lab/tree/main/projects/p01_switchboard"


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
    raw = str(payload.get("raw", ""))
    if not raw.strip():
        raise BadRequest("paste a raw message first")
    wanted = str(payload.get("framing", "auto"))
    if wanted not in {"auto", binary.ASCII, binary.BINARY, binary.BINARY_NO_HEADER}:
        raise BadRequest(f"unknown framing {wanted!r}")

    detection = binary.detect(raw)
    try:
        if wanted == "auto":
            message, framing = binary.parse_any(raw)
        elif wanted == binary.ASCII:
            message, framing = unpack(raw), binary.ASCII
        else:
            data = bytes.fromhex("".join(raw.split()))
            message = binary.unpack_binary(data, binary.Framing(header=wanted == binary.BINARY))
            framing = wanted
    except CodecError as exc:
        return Response.json(
            {
                "ok": False,
                "error": str(exc),
                "framing": None,
                "detection": detection,
                "partial": _best_effort(raw),
                "length": len(raw),
            }
        )
    except ValueError as exc:
        return Response.json(
            {
                "ok": False,
                "error": f"that is not hexadecimal, so it cannot be a binary dump: {exc}",
                "framing": None,
                "detection": detection,
                "partial": _best_effort(raw),
                "length": len(raw),
            }
        )
    # Field offsets only mean anything against the ASCII form; for a binary dump the
    # message is re-serialised to ASCII so the highlighting is still truthful about which
    # characters belong to which element.
    display = raw if framing == binary.ASCII else pack(message)
    return Response.json(
        {
            "ok": True,
            "framing": framing,
            "detection": detection,
            "re_serialised": framing != binary.ASCII,
            **_describe(message, display),
        }
    )


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
    chip = message.get(55)
    chip_tags: list[dict[str, object]] = []
    chip_problems: list[str] = []
    if chip is not None:
        try:
            chip_tags = [tag.as_dict() for tag in emv.parse(chip)]
        except emv.EmvError as exc:
            chip_problems.append(str(exc))
        else:
            for problem in (
                emv.amount_mismatch(chip, message.get(4)),
                emv.currency_mismatch(chip, message.get(49)),
            ):
                if problem:
                    chip_problems.append(problem)
            missing = emv.missing_tags(chip)
            if missing:
                chip_problems.append(f"mandatory tag(s) missing: {', '.join(missing)}")
    return {
        "chip": {"tags": chip_tags, "problems": chip_problems},
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


# ---------------------------------------------------------------------------- datasets
# What this project is measured on at full size, and what came out. The figures are read
# out of results_datasets.json and data/MANIFEST.json rather than typed, so this page
# cannot drift from the run that produced it.
DATASETS_RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"


def _at_scale() -> dict:
    if DATASETS_RESULTS.is_file():
        try:
            return json.loads(DATASETS_RESULTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def datasets_page() -> DataPage:
    data = _at_scale()
    blocks = [
        Block(
            title="Codec, every message in the corpus",
            subtitle="Packed, re-parsed, packed again, in both framings. No sampling.",
            figures=figures_from(
                data,
                [
                    ("messages checked", "codec.messages_checked", "the whole corpus"),
                    (
                        "ASCII round trip stable",
                        "codec.ascii_round_trip_stable",
                        "messages checked",
                    ),
                    (
                        "binary round trip stable",
                        "codec.binary_round_trip_stable",
                        "messages checked",
                    ),
                    ("failed to pack", "codec.messages_that_failed_to_pack", "messages checked"),
                    (
                        "character class violations",
                        "codec.character_class_violations",
                        "messages checked",
                    ),
                ],
            ),
            reading=str(dig(data, "codec.reading", "")),
        ),
        Block(
            title="Invariants, every lifecycle",
            subtitle="All of them are valid by construction, so a finding is a false positive.",
            figures=figures_from(
                data,
                [
                    ("rules", "invariants.rules", ""),
                    ("lifecycles checked", "invariants.flows_checked", "the whole corpus"),
                    (
                        "lifecycles with findings",
                        "invariants.flows_with_findings",
                        "lifecycles checked",
                    ),
                    ("false positive rate", "invariants.false_positive_rate", "lifecycles checked"),
                ],
            ),
            reading=str(dig(data, "invariants.reading", "")),
        ),
        Block(
            title="Fuzz, widened",
            subtitle=(
                "Single-character and single-byte damage, over a pool built from 50 "
                "random contexts."
            ),
            figures=figures_from(
                data,
                [
                    ("ASCII trials", "fuzz_ascii.trials", ""),
                    ("ASCII rejection rate", "fuzz_ascii.rejection_rate", "ASCII trials"),
                    (
                        "illegal characters rejected",
                        "fuzz_ascii.groups.class_violating.rejection_rate",
                        "class-violating trials",
                    ),
                    (
                        "framing damage rejected",
                        "fuzz_ascii.groups.framing.rejection_rate",
                        "framing trials",
                    ),
                    ("binary trials", "fuzz_binary.trials", ""),
                    ("binary rejection rate", "fuzz_binary.rejection_rate", "binary trials"),
                ],
            ),
            reading=(
                "The class-legal group is 0.000 by design: a legal character in a free-text "
                "field makes an unusual message, not an invalid one, and a parser that "
                "rejected it would be wrong."
            ),
        ),
        Block(
            title="Mutation, at every amount and currency",
            subtitle=(
                "The 38 curated faults, re-injected into flows built from 200 random contexts."
            ),
            figures=figures_from(
                data,
                [
                    ("mutants", "mutation.mutants", "200 contexts"),
                    ("killed", "mutation.killed", "mutants"),
                    ("kill rate", "mutation.kill_rate", "mutants"),
                    ("killed by the intended rule", "mutation.by_intended_rate", "mutants"),
                ],
            ),
            reading=str(dig(data, "mutation.reading", "")),
        ),
    ]
    if not data:
        blocks = [
            Block(
                title="Not run on this machine",
                subtitle=(
                    "The at-scale figures come from results_datasets.json, which is "
                    "committed. If this page is empty the file is missing: run make data "
                    "and then make bench-datasets."
                ),
            )
        ]
    return DataPage(
        title="01 Switchboard",
        subtitle="The generated ISO 8583 corpus, and the whole-corpus run",
        dataset_keys=[corpus.KEY],
        blocks=blocks,
        links=[("Dashboard", "/"), ("Input and output", "/io"), ("Source", SOURCE)],
    )


attach_datasets(app, datasets_page)


def main(port: int = 8101) -> None:
    run(app, port=port)


# ------------------------------------------------------------- input / output


def io_page() -> Page:
    """The proof, built by running the engine, served at ``/io``."""
    from . import binary, emv, mutations
    from .codec import check_values

    flow = scenarios.build("atm_partial_approval")
    request_message, response_message = flow.steps[0].message, flow.steps[1].message
    results = _results() or {}

    lines = []
    for index, step in enumerate(flow.steps, start=1):
        lines.append(f"step {index}  {step.direction}  ({step.label})")
        lines.append(f"  raw     {step.raw}")
        lines.append(f"  reads   {narrate.narrate(step.message)}")
        lines.append("")
    generated = Section(
        title="Generate a transaction lifecycle, then read it back in English",
        note="A partial approval: the one response allowed to carry a different amount.",
        given="scenario 'atm_partial_approval', amount 10,000.00 PKR",
        output="\n".join(lines).rstrip(),
        rows=[
            Row(
                "requested",
                narrate.format_amount(request_message.fields[4], request_message.get(49)),
            ),
            Row(
                "approved",
                narrate.format_amount(response_message.fields[4], response_message.get(49)),
            ),
            Row("response code", f"{response_message.fields[39]} - partial approval"),
        ],
    )

    data = binary.pack_binary(request_message)
    verdict = binary.detect(data.hex())
    reparsed, _ = binary.parse_any(data.hex())
    framing = Section(
        title="The same message as a live switch would send it",
        note="Packed bitmap, BCD numeric fields, a 2-byte length header. The framing is "
        "worked out by trying each candidate against the parser.",
        given=data[:40].hex().upper() + "...",
        rows=[
            Row("binary, with a length header", str(len(data)), "bytes"),
            Row("the same thing in ASCII", str(len(flow.steps[0].raw)), "characters"),
            Row("framing detected as", str(verdict["framing"])),
            Row(
                "fields identical to the ASCII form",
                "yes" if reparsed.fields == request_message.fields else "NO",
            ),
        ],
        reading=" ".join(str(reason) for reason in verdict["reasons"]),
    )

    tampered = request_message.replace(de4=str(int(request_message.fields[4]) * 2).zfill(12))
    chip = Section(
        title="DE 4 doubled, with the chip's own 9F02 left alone",
        note="Most decoders print DE 55 as one hex blob. Tag 9F02 is the amount the chip "
        "authorised, and it has to equal DE 4.",
        given="the approved withdrawal, with DE 4 multiplied by two",
        rows=[
            Row(
                "every field still well-formed",
                "yes" if check_values(tampered.fields) == [] else "no",
            ),
            Row(
                "the message still parses",
                "yes" if unpack(pack(tampered)).fields == tampered.fields else "no",
            ),
            Row("EMV tags in DE 55", str(len(emv.parse(request_message.fields[55])))),
        ],
        reading=str(emv.amount_mismatch(tampered.fields[55], tampered.fields[4])),
    )

    mutation = results.get("mutation", {})
    fuzz = results.get("fuzz", {}).get("groups", {})
    illegal = fuzz.get("class_violating", {})
    framing_damage = fuzz.get("framing", {})
    binary_fuzz = results.get("fuzz_binary", {})
    graded = Section(
        title="Break the valid traffic on purpose and count what is rejected",
        note="A decoder and a rule list both look convincing while they are only ever "
        "shown correct traffic.",
        given="all 14 scenarios, then every injected fault",
        rows=[
            Row(
                "valid flows checked",
                str(results.get("invariants", {}).get("valid_flows_checked", "-")),
            ),
            Row(
                "false positives on them",
                str(results.get("invariants", {}).get("valid_flows_with_findings", "-")),
            ),
            Row("faults injected", str(mutation.get("mutants", "-"))),
            Row(
                "killed by the intended rule",
                f"{mutation.get('killed_by_intended_rule', '-')}/{mutation.get('mutants', '-')}",
            ),
            Row(
                "random illegal characters rejected",
                f"{illegal.get('rejected', '-')}/{illegal.get('trials', '-')}",
            ),
            Row(
                "random framing damage rejected",
                f"{framing_damage.get('rejected', '-')}/{framing_damage.get('trials', '-')}",
            ),
            Row(
                "random byte damage, binary",
                f"{binary_fuzz.get('rejected', '-')}/{binary_fuzz.get('trials', '-')}",
            ),
        ],
        reading=str(results.get("fuzz", {}).get("note", "")),
    )
    del mutations

    return Page(
        title="Switchboard",
        subtitle="ISO 8583 workbench - input and output",
        sections=[generated, framing, chip, graded],
        links=[
            ("Open the dashboard", "/"),
            (
                "Source on GitHub",
                SOURCE,
            ),
            ("All three projects", "https://hammasbuilds.github.io/fintech-lab/"),
        ],
    )


attach_io(app, io_page)

# The __main__ block stays last on purpose. Anything registered after it never
# runs when this module is executed as a program, because main() blocks in the server
# loop and never returns - so a route added below here would exist on import and be
# missing when served, which is the one mode that counts.
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the Switchboard workbench.")
    parser.add_argument("--port", type=int, default=8101)
    main(parser.parse_args().port)
