"""Project 05 at full size: 60,000 policies through the language, 5 through the model.

Two columns, measured at sizes that differ by four orders of magnitude on purpose.

**The language, on all 60,000.** Every generated policy is compiled, printed back from
its own tree, compiled again, and then run over borrowers. Four properties have to hold
on every one of them, and each is a different kind of defect if it fails:

``compiles``
    the text came out of this project's own printer, so a parse error is a grammar that
    cannot read what it writes
``round_trips``
    printing the compiled tree gives back the same text - the property that keeps a
    policy's words and its behaviour from drifting apart
``evaluates``
    every borrower gets a decision rather than an exception
``decisions_are_stable``
    the policy and its reprinted twin decide the same borrowers the same way

**The model, on 5.** ``results.json`` holds the model arm: five policies in prose, two
arms, scored by agreement over the whole 50,000-borrower cohort. Five is the sample; this
file records the size of the bank it is a sample of, because "4 of 5 compiled" and
"80% of policies compile" are not the same claim.
"""

from __future__ import annotations

import json
import pathlib
import time

from shared import datasets

from ..p02_policy_bench import cohort, dsl, engine
from . import bank
from .benchmark import RESULTS as MODEL_RESULTS

RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"

#: Borrowers each policy is evaluated against. Every policy is run over this same slice
#: rather than the whole cohort: 60,000 policies times 50,000 borrowers is three billion
#: evaluations, which would take days and answer the same question as 400 does. The
#: cohort-wide numbers live in project 02, where there are three policies rather than
#: sixty thousand.
SLICE = 400


def score_the_language(limit: int | None = None, verbose: bool = True) -> dict[str, object]:
    pool = cohort.generate()[:SLICE]
    records = [borrower.record() for borrower in pool]

    totals = {
        "policies": 0,
        "compiles": 0,
        "round_trips": 0,
        "evaluates": 0,
        "decisions_are_stable": 0,
    }
    failures: list[dict[str, object]] = []
    decisions_counted = 0
    started = time.perf_counter()

    for row in bank.read(limit=limit):
        totals["policies"] += 1
        if verbose and totals["policies"] % 10_000 == 0:
            print(f"    {totals['policies']:,}", flush=True)
        text = str(row["reference"])
        try:
            policy = dsl.compile_policy(text)
        except dsl.PolicyError as exc:
            if len(failures) < 20:
                failures.append({"id": row["id"], "stage": "compile", "error": str(exc)})
            continue
        totals["compiles"] += 1

        printed = policy.render()
        try:
            twin = dsl.compile_policy(printed)
        except dsl.PolicyError as exc:
            if len(failures) < 20:
                failures.append({"id": row["id"], "stage": "recompile", "error": str(exc)})
            continue
        if twin.render() == printed:
            totals["round_trips"] += 1
        elif len(failures) < 20:
            failures.append({"id": row["id"], "stage": "round_trip", "error": "text changed"})

        try:
            first = [engine.evaluate(policy, record) for record in records]
            second = [engine.evaluate(twin, record) for record in records]
        except Exception as exc:  # noqa: BLE001
            if len(failures) < 20:
                failures.append(
                    {
                        "id": row["id"],
                        "stage": "evaluate",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            continue
        totals["evaluates"] += 1
        decisions_counted += len(first)
        same = all(
            (a.outcome, round(a.limit, 2)) == (b.outcome, round(b.limit, 2))
            for a, b in zip(first, second, strict=True)
        )
        if same:
            totals["decisions_are_stable"] += 1
        elif len(failures) < 20:
            failures.append({"id": row["id"], "stage": "stability", "error": "decisions differ"})

    def rate(key: str) -> float | None:
        return round(totals[key] / totals["policies"], 6) if totals["policies"] else None

    return {
        **totals,
        "borrowers_per_policy": len(records),
        "decisions_made": decisions_counted,
        "compile_rate": rate("compiles"),
        "round_trip_rate": rate("round_trips"),
        "evaluate_rate": rate("evaluates"),
        "stable_decision_rate": rate("decisions_are_stable"),
        "failures": failures,
        "seconds": round(time.perf_counter() - started, 1),
        "reading": (
            "Every policy here was printed by this project's own printer, so any rate "
            "below 1.000 is the language failing to read its own output. That is a "
            "different and more serious thing than a model failing to write it."
        ),
    }


def model_sample() -> dict[str, object]:
    if not MODEL_RESULTS.is_file():
        return {"note": "the model arm has not been run; see make bench-models"}
    data = json.loads(MODEL_RESULTS.read_text(encoding="utf-8"))
    setup = data.get("setup", {})
    bank_rows = datasets.load_manifest().get(bank.KEY, {}).get("rows")
    sample = setup.get("policy_cases")
    return {
        "policies_the_model_compiled": sample,
        "policies_in_the_bank": bank_rows,
        "share_of_the_bank": round(sample / bank_rows, 8) if (sample and bank_rows) else None,
        "borrowers_each_policy_was_run_over": setup.get("borrowers"),
        "arms": setup.get("arms"),
        "by_model_and_arm": data.get("by_model_and_arm", {}),
        "why_it_is_a_sample": (
            "Five policies in prose, written by hand to be awkward in five specific ways. "
            "The bank is the population; five is what a local model has been asked so far, "
            "and '4 of 5 compiled' is a count rather than a rate."
        ),
    }


def run(limit: int | None = None, verbose: bool = True) -> dict[str, object]:
    def say(text: str) -> None:
        if verbose:
            print(text, flush=True)

    say("=== the language against every policy in the bank")
    results = {
        "language_on_the_whole_bank": score_the_language(limit=limit, verbose=verbose),
        "model_on_a_sample": model_sample(),
        "setup": {
            "bank": datasets.load_manifest().get(bank.KEY, {"note": "not in the manifest"}),
            "borrowers_per_policy": SLICE,
        },
    }
    RESULTS.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    say(f"wrote {RESULTS.name}")
    return results


if __name__ == "__main__":
    run()
