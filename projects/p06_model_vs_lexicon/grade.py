"""Score the model and the rules on the same cases, with the same notion of correct.

The rule-based column is not an estimate: it is project 03's own layers run over the same
corpus here, so the two numbers in any row were produced by the same grader on the same
input. The only thing that differs is what did the answering.

One asymmetry is deliberate and is reported rather than smoothed over: an answer the model
does not return in usable JSON is scored wrong, not skipped. A model that cannot be parsed
cannot drive a transfer, and dropping those cases would be grading the subset it happened
to format correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from projects.p03_guardian import coercion, nlu, numerals
from shared.llm import extract_json

from .tasks import ACTION, AMOUNT, COERCION, Case


@dataclass
class Row:
    case: Case
    arm: str
    model: str
    correct: bool = False
    parsed: bool = False
    detail: str = ""
    got: object = None
    columns: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "case": self.case.label,
            "family": self.case.family,
            "text": self.case.text,
            "note": self.case.note,
            "arm": self.arm,
            "model": self.model,
            "correct": self.correct,
            "parsed": self.parsed,
            "expected": self.case.expected,
            "got": self.got,
            "detail": self.detail,
            **self.columns,
        }


# --------------------------------------------------------------------- amounts


def grade_amount(case: Case, reply: str, arm: str, model: str) -> Row:
    row = Row(case=case, arm=arm, model=model)
    try:
        blob = extract_json(reply)
    except ValueError as exc:
        row.detail = f"no JSON: {exc}"
        return row
    if not isinstance(blob, dict):
        row.detail = f"expected an object, got {type(blob).__name__}"
        return row
    row.parsed = True
    value = blob.get("amount")
    if isinstance(value, str):
        try:
            value = float(value.replace(",", "").strip())
        except ValueError:
            value = None
    row.got = value
    if value is None:
        row.detail = "no amount given"
        return row
    try:
        row.correct = float(value) == float(case.expected)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        row.detail = f"unusable amount {value!r}"
        return row
    if not row.correct:
        try:
            ratio = float(value) / float(case.expected)  # type: ignore[arg-type]
            row.columns["factor_out"] = round(ratio, 3)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        row.detail = f"said {value}, the amount is {case.expected}"
    return row


def rules_amount(case: Case) -> Row:
    row = Row(case=case, arm="rules", model="none (code)", parsed=True)
    found = numerals.find_all(case.text)
    row.got = found[0].value if len(found) == 1 else None
    row.correct = row.got == case.expected
    row.detail = "" if row.correct else f"the parser said {row.got}"
    return row


# --------------------------------------------------------------------- actions


def _normalise_action(blob: dict) -> dict[str, object]:
    """Reduce a model answer to the shape project 03's ``action()`` produces."""
    ask = blob.get("ask")
    if isinstance(ask, str) and ask.strip() and ask.strip().lower() not in {"null", "none"}:
        return {"ask": ask.strip()}
    out: dict[str, object] = {}
    intent = blob.get("intent")
    if isinstance(intent, str) and intent.strip():
        out["intent"] = intent.strip()
    amount = blob.get("amount")
    if isinstance(amount, str):
        try:
            amount = float(amount.replace(",", "").strip())
        except ValueError:
            amount = None
    if isinstance(amount, (int, float)):
        out["amount"] = float(amount)
    beneficiary = blob.get("beneficiary")
    if isinstance(beneficiary, str) and beneficiary.strip().lower() not in {"", "null", "none"}:
        out["beneficiary"] = beneficiary.strip()
    number = blob.get("target_number")
    if isinstance(number, str) and number.strip().lower() not in {"", "null", "none"}:
        out["target_number"] = number.strip().replace("-", "").replace(" ", "")
    biller = blob.get("biller")
    if isinstance(biller, str) and biller.strip().lower() not in {"", "null", "none"}:
        out["biller"] = biller.strip()
    return out


def _compare_action(expected: object, got: dict[str, object]) -> tuple[bool, str]:
    if isinstance(expected, dict) and "ask" in expected:
        if "ask" in got:
            return True, "asked, as it should"
        return False, f"resolved it instead of asking ({got})"
    if "ask" in got:
        return False, f"asked when the action was determined: {got['ask']!r}"
    assert isinstance(expected, dict)

    wrong: list[str] = []
    if got.get("intent") != expected.get("intent"):
        wrong.append(f"intent {got.get('intent')!r} not {expected.get('intent')!r}")
    if "amount" in expected:
        # Two statements in this branch on purpose. Collapsing it to
        # ``if "amount" in expected and <mismatch>`` re-binds the elif below to the
        # *combined* condition, so a correct amount falls through to "gave an amount
        # where there is none" - which is what a lint fix did here, and it turned 14 of
        # 30 correct answers into failures until the baseline test caught it.
        mismatch = "amount" not in got or float(got["amount"]) != float(
            expected["amount"]  # type: ignore[arg-type]
        )
        if mismatch:
            wrong.append(f"amount {got.get('amount')} not {expected['amount']}")
    elif "amount" in got:
        wrong.append("gave an amount where there is none")
    if (
        "beneficiary" in expected
        and str(got.get("beneficiary", "")).strip() != expected["beneficiary"]
    ):
        wrong.append(f"beneficiary {got.get('beneficiary')!r} not {expected['beneficiary']!r}")
    if "target_number" in expected and got.get("target_number") != expected["target_number"]:
        wrong.append(f"number {got.get('target_number')!r} not {expected['target_number']!r}")
    if "biller" in expected and got.get("biller") != expected["biller"]:
        wrong.append(f"biller {got.get('biller')!r} not {expected['biller']!r}")
    return (not wrong), ("exact" if not wrong else "; ".join(wrong))


def grade_action(case: Case, reply: str, arm: str, model: str) -> Row:
    row = Row(case=case, arm=arm, model=model)
    try:
        blob = extract_json(reply)
    except ValueError as exc:
        row.detail = f"no JSON: {exc}"
        return row
    if not isinstance(blob, dict):
        row.detail = f"expected an object, got {type(blob).__name__}"
        return row
    row.parsed = True
    got = _normalise_action(blob)
    row.got = got
    row.correct, row.detail = _compare_action(case.expected, got)
    row.columns["asked"] = "ask" in got
    return row


def rules_action(case: Case) -> Row:
    row = Row(case=case, arm="rules", model="none (code)", parsed=True)
    utterance = nlu.parse(case.text)
    action = utterance.action()
    got = (
        action if action is not None else {"ask": utterance.issues[0] if utterance.issues else "?"}
    )
    row.got = got
    row.correct, row.detail = _compare_action(case.expected, dict(got))
    row.columns["asked"] = "ask" in got
    return row


# -------------------------------------------------------------------- coercion


def grade_coercion(case: Case, reply: str, arm: str, model: str) -> Row:
    row = Row(case=case, arm=arm, model=model)
    try:
        blob = extract_json(reply)
    except ValueError as exc:
        row.detail = f"no JSON: {exc}"
        return row
    if not isinstance(blob, dict):
        row.detail = f"expected an object, got {type(blob).__name__}"
        return row
    row.parsed = True
    verdict = str(blob.get("verdict", "")).strip().lower()
    row.got = verdict
    row.correct = verdict == case.expected
    row.columns["why"] = str(blob.get("why", ""))[:80]
    row.columns["blocked"] = verdict == "block"
    row.columns["lexicon_misses_this"] = bool(case.note)
    if not row.correct:
        row.detail = f"said {verdict!r}, expected {case.expected!r}"
    return row


def rules_coercion(case: Case) -> Row:
    row = Row(case=case, arm="rules", model="none (code)", parsed=True)
    assessment = coercion.assess(case.text)
    row.got = assessment.verdict
    row.correct = assessment.verdict == case.expected
    row.columns["blocked"] = assessment.verdict == coercion.BLOCK
    row.columns["lexicon_misses_this"] = bool(case.note)
    row.columns["why"] = ", ".join(assessment.families) or "nothing matched"
    if not row.correct:
        row.detail = f"said {assessment.verdict!r}, expected {case.expected!r}"
    return row


GRADERS = {AMOUNT: grade_amount, ACTION: grade_action, COERCION: grade_coercion}
RULES = {AMOUNT: rules_amount, ACTION: rules_action, COERCION: rules_coercion}


def grade(case: Case, reply: str, arm: str, model: str, aborted: str = "") -> Row:
    """Grade one answer. An abort scores wrong, the same as an unusable one."""
    if aborted:
        row = Row(case=case, arm=arm, model=model)
        row.detail = f"no answer: {aborted}"
        row.columns["aborted"] = aborted
        return row
    return GRADERS[case.family](case, reply, arm, model)


def rules(case: Case) -> Row:
    return RULES[case.family](case)


def summarise(rows: list[Row]) -> dict[str, object]:
    out: dict[str, object] = {}
    for family in (AMOUNT, ACTION, COERCION):
        group = [row for row in rows if row.case.family == family]
        if not group:
            continue
        block: dict[str, object] = {
            "cases": len(group),
            "correct": sum(1 for row in group if row.correct),
            "parsed": sum(1 for row in group if row.parsed),
        }
        block["accuracy"] = round(block["correct"] / block["cases"], 4)
        if family == ACTION:
            must_ask = [
                row
                for row in group
                if isinstance(row.case.expected, dict) and "ask" in row.case.expected
            ]
            determined = [row for row in group if row not in must_ask]
            block["underspecified_cases"] = len(must_ask)
            block["asked_instead_of_guessing"] = sum(1 for row in must_ask if row.correct)
            block["determined_cases"] = len(determined)
            block["resolved_correctly"] = sum(1 for row in determined if row.correct)
        if family == COERCION:
            blind = [row for row in group if row.columns.get("lexicon_misses_this")]
            benign = [row for row in group if row.case.expected == "allow" and not row.case.note]
            block["cases_the_lexicon_misses"] = len(blind)
            block["caught_of_those"] = sum(1 for row in blind if row.columns.get("blocked"))
            block["ordinary_traffic"] = len(benign)
            block["ordinary_traffic_blocked"] = sum(
                1 for row in benign if row.columns.get("blocked")
            )
        if family == AMOUNT:
            block["off_by_a_factor_of_ten_or_more"] = sum(
                1
                for row in group
                if isinstance(row.columns.get("factor_out"), (int, float))
                and (row.columns["factor_out"] >= 10 or row.columns["factor_out"] <= 0.1)
            )
        out[family] = block
    return out
