"""Project 04 at full size: the product answers 60,000 tasks, the model answers a sample.

The two columns in this file are measured at deliberately different sizes, and saying so
is the point:

* **project 01's codec** answers all 60,000 generated tasks. It needs no model and no
  network, so there is no reason to sample it, and a baseline measured on 29 cases cannot
  tell a parser that is right from a parser that is right about those 29.
* **the model** answers the 29 hand-written tasks that ``benchmark.py`` ships, which is a
  **sample of 29 from a bank of 60,000**. Every reply costs seconds of GPU time and 18 of
  58 replies came back as server-side aborts, so the sample is small on purpose rather
  than by accident. ``results.json`` holds those numbers; this file records how large the
  bank they were drawn from is, so nobody reads 29 cases as a measurement of the model.

Extending the model to a bigger sample is one command - ``make bench-models`` with a
larger ``--sample`` - and it is not run here because the GPU on this machine belongs to
something else.
"""

from __future__ import annotations

import json
import pathlib
import time

from shared import datasets

from . import bank
from .benchmark import RESULTS as MODEL_RESULTS

RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"


def score_the_product(limit: int | None = None, verbose: bool = True) -> dict[str, object]:
    by_family: dict[str, dict[str, int]] = {}
    wrong: list[dict[str, object]] = []
    seen = 0
    started = time.perf_counter()

    for task in bank.read(limit=limit):
        seen += 1
        if verbose and seen % 20_000 == 0:
            print(f"    {seen:,}", flush=True)
        family = str(task["family"])
        entry = by_family.setdefault(
            family,
            {
                "cases": 0,
                "correct": 0,
                "errored": 0,
                "found_a_fault": 0,
                "named_the_injected_fault": 0,
            },
        )
        entry["cases"] += 1
        try:
            answer = bank.answer_with_the_product(task)
        except Exception as exc:  # noqa: BLE001 - an exception is a wrong answer here
            entry["errored"] += 1
            if len(wrong) < 30:
                wrong.append(
                    {"id": task["id"], "family": family, "error": f"{type(exc).__name__}: {exc}"}
                )
            continue
        if family == "negative":
            # Two numbers, because one injected fault can make several true statements
            # about a message: did it see a fault at all, and did it name the one that
            # was injected.
            named = list(answer)
            acceptable = set(task.get("also_true") or [task["answer"]])
            entry["found_a_fault"] += int(bool(named))
            entry["correct"] += int(bool(acceptable & set(named)))
            # The stricter number, kept beside the generous one so widening the list of
            # acceptable descriptions cannot quietly flatter the result.
            entry["named_the_injected_fault"] += int(task["answer"] in named)
            if not (acceptable & set(named)) and len(wrong) < 30:
                wrong.append(
                    {
                        "id": task["id"],
                        "family": family,
                        "injected": task["answer"],
                        "named": named,
                    }
                )
            continue
        if answer == task["answer"]:
            entry["correct"] += 1
        elif len(wrong) < 30:
            wrong.append(
                {
                    "id": task["id"],
                    "family": family,
                    "ask": task.get("ask"),
                    "expected": task["answer"],
                    "got": answer,
                }
            )

    return {
        "tasks_scored": seen,
        "seconds": round(time.perf_counter() - started, 1),
        "by_family": {
            name: {
                **entry,
                "accuracy": round(entry["correct"] / entry["cases"], 4) if entry["cases"] else None,
                "found_a_fault_rate": (
                    round(entry["found_a_fault"] / entry["cases"], 4)
                    if name == "negative" and entry["cases"]
                    else None
                ),
                "named_the_injected_fault_rate": (
                    round(entry["named_the_injected_fault"] / entry["cases"], 4)
                    if name == "negative" and entry["cases"]
                    else None
                ),
            }
            for name, entry in sorted(by_family.items())
        },
        "wrong_answers": wrong,
        "how_to_read_the_negative_family": (
            "accuracy counts an answer right when it names any description that is true "
            "of the damaged message; named_the_injected_fault_rate counts only the exact "
            "fault that was injected. The gap between them is the bitmap family, where "
            "nothing downstream can tell which bit moved - see project 01's fuzz finding."
        ),
        "reading": (
            "These are the product's own answers to questions whose answers the product "
            "generated, so anything below 1.000 is a round trip that does not round trip "
            "- a real defect, not a hard case."
        ),
    }


def model_sample() -> dict[str, object]:
    """What the model was measured on, stated as a fraction of the bank."""
    if not MODEL_RESULTS.is_file():
        return {"note": "the model arm has not been run; see make bench-models"}
    data = json.loads(MODEL_RESULTS.read_text(encoding="utf-8"))
    setup = data.get("setup", {})
    usage = data.get("usage", {})
    manifest = datasets.load_manifest().get(bank.KEY, {})
    bank_rows = manifest.get("rows")
    sample = setup.get("cases")
    return {
        "cases_the_model_answered": sample,
        "tasks_in_the_bank": bank_rows,
        "share_of_the_bank": (round(sample / bank_rows, 6) if (sample and bank_rows) else None),
        "arms": setup.get("arms"),
        "models": setup.get("models"),
        "replies_generated": usage.get("generated"),
        "aborted_by_the_server": usage.get("aborted_by_the_server"),
        "by_model_and_arm": data.get("by_model_and_arm", {}),
        "why_it_is_a_sample": (
            "Each reply is a local model call measured in seconds, and on the wire family "
            "most of them end in a server-side abort. 29 cases is what was run; the bank "
            "is the population it was drawn from, and the share above is how much of it "
            "the model has actually seen."
        ),
    }


def run(limit: int | None = None, verbose: bool = True) -> dict[str, object]:
    def say(text: str) -> None:
        if verbose:
            print(text, flush=True)

    say("=== the product against every task in the bank")
    results = {
        "product_on_the_whole_bank": score_the_product(limit=limit, verbose=verbose),
        "model_on_a_sample": model_sample(),
        "setup": {
            "bank": datasets.load_manifest().get(bank.KEY, {"note": "not in the manifest"}),
            "plan": bank.PLAN,
        },
    }
    RESULTS.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    say(f"wrote {RESULTS.name}")
    return results


if __name__ == "__main__":
    run()
