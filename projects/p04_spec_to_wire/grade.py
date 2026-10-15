"""Grade a model's answer with project 01's own codec and invariant checker.

No judgement anywhere in this file. A message packs or it does not. The invariants hold
or they name the rule that broke. A field matches the deterministic generator or it
differs, and the difference is printed. That is the whole reason this project is worth
having: the thing being measured cannot talk its way past the grader.

Partial credit is given where it is real and withheld where it is not. On ``fields``, a
model that gets 24 of 27 data elements right has done something useful and the score says
so; but *packs* and *invariants hold* stay separate pass/fail columns, because an
unparseable message is not 90% of a message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from projects.p01_switchboard import lifecycle, narrate, scenarios
from projects.p01_switchboard.codec import CodecError, Message, check_values, pack, unpack
from shared.llm import extract_json, first_block

from .tasks import DECODE, FIELDS, NEGATIVE, WIRE, Case


@dataclass
class Result:
    case: Case
    arm: str
    model: str
    #: The single number this case contributes, in 0..1. What it means is family
    #: specific and is spelled out in ``detail``.
    score: float = 0.0
    parsed: bool = False
    packs: bool = False
    invariants_hold: bool = False
    detail: str = ""
    columns: dict[str, object] = field(default_factory=dict)
    raw_reply: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "case": self.case.label,
            "family": self.case.family,
            "scenario": self.case.scenario,
            "rule": self.case.rule,
            "arm": self.arm,
            "model": self.model,
            "score": round(self.score, 4),
            "parsed": self.parsed,
            "packs": self.packs,
            "invariants_hold": self.invariants_hold,
            "detail": self.detail,
            **self.columns,
        }


def _message_from(blob: object) -> Message:
    if not isinstance(blob, dict):
        raise ValueError(f"expected an object, got {type(blob).__name__}")
    mti = blob.get("mti")
    if not isinstance(mti, str):
        raise ValueError("no 'mti' string")
    raw_fields = blob.get("fields")
    if not isinstance(raw_fields, dict):
        raise ValueError("no 'fields' object")
    values: dict[int, str] = {}
    for key, value in raw_fields.items():
        try:
            de = int(str(key).strip().lstrip("dDeE ").strip())
        except ValueError:
            raise ValueError(f"field key {key!r} is not a data element number") from None
        if value is None:
            continue
        values[de] = value if isinstance(value, str) else str(value)
    return Message(mti=mti, fields=values)


# ------------------------------------------------------------------ the families


def grade_decode(case: Case, reply: str, arm: str, model: str) -> Result:
    """Did it read the message correctly? Graded against the codec, field by field."""
    result = Result(case=case, arm=arm, model=model, raw_reply=reply)
    truth = scenarios.build(case.scenario, scenarios.Context()).steps[0].message
    try:
        blob = extract_json(reply)
    except ValueError as exc:
        result.detail = f"no JSON in the reply: {exc}"
        return result
    if not isinstance(blob, dict):
        result.detail = f"expected an object, got {type(blob).__name__}"
        return result
    result.parsed = True

    expected_amount = int(truth.fields.get(4, "0") or 0)
    expected = {
        "mti": truth.mti,
        "amount_minor_units": expected_amount,
        "currency": (truth.get(49) or "").lstrip("0"),
        "trace": (truth.get(11) or "").lstrip("0"),
    }
    got_amount = blob.get("amount_minor_units")
    try:
        got_amount = int(str(got_amount).replace(",", "").strip())
    except (TypeError, ValueError):
        got_amount = None
    got = {
        "mti": str(blob.get("mti", "")).strip(),
        "amount_minor_units": got_amount,
        "currency": str(blob.get("currency", "")).strip().lstrip("0"),
        "trace": str(blob.get("trace", "")).strip().lstrip("0"),
    }
    hits = {key: got[key] == expected[key] for key in expected}

    # The transaction description is the one free-text field, so it is scored by whether
    # the deterministic name's head word appears - not by similarity to a sentence.
    wanted = narrate.decode_processing_code(truth.get(3) or "000000")["transaction"]
    said = str(blob.get("transaction", "")).lower()
    key_word = wanted.split()[-1].lower()
    hits["transaction"] = key_word in said

    result.score = sum(hits.values()) / len(hits)
    result.columns = {
        "fields_right": sum(hits.values()),
        "fields_asked": len(hits),
        "wrong": [key for key, ok in hits.items() if not ok],
        "expected": expected | {"transaction": wanted},
        "got": got | {"transaction": said[:60]},
    }
    result.detail = (
        "all five read correctly"
        if all(hits.values())
        else "wrong: " + ", ".join(key for key, ok in hits.items() if not ok)
    )
    return result


def grade_fields(case: Case, reply: str, arm: str, model: str) -> Result:
    """Did it name the right data elements with the right contents?"""
    result = Result(case=case, arm=arm, model=model, raw_reply=reply)
    flow = scenarios.build(case.scenario, scenarios.Context())
    truth = flow.steps[0].message

    try:
        message = _message_from(extract_json(reply))
    except ValueError as exc:
        result.detail = f"unusable answer: {exc}"
        return result
    result.parsed = True

    problems = check_values(message.fields)
    try:
        raw = pack(message)
        unpack(raw)
        result.packs = True
    except CodecError as exc:
        result.detail = f"does not pack: {exc}"

    # Field agreement with the generator, over the union of what each side carries.
    present = set(truth.fields) | set(message.fields)
    same = {de for de in present if truth.get(de) == message.get(de)}
    missing = sorted(set(truth.fields) - set(message.fields))
    extra = sorted(set(message.fields) - set(truth.fields))
    differing = sorted(
        de for de in set(truth.fields) & set(message.fields) if truth.get(de) != message.get(de)
    )

    if result.packs:
        single = scenarios.Flow(
            case.scenario,
            case.scenario,
            "",
            [scenarios.Step(scenarios.ACQ_TO_ISS, "model", message, raw=raw)],
        )
        findings = [
            finding
            for finding in lifecycle.check(single)
            # L01 fires because a lone request has no response in this flow; that is the
            # harness's shape, not the model's mistake.
            if finding.rule != "L01"
        ]
        result.invariants_hold = not findings
        if findings:
            result.detail = f"{len(findings)} invariant finding(s): " + "; ".join(
                f.rule for f in findings
            )

    result.score = len(same) / len(present) if present else 0.0
    result.columns = {
        "elements_expected": len(truth.fields),
        "elements_given": len(message.fields),
        "elements_matching": len(same),
        "missing": missing,
        "extra": extra,
        "differing": differing,
        "value_problems": problems[:4],
    }
    if not result.detail:
        result.detail = (
            "exact"
            if not missing and not extra and not differing
            else f"{len(same)}/{len(present)} elements agree"
        )
    return result


def grade_wire(case: Case, reply: str, arm: str, model: str) -> Result:
    """Did it emit something the parser accepts? This is the strict one."""
    result = Result(case=case, arm=arm, model=model, raw_reply=reply)
    truth_raw = scenarios.build(case.scenario, scenarios.Context()).steps[0].raw
    candidate = first_block(reply).strip().splitlines()
    text = candidate[0].strip() if candidate else ""
    result.columns = {"characters": len(text), "expected_characters": len(truth_raw)}
    if not text:
        result.detail = "empty answer"
        return result
    result.parsed = True
    try:
        message = unpack(text)
        result.packs = True
    except CodecError as exc:
        result.detail = f"the parser rejected it: {exc}"
        result.columns["first_20"] = text[:20]
        return result

    same_mti = message.mti == truth_raw[:4]
    result.columns["mti"] = message.mti
    result.columns["elements"] = message.present
    result.score = 1.0 if text == truth_raw else (0.5 if same_mti else 0.25)
    result.invariants_hold = True
    result.detail = (
        "byte-identical to the generator"
        if text == truth_raw
        else f"parses, {len(message.present)} elements, MTI {message.mti}"
    )
    return result


def grade_negative(case: Case, reply: str, arm: str, model: str) -> Result:
    """Did the flow it produced break the rule it was asked to break, and only that one?"""
    result = Result(case=case, arm=arm, model=model, raw_reply=reply)
    try:
        blob = extract_json(reply)
        if not isinstance(blob, dict):
            raise ValueError("expected an object")
        request = _message_from(blob.get("request"))
        response = _message_from(blob.get("response"))
    except ValueError as exc:
        result.detail = f"unusable answer: {exc}"
        return result
    result.parsed = True

    try:
        request_raw, response_raw = pack(request), pack(response)
        unpack(request_raw)
        unpack(response_raw)
        result.packs = True
    except CodecError as exc:
        result.detail = f"does not pack: {exc}"
        return result

    flow = scenarios.Flow(
        "model",
        "model",
        "",
        [
            scenarios.Step(scenarios.ACQ_TO_ISS, "request", request, raw=request_raw),
            scenarios.Step(scenarios.ISS_TO_ACQ, "response", response, raw=response_raw),
        ],
    )
    fired = sorted({finding.rule for finding in lifecycle.check(flow)})
    wanted = case.rule
    hit = wanted in fired
    others = [rule for rule in fired if rule != wanted]

    # Full marks only for a clean test case: the named rule and nothing else. A flow that
    # breaks four rules does catch the one, but it is a poor test - it would also pass
    # against a switch that enforces only the other three.
    if hit and not others:
        result.score = 1.0
    elif hit:
        result.score = 0.5
    result.invariants_hold = not fired
    result.columns = {
        "asked_for": wanted,
        "rules_fired": fired,
        "collateral": others,
    }
    result.detail = (
        f"broke {wanted} and nothing else"
        if result.score == 1.0
        else (
            f"broke {wanted}, and also {', '.join(others)}"
            if hit
            else f"did not break {wanted}" + (f"; broke {', '.join(fired)}" if fired else "")
        )
    )
    return result


GRADERS = {
    DECODE: grade_decode,
    FIELDS: grade_fields,
    WIRE: grade_wire,
    NEGATIVE: grade_negative,
}


def grade(case: Case, reply: str, arm: str, model: str) -> Result:
    return GRADERS[case.family](case, reply, arm, model)


def baseline(case: Case) -> Result:
    """What project 01's deterministic generator scores on the same case.

    It is 1.0 by construction, and printed anyway: a model number with no baseline beside
    it invites the reader to supply their own idea of what good looks like.
    """
    result = Result(case=case, arm="generator", model="none (code)", score=1.0)
    result.parsed = result.packs = result.invariants_hold = True
    result.detail = "by construction"
    if case.family == NEGATIVE:
        # The generator's equivalent is the fault injector, which does exactly this.
        from projects.p01_switchboard import mutations

        intended = [m for m in mutations.FLOW_MUTATORS if m.expected == case.rule]
        result.detail = (
            f"the fault injector has {len(intended)} mutator(s) for {case.rule}"
            if intended
            else f"no hand-written mutator targets {case.rule}"
        )
    return result


def summarise(results: list[Result]) -> dict[str, object]:
    """Per family, per arm, per model - counts and means, with the denominators shown."""
    out: dict[str, object] = {}
    families = sorted({result.case.family for result in results})
    for family in families:
        rows = [result for result in results if result.case.family == family]
        out[family] = {
            "cases": len(rows),
            "mean_score": round(sum(row.score for row in rows) / len(rows), 4),
            "perfect": sum(1 for row in rows if row.score == 1.0),
            "parsed": sum(1 for row in rows if row.parsed),
            "packs": sum(1 for row in rows if row.packs),
            "invariants_hold": sum(1 for row in rows if row.invariants_hold),
        }
    return out


def to_json(results: list[Result]) -> str:
    return json.dumps([result.as_dict() for result in results], indent=2, ensure_ascii=False)
