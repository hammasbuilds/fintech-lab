"""Project 06 at full size: the rules answer 200,000 utterances, the model answers 65.

This project has no dataset of its own and should not have one. Its question is "how do
the rules and a model compare on the same cases", so it uses project 03's generated set -
200,000 labelled utterances - exactly as project 03 scores it, and puts the model's 65
beside it with the sample size stated.

The asymmetry is the finding, not a defect in the write-up. The rules are a lexicon and a
grammar: they answer 200,000 cases in about a minute, so there is no reason to sample
them. Each model answer is a local call measured in seconds, so 200,000 of them is about
a fortnight of GPU time for a result whose shape is already visible at 65. What matters is
that nobody reads "the model caught 2 of 4 blind-spot cases" as a rate - 2 of 4 is two
cases, and the file says so.
"""

from __future__ import annotations

import json
import pathlib

from shared import datasets

from ..p03_guardian import generator
from ..p03_guardian.bigbench import RESULTS as RULES_AT_SCALE
from .benchmark import RESULTS as MODEL_RESULTS

RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"


def _load(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def run(verbose: bool = True) -> dict[str, object]:
    rules = _load(RULES_AT_SCALE).get("generated", {})
    model = _load(MODEL_RESULTS)
    bank = datasets.load_manifest().get(generator.KEY, {})
    bank_rows = bank.get("rows")
    model_cases = model.get("setup", {}).get("cases")

    rules_view = {
        "cases": rules.get("cases_scored"),
        "amount_exact_rate": rules.get("amounts", {}).get("exact_rate"),
        "amount_off_by_ten_or_more": rules.get("amounts", {}).get("off_by_ten_or_more"),
        "intent_accuracy": rules.get("intents", {}).get("accuracy"),
        "payee_resolve_rate": rules.get("beneficiaries", {}).get("resolve_rate"),
        "payee_ask_rate": rules.get("beneficiaries", {}).get("ask_rate"),
        "coercion_catch_rate": rules.get("coercion", {}).get("catch_rate"),
        "coercion_by_family": {
            name: entry.get("catch_rate")
            for name, entry in (rules.get("coercion", {}).get("by_family", {}) or {}).items()
        },
        "false_block_rate_on_ordinary_traffic": rules.get("benign", {}).get("false_block_rate"),
        "confirmation_accuracy": rules.get("replies", {}).get("accuracy"),
    }

    results = {
        "the_same_dataset": {
            "key": generator.KEY,
            "rows": bank_rows,
            "sha256": bank.get("sha256"),
            "note": (
                "Project 03's generated set, unchanged. Both columns are scored on it, "
                "which is the only reason they can be put side by side."
            ),
        },
        "rules_on_the_whole_set": rules_view,
        "model_on_a_sample": {
            "cases": model_cases,
            "share_of_the_set": (
                round(model_cases / bank_rows, 8) if (model_cases and bank_rows) else None
            ),
            "arms": model.get("setup", {}).get("arms"),
            "models": model.get("setup", {}).get("models"),
            "by_model_and_arm": model.get("by_model_and_arm", {}),
            "blind_spot_cases_in_the_sample": 4,
            "why_four_cases_cannot_rank_the_arms": (
                "The blind-spot family is the four scams phrased in words the lexicon "
                "does not have. With four cases, one case is 25 points, so 2 against 1 "
                "is a difference of one sentence. Scaling that family up needs new "
                "hand-written scams, not more generated ones: a generator that could "
                "write them would have to know which words the lexicon is missing, and "
                "then it would be writing the answer key."
            ),
        },
        "what_this_comparison_can_and_cannot_say": [
            "It can say the rules answer 200,000 cases at a measured rate, with the "
            "false-block rate on ordinary traffic beside it.",
            "It can say what the model did on 65 of those cases.",
            "It cannot say the model is better or worse at the blind spot, because the "
            "blind spot is four cases and the confidence interval on four cases covers "
            "almost everything.",
        ],
    }
    RESULTS.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    if verbose:
        print(f"wrote {RESULTS.name}")
    return results


if __name__ == "__main__":
    run()
