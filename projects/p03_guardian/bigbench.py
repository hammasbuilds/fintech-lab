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

from . import behaviour, coercion, confirm, generator, nlu, numerals

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


def _score_structuring(legs: list[dict]) -> dict[str, object]:
    """Does the behavioural layer notice a split once the legs add up?

    Each leg is replayed into a profile in order, and the question is whether the layer
    escalates on the leg that takes the running total past the wallet's daily cap. A
    single-utterance test set cannot ask this: every leg is an ordinary transfer and the
    pattern only exists across the sequence.
    """
    from datetime import datetime, timedelta

    threads: dict[str, list[dict]] = {}
    for leg in legs:
        threads.setdefault(str(leg["thread_id"]), []).append(leg)

    caught = 0
    missed_entirely = 0
    escalated_early = 0
    by_pattern: dict[str, dict[str, int]] = {}
    examples: list[dict[str, object]] = []
    for name, rows in threads.items():
        rows.sort(key=lambda row: int(row["turn"]))
        cap = float(rows[0]["daily_cap"])
        recipe = rows[0].get("recipe", {})
        pattern = str(recipe.get("pattern", "same_day"))
        spacing = int(recipe.get("days_between_legs", 0) or 0)
        tally = by_pattern.setdefault(pattern, {"threads": 0, "caught": 0})
        tally["threads"] += 1
        # The payee is already known, with a history of ordinary transfers to them, so
        # the first-time-payee signal cannot fire and what is left to catch the split is
        # the cap itself. With an empty history every thread was "caught" on leg one for
        # a reason that had nothing to do with structuring.
        now = datetime(2026, 10, 15, 10, 0)
        payee_name = str(rows[0]["gold_payee"] or "unknown")
        history = [
            behaviour.Transfer(
                payee_name, float(rows[0]["gold_amount"] or 0), now - timedelta(days=day)
            )
            for day in (30, 23, 16, 9)
        ]
        monthly = float(recipe.get("monthly_cap") or 0) or cap * 2
        profile = behaviour.Profile(
            balance=cap * 20,
            daily_cap=cap,
            monthly_cap=monthly,
            history=history,
            now=now,
        )
        crossed_at = None
        first_escalation = None
        running = 0.0
        for index, row in enumerate(rows, start=1):
            amount = float(row["gold_amount"] or 0)
            payee = str(row["gold_payee"] or "unknown")
            # A split across days is replayed across days: each leg arrives on its own
            # date, so the daily total resets exactly as it would in life. Replaying it
            # as if it all happened at once would make the daily cap catch a pattern
            # built to avoid it.
            profile.now = now + timedelta(days=spacing * (index - 1))
            verdict = behaviour.assess(profile, payee, amount)
            if verdict.escalated and first_escalation is None:
                first_escalation = index
            running += amount
            if crossed_at is None and running > cap:
                crossed_at = index
            profile.record(payee, amount)
        if first_escalation is not None:
            tally["caught"] += 1
        if first_escalation is None:
            missed_entirely += 1
            if len(examples) < 20:
                examples.append(
                    {
                        "thread": name,
                        "legs": len(rows),
                        "per_leg": rows[0]["gold_amount"],
                        "cap": cap,
                        "crossed_on_leg": crossed_at,
                        "escalated_on_leg": None,
                    }
                )
        elif crossed_at is not None and first_escalation <= crossed_at:
            caught += 1
            if first_escalation < crossed_at:
                escalated_early += 1

    return {
        "threads": len(threads),
        "legs": len(legs),
        "by_pattern": {
            name: {
                **entry,
                "catch_rate": round(entry["caught"] / entry["threads"], 4)
                if entry["threads"]
                else None,
            }
            for name, entry in sorted(by_pattern.items())
        },
        "caught_by_the_leg_that_crossed_the_cap": caught,
        "catch_rate": round(caught / len(threads), 4) if threads else None,
        "never_escalated": missed_entirely,
        "escalated_before_the_cap_was_crossed": escalated_early,
        "examples_never_escalated": examples,
        "reading": (
            "Read by_pattern, not the total. A same-day split crosses the daily cap and "
            "the daily-cap signal sees it. A split spread over several days never gets "
            "near the daily cap and goes over the *monthly* one - and the behavioural "
            "layer has no monthly view, which is the gap this family exists to measure."
        ),
    }


def _score_threads(turns: dict[str, list[dict]]) -> dict[str, object]:
    """Replay whole conversations through the session, not one sentence at a time.

    Whether a conversation *can* end in a transfer is part of the gold. A thread about
    somebody who is not in the contact book cannot, and counting that as a failure to
    complete would mark the assistant down for refusing to pay a stranger.
    """
    from .confirm import Session, State

    completed = 0
    handled = 0
    executed_after_yes = 0
    executed_after_no = 0
    asked_to_strengthen = 0
    unfinished = 0
    correctly_refused_unknown = 0
    correctly_stopped = 0
    by_shape: dict[str, dict[str, int]] = {}
    examples: list[dict[str, object]] = []

    for name, rows in sorted(turns.items()):
        rows.sort(key=lambda row: int(row["turn"]))
        # The account's own contact book, not the shipped demo one. Scoring threads
        # against seven contacts the generator never used made every one of them fail at
        # turn one with "that name is not in your contacts" - a defect in this harness
        # that looked like a defect in the product.
        session = Session(contacts=CONTACTS)
        recipe = rows[-1].get("recipe", {}) or {}
        shape = str(recipe.get("shape", "plain"))
        should_complete = bool(recipe.get("completes"))
        said_yes = bool(recipe.get("affirmative"))

        for row in rows:
            session.handle(str(row["text"]))
        completed += 1
        executed = session.state is State.EXECUTED
        # A transfer the behavioural layer escalated needs the stronger word, and a plain
        # "haan" is correctly refused. That is the product working, not a thread failing.
        stepped_up = bool(getattr(session, "stepped_up", False)) and not executed

        if executed and not should_complete:
            outcome = "executed_when_it_should_not"
            executed_after_no += 1
        elif not should_complete:
            # Two different right answers, and lumping them together mislabelled every
            # "no" as a refusal over an unknown payee.
            if said_yes:
                outcome = "correctly_refused_unknown_payee"
                correctly_refused_unknown += 1
            else:
                outcome = "correctly_stopped_on_a_no"
                correctly_stopped += 1
            handled += 1
        elif executed:
            outcome = "executed_after_a_yes"
            executed_after_yes += 1
            handled += 1
        elif stepped_up:
            outcome = "asked_to_strengthen"
            asked_to_strengthen += 1
            handled += 1
        else:
            outcome = "a_yes_that_did_not_execute"
            unfinished += 1
            if len(examples) < 20:
                examples.append(
                    {
                        "thread": name,
                        "shape": shape,
                        "turns": [row["text"] for row in rows],
                        "ended_in": str(session.state),
                    }
                )

        tally = by_shape.setdefault(shape, {"threads": 0, "handled": 0})
        tally["threads"] += 1
        tally["handled"] += int(
            outcome != "a_yes_that_did_not_execute" and outcome != "executed_when_it_should_not"
        )

    yes_threads = executed_after_yes + asked_to_strengthen + unfinished
    return {
        "threads": completed,
        "executed_after_a_yes": executed_after_yes,
        "executed_after_a_no": executed_after_no,
        "a_yes_that_did_not_execute": unfinished,
        "a_yes_the_layer_asked_to_strengthen": asked_to_strengthen,
        "correctly_refused_because_the_payee_is_unknown": correctly_refused_unknown,
        "correctly_stopped_because_the_reply_was_no": correctly_stopped,
        # Of the threads that could complete and said yes, how many did.
        "completion_rate": round(executed_after_yes / yes_threads, 4) if yes_threads else None,
        # Counted while deciding each thread, not derived afterwards by subtracting three
        # tallies from the total - which is the arithmetic that quietly double-counts.
        "handled_correctly": handled,
        "handled_correctly_rate": round(handled / completed, 4) if completed else None,
        "by_shape": {
            name: {
                **entry,
                "handled_rate": round(entry["handled"] / entry["threads"], 4)
                if entry["threads"]
                else None,
            }
            for name, entry in sorted(by_shape.items())
        },
        "examples_of_a_yes_that_did_not_execute": examples,
        "reading": (
            "executed_after_a_no is the number that has to be zero: it would mean a "
            "refusal, or a payee the book does not have, was read as consent. A yes the "
            "layer asked to strengthen is the step-up working. A yes that did not execute "
            "and was not stepped up is the real miss, and by_shape says which kind of "
            "conversation it was."
        ),
    }


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
    outside = {"cases": 0, "caught": 0}
    by_obfuscation: dict[str, dict[str, int]] = {}
    by_script: dict[str, dict[str, int]] = {}
    structuring_legs: list[dict[str, object]] = []
    thread_turns: dict[str, list[dict[str, object]]] = {}
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

        # --------------------------------------------------- splits and whole threads
        if row["family"] == "structuring":
            structuring_legs.append(row)
        elif row["family"] == "thread":
            thread_turns.setdefault(str(row["thread_id"]), []).append(row)

        # ------------------------------------------------------------------ coercion
        if row["family"] == "coercion":
            coercion_rows["cases"] += 1
            assessment = coercion.assess(text)
            caught = bool(assessment.families)
            coercion_rows["blocked"] += int(caught)

            # Scam types the product has no vocabulary for at all. Reported separately,
            # because mixing them into the catch rate would let a product look better by
            # having fewer kinds of scam in its test set.
            if row.get("outside_the_vocabulary"):
                outside["cases"] += 1
                outside["caught"] += int(caught)

            technique = str(row.get("obfuscation") or "")
            if technique:
                bucket = by_obfuscation.setdefault(technique, {"cases": 0, "caught": 0})
                bucket["cases"] += 1
                bucket["caught"] += int(caught)

            script = str(row.get("language", ""))
            if script in {"urdu", "urdu_arabic_codepoints"}:
                bucket = by_script.setdefault(script, {"cases": 0, "caught": 0})
                bucket["cases"] += 1
                bucket["caught"] += int(caught)

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

    splits = _score_structuring(structuring_legs)
    threads = _score_threads(thread_turns)

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
            # The headline above includes the scam types the product has no words for,
            # which drags it down by design. These two say which part is which, because
            # "0.87 of scam scripts" averages a family it knows with one it has never
            # heard of and tells you about neither.
            "catch_rate_within_the_vocabulary": _rate(
                coercion_rows["blocked"] - outside["caught"],
                coercion_rows["cases"] - outside["cases"],
            ),
            "catch_rate_outside_the_vocabulary": _rate(outside["caught"], outside["cases"]),
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
        "scam_types_with_no_vocabulary": {
            **outside,
            "catch_rate": _rate(outside["caught"], outside["cases"]),
            "reading": (
                "Job offers, QR swaps, SIM swaps and refund-overpayment scripts. The "
                "product has no words for any of them, so this rate is the blind spot "
                "measured rather than asserted - and a generator cannot close it, "
                "because writing the missing words would be writing the answer key."
            ),
        },
        "obfuscated_security_words": {
            name: {
                **entry,
                "catch_rate": _rate(entry["caught"], entry["cases"]),
            }
            for name, entry in sorted(by_obfuscation.items())
        },
        "urdu_script_variants": {
            name: {
                **entry,
                "catch_rate": _rate(entry["caught"], entry["cases"]),
            }
            for name, entry in sorted(by_script.items())
        },
        "structuring": splits,
        "threads": threads,
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
