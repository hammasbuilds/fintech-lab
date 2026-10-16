"""Project 03 against 200,000 generated utterances.

``benchmark.py`` scores the 140 hand-written cases: that is the regression suite. This
scores the generated set, where the gold label comes from how the sentence was built and
the sentences were not built by anyone who had seen the parser. The two measure different
things and the generated one is the one that can be wrong in a way nobody expected.

What is scored, per family:

* **amount** - exact match on the value, and separately the share that is wrong by a
  factor of ten or more, because those are the only wrong amounts that empty an account
* **action** - intent, and whether the assistant resolved a payee it should have
  resolved or asked about one it should have asked about
* **coercion** - caught or missed, by family, and which families it confuses
* **benign** - the false-block rate, on traffic generated to the same length and urgency
  distribution as the coercion traffic
* **reply** - whether a confirmation was read as yes or no

Every miss is counted by *style of phrasing* as well as in total, which is the table that
says something actionable: "6% of amounts are wrong" is not a bug report, and "every
amount written with a fractional modifier in Urdu script is wrong" is.

Committed to ``results_datasets.json``; not re-run by CI, which has no 72 MB corpus.
"""

from __future__ import annotations

import json
import pathlib
import time

from shared import datasets

from . import coercion, confirm, generator, nlu, numerals

RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"

#: The contact book the generator labelled against. Scoring the parser against the
#: shipped book would mark it wrong for not knowing people who are not in it.
CONTACTS = tuple(
    nlu.Contact(contact.name, contact.last4, contact.bank) for contact in generator.BOOK
)


def _bump(table: dict[str, int], key: str) -> None:
    table[key] = table.get(key, 0) + 1


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def score(limit: int | None = None, verbose: bool = True) -> dict[str, object]:
    started = time.perf_counter()

    amounts = {"cases": 0, "exact": 0, "none_found": 0, "off_by_ten_or_more": 0}
    by_style: dict[str, dict[str, int]] = {}
    intents = {"cases": 0, "correct": 0}
    by_intent: dict[str, dict[str, int]] = {}
    resolution = {"should_resolve": 0, "resolved": 0, "should_ask": 0, "asked": 0}
    coercion_rows = {"cases": 0, "blocked": 0}
    by_family: dict[str, dict[str, int]] = {}
    benign = {"cases": 0, "blocked": 0}
    replies = {"cases": 0, "correct": 0}
    examples: list[dict[str, object]] = []
    seen = 0

    for row in generator.read(limit=limit):
        seen += 1
        if verbose and seen % 25_000 == 0:
            print(f"    {seen:,}", flush=True)
        text = str(row["text"])
        gold_amount = row["gold_amount"]
        style = str(row.get("recipe", {}).get("style", "-"))

        # ---------------------------------------------------------------- amounts
        if gold_amount is not None:
            amounts["cases"] += 1
            bucket = by_style.setdefault(
                style, {"cases": 0, "exact": 0, "none_found": 0, "off_by_ten_or_more": 0}
            )
            bucket["cases"] += 1
            found = numerals.find_all(text)
            best = found[0].value if found else None
            if best is None:
                amounts["none_found"] += 1
                bucket["none_found"] += 1
            elif abs(best - float(gold_amount)) < 0.5:
                amounts["exact"] += 1
                bucket["exact"] += 1
            else:
                ratio = max(best, float(gold_amount)) / max(min(best, float(gold_amount)), 1e-9)
                if ratio >= 10:
                    amounts["off_by_ten_or_more"] += 1
                    bucket["off_by_ten_or_more"] += 1
                if len(examples) < 40:
                    examples.append(
                        {
                            "text": text,
                            "style": style,
                            "gold": gold_amount,
                            "read_as": best,
                            "ratio": round(ratio, 2),
                        }
                    )

        # ---------------------------------------------------- intent and resolution
        utterance = nlu.parse(text, contacts=CONTACTS)
        gold_intent = row["gold_intent"]
        if gold_intent in {"send_money", "bill_payment", "mobile_topup", "balance"}:
            intents["cases"] += 1
            entry = by_intent.setdefault(str(gold_intent), {"cases": 0, "correct": 0})
            entry["cases"] += 1
            if utterance.intent == gold_intent:
                intents["correct"] += 1
                entry["correct"] += 1

        if row["gold_resolution"] == "resolved" and row["gold_payee"]:
            resolution["should_resolve"] += 1
            if utterance.beneficiary and utterance.beneficiary.name == row["gold_payee"]:
                resolution["resolved"] += 1
        elif row["gold_resolution"] == "ask":
            resolution["should_ask"] += 1
            if utterance.beneficiary is None:
                resolution["asked"] += 1

        # ------------------------------------------------------------------ coercion
        if row["family"] == "coercion":
            coercion_rows["cases"] += 1
            assessment = coercion.assess(text)
            caught = bool(assessment.families)
            coercion_rows["blocked"] += int(caught)
            for family in row["gold_coercion"]:
                entry = by_family.setdefault(str(family), {"cases": 0, "caught": 0, "named": 0})
                entry["cases"] += 1
                entry["caught"] += int(caught)
                entry["named"] += int(family in assessment.families)
        elif row["family"] == "benign":
            benign["cases"] += 1
            benign["blocked"] += int(bool(coercion.assess(text).families))
        elif row["family"] == "reply":
            # Confirmations are read by confirm.classify_reply, not by the intent
            # detector: a reply to a question is not an instruction, and there is no
            # "affirmative" intent for detect_intent to return. Scoring them through the
            # intent detector gave 0.503 - chance - which was a defect in this file and
            # not in the product.
            replies["cases"] += 1
            affirmative = bool(row.get("recipe", {}).get("affirmative"))
            read_as = confirm.classify_reply(text)
            want = "affirmative" if affirmative else "negative"
            replies["correct"] += int(read_as == want)

    worst_styles = sorted(
        (
            {
                "style": name,
                "cases": bucket["cases"],
                "exact_rate": _rate(bucket["exact"], bucket["cases"]),
                "none_found": bucket["none_found"],
                "off_by_ten_or_more": bucket["off_by_ten_or_more"],
            }
            for name, bucket in by_style.items()
        ),
        key=lambda entry: entry["exact_rate"] if entry["exact_rate"] is not None else 1.0,
    )

    return {
        "cases_scored": seen,
        "seconds": round(time.perf_counter() - started, 1),
        "amounts": {
            **amounts,
            "exact_rate": _rate(amounts["exact"], amounts["cases"]),
            "off_by_ten_or_more_rate": _rate(amounts["off_by_ten_or_more"], amounts["cases"]),
            "by_style": worst_styles,
            "reading": (
                "An exact rate below one is not automatically a defect - some of these "
                "sentences carry two numbers and the gold names one of them - but a style "
                "whose rate is far below the others is a phrasing the parser does not "
                "handle, and that is a bug report with an address."
            ),
        },
        "intents": {
            **intents,
            "accuracy": _rate(intents["correct"], intents["cases"]),
            "by_intent": {
                name: {**entry, "accuracy": _rate(entry["correct"], entry["cases"])}
                for name, entry in sorted(by_intent.items())
            },
        },
        "beneficiaries": {
            **resolution,
            "resolve_rate": _rate(resolution["resolved"], resolution["should_resolve"]),
            "ask_rate": _rate(resolution["asked"], resolution["should_ask"]),
            "reading": (
                "These two have to be read together. A resolver that never asks scores "
                "1.000 on the first and 0.000 on the second, and pays whoever it guessed."
            ),
        },
        "coercion": {
            **coercion_rows,
            "catch_rate": _rate(coercion_rows["blocked"], coercion_rows["cases"]),
            "by_family": {
                name: {
                    **entry,
                    "catch_rate": _rate(entry["caught"], entry["cases"]),
                    "named_the_family_rate": _rate(entry["named"], entry["cases"]),
                }
                for name, entry in sorted(by_family.items())
            },
        },
        "benign": {
            **benign,
            "false_block_rate": _rate(benign["blocked"], benign["cases"]),
        },
        "replies": {**replies, "accuracy": _rate(replies["correct"], replies["cases"])},
        "worst_amount_examples": examples,
    }


def run(limit: int | None = None, verbose: bool = True) -> dict[str, object]:
    def say(text: str) -> None:
        if verbose:
            print(text, flush=True)

    say("=== scoring the generated set with the shipped layers")
    results = {"generated": score(limit=limit, verbose=verbose)}
    results["setup"] = {
        "dataset": datasets.load_manifest().get(generator.KEY, {"note": "not in the manifest yet"}),
        "contacts": len(CONTACTS),
        "plan": generator.PLAN,
        "why_this_is_a_separate_file": (
            "The generated set is 72 MB, so CI cannot re-derive these numbers. "
            "results.json stays the 140 hand-written cases, which need no data files."
        ),
    }
    RESULTS.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    say(f"wrote {RESULTS.name}")
    return results


if __name__ == "__main__":
    run()
