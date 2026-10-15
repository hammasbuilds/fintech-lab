"""Run the four task families against each model and arm, and write ``results.json``.

    uv run python -m projects.p04_spec_to_wire.benchmark              # cache only
    uv run python -m projects.p04_spec_to_wire.benchmark --live       # ask the model

Without ``--live`` this runs entirely from the committed cache, so the published numbers
can be rechecked on a machine with no GPU and no Ollama. With ``--live`` it fills any gaps
and writes them to the cache.

Token budgets are set per family from the length of a correct answer. A cap below that
turns a right answer into an unparseable one, and the failure then looks exactly like the
model not knowing the task - so the count of replies that hit the cap is reported next to
the scores rather than left out.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shared import llm

from . import grade, tasks

RESULTS = Path(__file__).with_name("results.json")

#: Sized from the deterministic answer for each family, with room to spare.
BUDGET = {
    tasks.DECODE: 260,
    tasks.FIELDS: 900,
    tasks.WIRE: 420,
    tasks.NEGATIVE: 1400,
}

#: The models the headline numbers use. qwen3.6:27b is deliberately absent: it does not
#: fit in this machine's 16 GB alongside anything else, so it runs partly on CPU at about
#: 13 seconds a token, and it spends its budget on hidden reasoning before emitting any
#: content. Both are properties of the setup rather than of the model, and either way it
#: cannot be measured here honestly.
MODELS = ("qwen2.5:14b-instruct", "qwen2.5:7b-instruct")
ARMS = (tasks.BARE, tasks.SPEC)


def run(
    models: tuple[str, ...] = MODELS,
    arms: tuple[str, ...] = ARMS,
    *,
    live: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    cases = tasks.cases()
    if limit:
        cases = cases[:limit]

    usage = llm.Usage()
    results: list[grade.Result] = []
    missing: list[str] = []

    for model in models:
        for arm in arms:
            for case in cases:
                prompt = tasks.prompt(case, arm)
                options = llm.Options(num_predict=BUDGET[case.family])
                key = llm.digest(model, prompt, tasks.SYSTEM, options)
                if not live and llm.read_cache(key) is None:
                    missing.append(f"{model} {arm} {case.label}")
                    continue
                try:
                    reply = llm.generate(
                        model,
                        prompt,
                        system=tasks.SYSTEM,
                        options=options,
                        usage=usage,
                    )
                except llm.ModelUnavailable as exc:
                    missing.append(f"{model} {arm} {case.label}: {exc}")
                    continue
                result = grade.grade(case, reply.text, arm, model)
                result.columns["hit_token_cap"] = reply.truncated
                result.columns["seconds"] = round(reply.duration_ms / 1000, 1)
                results.append(result)

    baselines = [grade.baseline(case) for case in cases]

    by_arm: dict[str, Any] = {}
    for model in models:
        for arm in arms:
            rows = [r for r in results if r.model == model and r.arm == arm]
            if rows:
                by_arm[f"{model} / {arm}"] = grade.summarise(rows)

    return {
        "setup": {
            "models": list(models),
            "arms": list(arms),
            "cases": len(cases),
            "families": sorted({case.family for case in cases}),
            "token_budget": BUDGET,
            "models_available_locally": llm.tags(),
            "excluded": {
                "qwen3.6:27b": (
                    "does not fit this machine's 16 GB alongside anything else, so it runs "
                    "partly on CPU (about 13 seconds per token measured), and it spends its "
                    "budget on hidden reasoning before emitting content. A property of the "
                    "setup, not of the model."
                ),
                "qwen2.5-coder:14b": (
                    "a base model, not instruction-tuned: asked to reply with 'OK' it "
                    "replied '88888888'. It would be measuring the wrong thing."
                ),
            },
        },
        "usage": usage.as_dict(),
        "cache": llm.cache_size(),
        "incomplete": missing,
        "baseline": {
            "what": ("project 01's deterministic generator and fault injector, on the same cases"),
            "summary": grade.summarise(baselines),
        },
        "by_model_and_arm": by_arm,
        "rows": [result.as_dict() for result in results],
    }


def report(results: dict[str, Any]) -> str:
    setup = results["setup"]
    lines = [
        "04 Spec to wire - a local model against project 01's own oracle",
        "",
        f"  cases                        {setup['cases']}  ({', '.join(setup['families'])})",
        f"  arms                         {', '.join(setup['arms'])}",
        f"  models                       {', '.join(setup['models'])}",
        f"  replies generated            {results['usage']['generated']}"
        f"  (plus {results['usage']['served_from_cache']} from cache)",
        f"  wall clock                   {results['usage']['wall_clock_seconds']}s",
        f"  hit the token cap            {results['usage']['hit_the_token_cap']}",
    ]
    if results["incomplete"]:
        lines.append(
            f"  NOT RUN                      {len(results['incomplete'])} case(s) have no "
            f"cached reply; re-run with --live"
        )

    lines += ["", "  mean score by family, 0 to 1 - the generator scores 1.00 on all of them", ""]
    families = setup["families"]
    header = "    " + "arm".ljust(34) + "".join(f"{family:>12}" for family in families)
    lines.append(header)
    for name, summary in results["by_model_and_arm"].items():
        row = "    " + name.ljust(34)
        for family in families:
            cell = summary.get(family, {})
            row += f"{cell.get('mean_score', 0):>12.2f}"
        lines.append(row)

    lines += ["", "  how many answers the parser could use at all", ""]
    lines.append("    " + "arm".ljust(34) + f"{'parsed':>10}{'packs':>10}{'clean':>10}")
    for name, summary in results["by_model_and_arm"].items():
        parsed = sum(cell["parsed"] for cell in summary.values())
        packs = sum(cell["packs"] for cell in summary.values())
        clean = sum(cell["invariants_hold"] for cell in summary.values())
        total = sum(cell["cases"] for cell in summary.values())
        lines.append(
            "    "
            + name.ljust(34)
            + f"{f'{parsed}/{total}':>10}{f'{packs}/{total}':>10}{f'{clean}/{total}':>10}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="ask the model for anything uncached")
    parser.add_argument("--models", nargs="*", default=list(MODELS))
    parser.add_argument("--arms", nargs="*", default=list(ARMS))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    results = run(
        models=tuple(args.models),
        arms=tuple(args.arms),
        live=args.live,
        limit=args.limit,
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
