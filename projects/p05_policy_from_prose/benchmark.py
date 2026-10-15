"""Run the two tasks against each model and arm, and write ``results.json``.

    uv run python -m projects.p05_policy_from_prose.benchmark           # cache only
    uv run python -m projects.p05_policy_from_prose.benchmark --live    # ask the model

The number to read first is **compiled but different**: policies that parse, run, and
decide a different set of applicants than the document they were translated from. That is
the failure this project is for, and it is invisible to any check that stops at the text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shared import llm

from . import grade, tasks

RESULTS = Path(__file__).with_name("results.json")

#: A policy is longer than it looks once every reason string is written out, and a cap
#: below that truncates mid-rule - which compiles as a different policy or not at all.
BUDGET = {tasks.COMPILE: 1100, tasks.NOTICE: 400}

#: The default is the one the published numbers were measured with, so a plain
#: re-run reproduces them. Pass --models to add another, e.g. qwen2.5:7b-instruct
#: for a size comparison.
MODELS = ("qwen2.5:14b-instruct",)
ARMS = (tasks.BARE, tasks.SPEC)


def run(
    models: tuple[str, ...] = MODELS,
    arms: tuple[str, ...] = ARMS,
    *,
    live: bool = False,
) -> dict[str, Any]:
    usage = llm.Usage()
    compiles: list[grade.CompileResult] = []
    notices: list[grade.NoticeResult] = []
    missing: list[str] = []

    for model in models:
        for arm in arms:
            for case in tasks.POLICY_CASES:
                prompt = tasks.compile_prompt(case, arm)
                options = llm.Options(num_predict=BUDGET[tasks.COMPILE])
                if (
                    not live
                    and llm.read_cache(llm.digest(model, prompt, tasks.SYSTEM, options)) is None
                ):
                    missing.append(f"{model} {arm} compile:{case.key}")
                    continue
                try:
                    reply = llm.generate(
                        model, prompt, system=tasks.SYSTEM, options=options, usage=usage
                    )
                except llm.ModelUnavailable as exc:
                    missing.append(f"{model} {arm} compile:{case.key}: {exc}")
                    continue
                result = grade.grade_compile(case, reply.text, arm, model, aborted=reply.aborted)
                result.columns["hit_token_cap"] = reply.truncated
                result.columns["seconds"] = round(reply.duration_ms / 1000, 1)
                compiles.append(result)

            for notice_case in tasks.NOTICE_CASES:
                prompt = tasks.notice_prompt(notice_case, arm)
                options = llm.Options(num_predict=BUDGET[tasks.NOTICE])
                if (
                    not live
                    and llm.read_cache(llm.digest(model, prompt, tasks.NOTICE_SYSTEM, options))
                    is None
                ):
                    missing.append(f"{model} {arm} notice:{notice_case.key}")
                    continue
                try:
                    reply = llm.generate(
                        model,
                        prompt,
                        system=tasks.NOTICE_SYSTEM,
                        options=options,
                        usage=usage,
                    )
                except llm.ModelUnavailable as exc:
                    missing.append(f"{model} {arm} notice:{notice_case.key}: {exc}")
                    continue
                notices.append(
                    grade.grade_notice(notice_case, reply.text, arm, model, aborted=reply.aborted)
                )

    baseline_compiles = [
        grade.grade_compile(case, case.reference, "reference", "none (code)")
        for case in tasks.POLICY_CASES
    ]
    baseline_notices = [grade.baseline_notice(case) for case in tasks.NOTICE_CASES]

    by_arm: dict[str, Any] = {}
    for model in models:
        for arm in arms:
            key = f"{model} / {arm}"
            rows = [row for row in compiles if row.model == model and row.arm == arm]
            notes = [row for row in notices if row.model == model and row.arm == arm]
            if rows or notes:
                by_arm[key] = {
                    "compile": grade.summarise_compiles(rows),
                    "notice": grade.summarise_notices(notes),
                }

    return {
        "setup": {
            "models": list(models),
            "arms": list(arms),
            "policy_cases": len(tasks.POLICY_CASES),
            "notice_cases": len(tasks.NOTICE_CASES),
            "borrowers": len(grade.borrowers()),
            "token_budget": BUDGET,
            "what_the_arms_differ_by": (
                "bare gives the prose and the task; spec adds the grammar, an example "
                "policy and the field list. Nothing else differs."
            ),
        },
        "usage": usage.as_dict(),
        "cache": llm.cache_size(),
        "incomplete": missing,
        "baseline": {
            "what": "project 02's hand-written policies and template notices",
            "compile": grade.summarise_compiles(baseline_compiles),
            "notice": grade.summarise_notices(baseline_notices),
        },
        "by_model_and_arm": by_arm,
        "compile_rows": [row.as_dict() for row in compiles],
        "notice_rows": [row.as_dict() for row in notices],
    }


def _row(name: str, cells: list[str], widths: list[int]) -> str:
    out = "    " + name.ljust(32)
    for cell, width in zip(cells, widths, strict=True):
        out += cell.rjust(width)
    return out


def _compile_cells(block: dict[str, Any]) -> list[str]:
    return [
        f"{block['compiled']}/{block['cases']}",
        f"{block['behaved_identically']}/{block['cases']}",
        f"{block['mean_agreement']:.3f}",
        str(block["compiled_but_different"]),
    ]


def _notice_cells(block: dict[str, Any]) -> list[str]:
    return [
        f"{block['mean_score']:.2f}",
        f"{block['all_checks_passed']}/{block['cases']}",
        f"{block['safe']}/{block['cases']}",
        str(block["invented_a_reason"]),
        str(block["promised_something"]),
    ]


def report(results: dict[str, Any]) -> str:
    setup = results["setup"]
    usage = results["usage"]
    lines = [
        "05 Policy from prose - a local model against project 02's own cohort",
        "",
        f"  policies described in prose   {setup['policy_cases']}",
        f"  notice cases                  {setup['notice_cases']}",
        f"  borrowers each policy decides {setup['borrowers']:,}",
        f"  replies generated             {usage['generated']}"
        f"  (plus {usage['served_from_cache']} from cache)",
        f"  wall clock                    {usage['wall_clock_seconds']}s",
        f"  hit the token cap             {usage['hit_the_token_cap']}",
        f"  aborted by the server         {usage['aborted_by_the_server']}",
    ]
    if results["incomplete"]:
        lines.append(
            f"  NOT RUN                       {len(results['incomplete'])} case(s) have no "
            f"cached reply; re-run with --live"
        )

    compile_widths = [10, 11, 11, 11]
    lines += [
        "",
        "  turning prose into a policy that runs",
        "",
        _row("", ["compiles", "identical", "agreement", "different"], compile_widths),
    ]
    baseline = results["baseline"]["compile"]
    if baseline:
        lines.append(_row("project 02's own policies", _compile_cells(baseline), compile_widths))
    for name, summary in results["by_model_and_arm"].items():
        block = summary["compile"]
        if block:
            lines.append(_row(name, _compile_cells(block), compile_widths))

    lines += [
        "",
        "  'different' is the column that matters: it compiled, it ran, and it decided a",
        "  different set of applicants than the document it came from.",
    ]

    notice_widths = [10, 10, 8, 10, 10]
    lines += [
        "",
        "  the decline and approval notices",
        "",
        _row("", ["checks", "all pass", "safe", "invented", "promised"], notice_widths),
    ]
    base_notice = results["baseline"]["notice"]
    if base_notice:
        lines.append(_row("project 02's templates", _notice_cells(base_notice), notice_widths))
    for name, summary in results["by_model_and_arm"].items():
        block = summary["notice"]
        if block:
            lines.append(_row(name, _notice_cells(block), notice_widths))

    lines += [
        "",
        "  'invented' counts notices that gave the applicant a reason the policy did not;",
        "  'promised' counts notices that implied a future approval. Both are zero for the",
        "  templates by construction, which is what makes the model column readable.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--models", nargs="*", default=list(MODELS))
    parser.add_argument("--arms", nargs="*", default=list(ARMS))
    args = parser.parse_args()

    results = run(models=tuple(args.models), arms=tuple(args.arms), live=args.live)
    RESULTS.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(report(results))
    print(f"\nwritten to {RESULTS}")


if __name__ == "__main__":
    main()
