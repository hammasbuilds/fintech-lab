"""Run project 03's test set against a local model, beside the rules, and write results.

    uv run python -m projects.p06_model_vs_lexicon.benchmark           # cache only
    uv run python -m projects.p06_model_vs_lexicon.benchmark --live    # ask the model

Both columns in the report come from the same grader on the same cases. The rules column
is project 03's shipped layers, not a sketch of them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shared import llm

from . import grade, tasks

RESULTS = Path(__file__).with_name("results.json")

BUDGET = {tasks.AMOUNT: 90, tasks.ACTION: 260, tasks.COERCION: 140}

MODELS = ("qwen2.5:14b-instruct",)
ARMS = (tasks.BARE, tasks.TAUGHT)


def run(
    models: tuple[str, ...] = MODELS,
    arms: tuple[str, ...] = ARMS,
    *,
    live: bool = False,
    amount_limit: int | None = 12,
) -> dict[str, Any]:
    cases = tasks.cases(amount_limit=amount_limit)
    usage = llm.Usage()
    rows: list[grade.Row] = []
    missing: list[str] = []

    for model in models:
        for arm in arms:
            for case in cases:
                prompt = tasks.prompt(case, arm)
                options = llm.Options(num_predict=BUDGET[case.family], json_mode=True)
                if (
                    not live
                    and llm.read_cache(llm.digest(model, prompt, tasks.SYSTEM, options)) is None
                ):
                    missing.append(f"{model} {arm} {case.label}")
                    continue
                try:
                    reply = llm.generate(
                        model, prompt, system=tasks.SYSTEM, options=options, usage=usage
                    )
                except llm.ModelUnavailable as exc:
                    missing.append(f"{model} {arm} {case.label}: {exc}")
                    continue
                row = grade.grade(case, reply.text, arm, model)
                row.columns["hit_token_cap"] = reply.truncated
                row.columns["seconds"] = round(reply.duration_ms / 1000, 1)
                rows.append(row)

    rule_rows = [grade.rules(case) for case in cases]

    by_arm: dict[str, Any] = {}
    for model in models:
        for arm in arms:
            group = [row for row in rows if row.model == model and row.arm == arm]
            if group:
                by_arm[f"{model} / {arm}"] = grade.summarise(group)

    return {
        "setup": {
            "models": list(models),
            "arms": list(arms),
            "cases": len(cases),
            "families": {
                family: sum(1 for case in cases if case.family == family)
                for family in (tasks.AMOUNT, tasks.ACTION, tasks.COERCION)
            },
            "amount_cases_used": amount_limit,
            "token_budget": BUDGET,
            "where_the_cases_come_from": (
                "project 03's testset.py, unchanged, graded by project 03's own notion of "
                "a correct action"
            ),
            "what_the_arms_differ_by": (
                "bare gives the task and the contact list; taught adds the number "
                "conventions (hazaar, lakh, sawa/paune/saadhe, saat against saath) and the "
                "instruction to ask rather than choose. Nothing else differs."
            ),
        },
        "usage": usage.as_dict(),
        "cache": llm.cache_size(),
        "incomplete": missing,
        "rules": grade.summarise(rule_rows),
        "by_model_and_arm": by_arm,
        "rows": [row.as_dict() for row in rows],
        "rule_rows": [row.as_dict() for row in rule_rows],
    }


def _cells(block: dict[str, Any]) -> list[str]:
    amount = block.get(tasks.AMOUNT, {})
    action = block.get(tasks.ACTION, {})
    co = block.get(tasks.COERCION, {})
    return [
        f"{amount.get('correct', 0)}/{amount.get('cases', 0)}",
        f"{action.get('resolved_correctly', 0)}/{action.get('determined_cases', 0)}",
        f"{action.get('asked_instead_of_guessing', 0)}/{action.get('underspecified_cases', 0)}",
        f"{co.get('correct', 0)}/{co.get('cases', 0)}",
        f"{co.get('caught_of_those', 0)}/{co.get('cases_the_lexicon_misses', 0)}",
        f"{co.get('ordinary_traffic_blocked', 0)}/{co.get('ordinary_traffic', 0)}",
    ]


HEADS = ["amounts", "resolved", "asked", "verdicts", "blind spot", "false blocks"]
WIDTHS = [10, 10, 9, 10, 12, 14]


def report(results: dict[str, Any]) -> str:
    setup = results["setup"]
    usage = results["usage"]
    lines = [
        "06 Model against lexicon - project 03's test set, both ways",
        "",
        f"  cases                        {setup['cases']}"
        f"  ({setup['families'][tasks.AMOUNT]} amounts, "
        f"{setup['families'][tasks.ACTION]} actions, "
        f"{setup['families'][tasks.COERCION]} messages)",
        f"  replies generated            {usage['generated']}"
        f"  (plus {usage['served_from_cache']} from cache)",
        f"  wall clock                   {usage['wall_clock_seconds']}s",
        f"  hit the token cap            {usage['hit_the_token_cap']}",
    ]
    if results["incomplete"]:
        lines.append(
            f"  NOT RUN                      {len(results['incomplete'])} case(s) have no "
            f"cached reply; re-run with --live"
        )

    def row(name: str, cells: list[str]) -> str:
        out = "    " + name.ljust(30)
        for cell, width in zip(cells, WIDTHS, strict=True):
            out += cell.rjust(width)
        return out

    lines += ["", row("", HEADS)]
    lines.append(row("project 03's rules", _cells(results["rules"])))
    for name, block in results["by_model_and_arm"].items():
        lines.append(row(name, _cells(block)))

    lines += [
        "",
        "  'blind spot' is the four scams phrased in ordinary language that the lexicon",
        "  provably misses - the rules score 0/4 there by construction, and it is the one",
        "  column where a model has room to win. 'false blocks' is what that costs on",
        "  ordinary traffic, and the two have to be read together.",
        "",
        "  'asked' counts under-specified utterances the answer stopped and asked about",
        "  rather than resolving. A column of zeroes there with a high 'resolved' is not a",
        "  better assistant, it is one that guesses at who to pay.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--models", nargs="*", default=list(MODELS))
    parser.add_argument("--arms", nargs="*", default=list(ARMS))
    parser.add_argument("--amounts", type=int, default=12)
    args = parser.parse_args()

    results = run(
        models=tuple(args.models),
        arms=tuple(args.arms),
        live=args.live,
        amount_limit=args.amounts,
    )
    RESULTS.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(report(results))
    print(f"\nwritten to {RESULTS}")


if __name__ == "__main__":
    main()
