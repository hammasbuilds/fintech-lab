"""Project 02 at full size: two real books and a quarter-million-row synthetic cohort.

This is a second benchmark, separate from :mod:`benchmark`, and the split is deliberate.
``benchmark.py`` runs on a 50,000-row generated cohort that any machine can reproduce
from a seed, so CI re-runs it and diffs the committed ``results.json``. The numbers here
need files that are 95 MB and come from Kaggle, so CI cannot re-derive them; they go in
``results_datasets.json``, which is committed and *not* diffed.

Three cohorts, in order of how much they can be trusted:

1. **The real loan book** - 255,347 applications with the outcome that actually happened.
   Three policies, each backtested, swept and scored for discrimination.
2. **The real card book** - 438,557 applications of which 36,457 joined to a repayment
   history. Scored whole with every outcome figure withheld, then the slice reported
   separately under both definitions of "bad".
3. **The synthetic cohort at 250,000** - the same generator the CI benchmark uses at
   50,000, five times larger, to show which of its numbers are stable in the size and
   which were sampling noise.

Run it with ``make bench-datasets`` or ``python -m projects.p02_policy_bench.bigbench``.
"""

from __future__ import annotations

import json
import pathlib
import time

from shared import datasets

from . import (
    backtest,
    cardbook,
    cohort,
    compliance,
    dsl,
    fairness,
    loanbook,
    sweep,
    timeline,
)

RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"
POLICIES = pathlib.Path(__file__).resolve().parent / "policies" / "real"

#: Segments for each book: the attributes that exist on its own records.
LOAN_SEGMENTS = ("education", "employment_type", "marital_status", "loan_purpose")
CARD_SEGMENTS = ("education", "income_type", "family_status", "housing")
SYNTHETIC_SEGMENTS = ("region", "employment")

#: The synthetic cohort size used here, against the 50,000 the CI benchmark runs.
SYNTHETIC_SIZE = 250_000


def _policy_from(path) -> dsl.Policy:
    policy = dsl.compile_policy(path.read_text(encoding="utf-8"))
    if dsl.compile_policy(policy.render()).render() != policy.render():
        raise AssertionError(f"{path.name} does not round-trip through its own text")
    return policy


def _policy(name: str) -> dsl.Policy:
    source = (POLICIES / f"{name}.policy").read_text(encoding="utf-8")
    policy = dsl.compile_policy(source)
    if dsl.compile_policy(policy.render()).render() != policy.render():
        raise AssertionError(f"{name} does not round-trip through its own text")
    return policy


def _numbers(report: backtest.Backtest) -> dict[str, object]:
    out = report.as_dict()
    for drop in ("score_histogram", "segments", "income_bands", "decline_reasons"):
        out.pop(drop, None)
    return out


def _lint(policy: dsl.Policy) -> list[dict[str, object]]:
    notes = compliance.review(policy)
    return [
        {
            "check": note.check,
            "severity": note.severity,
            "title": note.title,
            "line": note.line,
        }
        for note in notes
    ]


def _one_policy(
    name: str,
    policy: dsl.Policy,
    book: list,
    segments: tuple[str, ...],
    *,
    labelled: bool = True,
) -> dict[str, object]:
    started = time.perf_counter()
    report, outcomes = backtest.run(policy, book, labelled=labelled, segments=segments)
    entry: dict[str, object] = {
        "policy": name,
        "fields_used": policy.fields_used,
        "backtest": _numbers(report),
        "segments": report.segments,
        "lint": _lint(policy),
    }
    if labelled:
        entry["sweep"] = sweep.sweep(policy, book)
        entry["discrimination"] = sweep.discrimination(sweep.score_all(policy, book))
        entry["fairness"] = {
            name: fairness.analyse(outcomes, attribute=name) for name in segments[:2]
        }
    entry["seconds"] = round(time.perf_counter() - started, 1)
    return entry


def run(verbose: bool = True) -> dict[str, object]:
    def say(text: str) -> None:
        if verbose:
            print(text, flush=True)

    results: dict[str, object] = {
        "setup": {
            "row_floor": datasets.MINIMUM_ROWS,
            "row_ceiling": datasets.MAXIMUM_ROWS,
            "why_this_is_a_separate_file": (
                "These numbers need raw files that CI cannot download, so this file is "
                "committed and not diffed. results.json stays reproducible from a seed."
            ),
        }
    }

    say("=== the real loan book: 255,347 applications with real outcomes")
    book = loanbook.load()
    loan: dict[str, object] = {"cohort": loanbook.summary(book), "policies": {}}
    for name in ("real_v1_scoregate", "real_v2_no_price", "real_v3_priced"):
        say(f"    {name}")
        policy = _policy(name)
        loan["policies"][name] = _one_policy(name, policy, book, LOAN_SEGMENTS)  # type: ignore[index]
    results["loan_book"] = loan

    say("=== the real card book: 438,557 applications, 8.3% with a repayment history")
    cards, card_summary = cardbook.load()
    card: dict[str, object] = {"cohort": card_summary, "definitions": {}}
    policy = _policy("card_v1")
    say("    whole book, outcomes withheld")
    card["whole_book"] = _one_policy("card_v1", policy, cards, CARD_SEGMENTS, labelled=False)
    for definition in sorted(cardbook.BAD_DEFINITIONS):
        say(f"    labelled slice, {definition}")
        slice_book, slice_summary = cardbook.load(bad_definition=definition)
        labelled = cardbook.labelled_slice(slice_book)
        entry = _one_policy("card_v1", policy, labelled, CARD_SEGMENTS)
        entry["slice"] = slice_summary
        entry["is_a_slice_not_a_dataset"] = {
            "rows": len(labelled),
            "below_the_floor": len(labelled) < datasets.MINIMUM_ROWS,
            "read_it_as": (
                "a check on the whole book's scoring, not a measurement of the book. "
                "The applications with a repayment history are the ones that were "
                "approved and used, which is the opposite of a random sample."
            ),
        }
        card["definitions"][definition] = entry  # type: ignore[index]
    results["card_book"] = card

    say(f"=== the synthetic cohort at {SYNTHETIC_SIZE:,}")
    pool = cohort.generate(SYNTHETIC_SIZE)
    synthetic: dict[str, object] = {
        "cohort": cohort.summary(pool) if hasattr(cohort, "summary") else {"size": len(pool)},
        "policies": {},
    }
    for name in ("nano_v1", "nano_v2", "nano_v4_compliant", "nano_v5_overcap"):
        say(f"    {name}")
        source = (POLICIES.parent / f"{name}.policy").read_text(encoding="utf-8")
        synthetic["policies"][name] = _one_policy(  # type: ignore[index]
            name, dsl.compile_policy(source), pool, SYNTHETIC_SEGMENTS
        )
    results["synthetic_at_scale"] = synthetic

    # ---------------------------------------------------------------- the time axis
    # Everything above happens at one instant. These four need a cohort with dates, and
    # three of them are standard credit practice that this project could not do at all
    # until the generator grew an origination window.
    say("=== vintages, stability and out-of-time, on the synthetic cohort")
    dated = cohort.generate()
    reference = _policy_from(POLICIES.parent / "nano_v4_compliant.policy")
    results["over_time"] = {
        "policy": "nano_v4_compliant",
        "cohort": len(dated),
        "window_months": cohort.WINDOW_MONTHS,
        "vintage_curves": timeline.vintage_curves(reference, dated),
        "population_stability": timeline.psi(reference, dated),
        "characteristic_stability": timeline.characteristic_psi(
            dated,
            (
                "monthly_income",
                "wallet_months",
                "other_app_outstanding",
                "avg_balance",
                "txn_velocity",
                "age",
            ),
        ),
        "out_of_time": timeline.out_of_time(reference, dated),
        "approvals_only": timeline.approvals_only_view(reference, dated),
    }

    results["manifest"] = datasets.load_manifest()
    RESULTS.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    say(f"wrote {RESULTS.name}")
    return results


if __name__ == "__main__":
    run()
