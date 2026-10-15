"""The project 06 dashboard: the model and the rules on the same cases, side by side."""

from __future__ import annotations

import argparse
import json

from shared.iopage import Page, Row, Section, attach_io
from shared.modelpage import Column, Spec, Table, attach
from shared.theme import attach_theme
from shared.webkit import App, attach_metrics, run

from . import tasks
from .benchmark import RESULTS
from .benchmark import run as measure

app = App("model-vs-lexicon", static_dir=None)
attach_theme(app)
attach_metrics(app)

SOURCE = "https://github.com/hammasbuilds/fintech-lab/tree/main/projects/p06_model_vs_lexicon"


def _data() -> dict:
    if RESULTS.is_file():
        try:
            return json.loads(RESULTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return measure(live=False)


def _over(block: dict, top: str, bottom: str) -> str:
    """``correct/attempted``, with both numbers named so a denominator cannot drift."""
    return f"{block.get(top, 0)}/{block.get(bottom, 0)}"


def _cells(name: str, block: dict) -> dict[str, object]:
    amount = block.get(tasks.AMOUNT, {})
    action = block.get(tasks.ACTION, {})
    co = block.get(tasks.COERCION, {})
    return {
        "arm": name,
        "amounts": _over(amount, "correct", "cases"),
        "resolved": _over(action, "resolved_correctly", "determined_cases"),
        "asked": _over(action, "asked_instead_of_guessing", "underspecified_cases"),
        "verdicts": _over(co, "correct", "cases"),
        "blind": _over(co, "caught_of_those", "cases_the_lexicon_misses"),
        "false_blocks": _over(co, "ordinary_traffic_blocked", "ordinary_traffic"),
    }


def spec() -> Spec:
    data = _data()
    setup = data.get("setup", {})
    usage = data.get("usage", {})

    rows = [_cells("project 03's rules", data.get("rules", {}))]
    for name, block in data.get("by_model_and_arm", {}).items():
        rows.append(_cells(name, block))

    case_rows = []
    for row in data.get("rule_rows", []) + data.get("rows", []):
        case_rows.append(
            {
                "family": row.get("family"),
                "text": row.get("text", "")[:70],
                "arm": f"{row.get('model', '')} / {row.get('arm', '')}",
                "correct": row.get("correct"),
                "expected": row.get("expected"),
                "got": row.get("got"),
                "detail": row.get("detail", "") or row.get("why", ""),
            }
        )

    return Spec(
        title="06 Model against lexicon",
        subtitle="Project 03's test set, answered by a model and by the rules, same grader",
        tiles=[
            ("Cases", str(setup.get("cases", "-")), "amounts, actions, messages"),
            (
                "Replies generated",
                str(usage.get("generated", 0)),
                f"{usage.get('served_from_cache', 0)} from cache",
            ),
            ("Wall clock", str(usage.get("wall_clock_seconds", 0)), "seconds"),
            ("Hit the token cap", str(usage.get("hit_the_token_cap", 0)), "truncated answers"),
            ("Cache entries", str(data.get("cache", {}).get("entries", 0)), "committed"),
        ],
        notes=[
            "Both columns come from the same grader on the same cases. The rules column is "
            "project 03's shipped layers, not a sketch of them, and it is the column the "
            "model is read against.",
            "'blind spot' is the four scams phrased in ordinary language that the lexicon "
            "provably misses - the rules score 0/4 there by construction, and it is the one "
            "place a model has room to win. 'false blocks' is what that costs on ordinary "
            "traffic, and the two have to be read together.",
            "'asked' counts under-specified utterances the answer stopped and asked about "
            "rather than resolving. A zero there with a high 'resolved' is not a better "
            "assistant; it is one that guesses at who to pay.",
            "An answer that does not come back as usable JSON is scored wrong, not skipped. "
            "A reply that cannot be parsed cannot drive a transfer, and dropping those cases "
            "would grade the subset that happened to be formatted correctly.",
        ],
        tables=[
            Table(
                title="The same cases, both ways",
                note="Every cell is correct-over-attempted.",
                columns=[
                    Column("arm", "Arm", "text"),
                    Column("amounts", "Amounts", "num"),
                    Column("resolved", "Resolved", "num"),
                    Column("asked", "Asked", "num"),
                    Column("verdicts", "Verdicts", "num"),
                    Column("blind", "Blind spot", "num"),
                    Column("false_blocks", "False blocks", "num"),
                ],
                rows=rows,
                baseline_key="arm",
                baseline_value="project 03's rules",
            ),
            Table(
                title="Every case, with what each side said",
                note="The rules rows are included so a disagreement can be read in one place.",
                columns=[
                    Column("family", "Family", "text"),
                    Column("text", "Utterance", "mono"),
                    Column("arm", "Arm", "text"),
                    Column("correct", "Correct", "verdict"),
                    Column("expected", "Expected", "mono"),
                    Column("got", "Given", "mono"),
                    Column("detail", "Detail", "reply"),
                ],
                rows=case_rows,
            ),
        ],
        links=[
            ("Input and output", "/io"),
            ("Project 03, the baseline", "http://127.0.0.1:8103/"),
            ("Source", SOURCE),
        ],
        incomplete=data.get("incomplete", []),
        setup=setup | {"usage": usage, "cache": data.get("cache", {})},
    )


attach(app, spec, RESULTS)


def io_page() -> Page:
    data = _data()
    rules = data.get("rules", {})
    rule_amount = rules.get("amount", {})
    rule_action = rules.get("action", {})
    rule_coercion = rules.get("coercion", {})
    sections = [
        Section(
            title="Project 03's rules on its own test set",
            given="the same cases, answered by a lexicon and a grammar",
            rows=[
                Row("amounts", _over(rule_amount, "correct", "cases")),
                Row(
                    "actions resolved",
                    _over(rule_action, "resolved_correctly", "determined_cases"),
                ),
                Row(
                    "asked instead of guessing",
                    _over(rule_action, "asked_instead_of_guessing", "underspecified_cases"),
                ),
                Row(
                    "coercion verdicts",
                    _over(rule_coercion, "correct", "cases"),
                ),
                Row(
                    "scams it misses, caught",
                    _over(rule_coercion, "caught_of_those", "cases_the_lexicon_misses"),
                ),
            ],
            reading="The last row is the published weakness: a lexical check cannot catch a "
            "scam phrased in words it does not have.",
        )
    ]
    for name, block in data.get("by_model_and_arm", {}).items():
        amount = block.get("amount", {})
        action = block.get("action", {})
        co = block.get("coercion", {})
        sections.append(
            Section(
                title=f"The model, {name}",
                given="the same cases, the same grader",
                rows=[
                    Row("amounts", _over(amount, "correct", "cases")),
                    Row(
                        "off by a factor of ten or more",
                        str(amount.get("off_by_a_factor_of_ten_or_more", "-")),
                    ),
                    Row(
                        "actions resolved",
                        _over(action, "resolved_correctly", "determined_cases"),
                    ),
                    Row(
                        "asked instead of guessing",
                        _over(action, "asked_instead_of_guessing", "underspecified_cases"),
                    ),
                    Row("coercion verdicts", _over(co, "correct", "cases")),
                    Row(
                        "scams the lexicon misses, caught",
                        _over(co, "caught_of_those", "cases_the_lexicon_misses"),
                    ),
                    Row(
                        "ordinary traffic blocked",
                        _over(co, "ordinary_traffic_blocked", "ordinary_traffic"),
                    ),
                ],
            )
        )
    return Page(
        title="06 Model against lexicon",
        subtitle="The same test set, answered both ways",
        sections=sections,
        links=[("Dashboard", "/"), ("Source", SOURCE)],
        caveat="The rules are the shipped product and score 48/48 on the full amount set. "
        "A model that wins on the coercion blind spot and loses on amounts has not replaced "
        "them; the two layers answer different questions.",
    )


attach_io(app, io_page)


def main(port: int = 8106) -> None:
    run(app, port=port)


# The __main__ block stays last: anything registered after it never runs when this module
# is executed as a program, because main() blocks in the server loop.
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve the project 06 dashboard.")
    parser.add_argument("--port", type=int, default=8106)
    main(parser.parse_args().port)
