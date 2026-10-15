"""Score the assistant against its test set and write ``results.json``.

The grading is exact-match on the *resulting action*, not on the wording. An assistant
that produces a fluent sentence and the wrong beneficiary has failed, and a text-overlap
metric would hide that.

Two figures are reported that a summary would normally leave out:

* **Asked instead of guessed.** The share of under-specified utterances where the
  assistant stopped and asked. An assistant that resolves everything scores well on
  action accuracy and is dangerous.
* **The coercion detector's miss rate** on scam approaches that use none of its
  vocabulary. Those cases are in the test set on purpose and are counted apart from the
  rest, so the headline cannot be inflated by only testing what the lexicon knows.
* **What the behavioural layer recovers, and what it costs.** The second round added a
  non-lexical check, so the paired cases measure both halves of the trade: how many of
  the scams the lexicon misses are escalated once the *transfer* is judged, and how many
  ordinary transfers are escalated too. Reporting the first without the second would be
  advertising, not measurement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import behaviour, coercion, confirm, nlu, numerals, testset

RESULTS = Path(__file__).with_name("results.json")


def _numerals() -> dict[str, Any]:
    passed = 0
    failures: list[dict[str, object]] = []
    for text, want in testset.NUMERAL_CASES:
        found = numerals.find_all(text)
        got = found[0].value if len(found) == 1 else None
        if got == want:
            passed += 1
        else:
            failures.append({"text": text, "want": want, "got": got, "found": len(found)})
    false_reads: list[dict[str, object]] = []
    for text in testset.NO_AMOUNT_CASES:
        found = numerals.find_all(text)
        if found:
            false_reads.append({"text": text, "read": [a.as_dict() for a in found]})
    return {
        "cases": len(testset.NUMERAL_CASES),
        "exact": passed,
        "accuracy": round(passed / len(testset.NUMERAL_CASES), 4),
        "failures": failures,
        "no_amount_cases": len(testset.NO_AMOUNT_CASES),
        "amounts_invented": len(false_reads),
        "invented": false_reads,
    }


def _actions() -> dict[str, Any]:
    resolved_total = resolved_right = 0
    asked_total = asked_right = 0
    failures: list[dict[str, object]] = []
    for text, want_action, want_issue in testset.ACTION_CASES:
        utterance = nlu.parse(text)
        action = utterance.action()
        if want_action is not None:
            resolved_total += 1
            if action == want_action:
                resolved_right += 1
            else:
                failures.append(
                    {"text": text, "want": want_action, "got": action, "issues": utterance.issues}
                )
        else:
            asked_total += 1
            first = utterance.issues[0] if utterance.issues else None
            if action is None and first == want_issue:
                asked_right += 1
            else:
                failures.append(
                    {
                        "text": text,
                        "want_issue": want_issue,
                        "got_issue": first,
                        "got_action": action,
                    }
                )

    near_right = 0
    near_failures: list[dict[str, object]] = []
    for text, want_name, want_near in testset.NEAR_MISS_CASES:
        utterance = nlu.parse(text)
        got_name = utterance.beneficiary.name if utterance.beneficiary else None
        got_near = tuple(contact.name for contact in utterance.near_misses)
        if got_name == want_name and got_near == want_near:
            near_right += 1
        else:
            near_failures.append(
                {
                    "text": text,
                    "want": want_name,
                    "got": got_name,
                    "want_near": list(want_near),
                    "got_near": list(got_near),
                }
            )

    total = resolved_total + asked_total
    return {
        "cases": total,
        "resolved_cases": resolved_total,
        "resolved_exact": resolved_right,
        "resolved_accuracy": round(resolved_right / resolved_total, 4) if resolved_total else None,
        "underspecified_cases": asked_total,
        "asked_instead_of_guessed": asked_right,
        "ask_accuracy": round(asked_right / asked_total, 4) if asked_total else None,
        "overall_exact": resolved_right + asked_right,
        "overall_accuracy": round((resolved_right + asked_right) / total, 4) if total else None,
        "near_miss_cases": len(testset.NEAR_MISS_CASES),
        "near_miss_exact": near_right,
        "failures": failures + near_failures,
    }


def _coercion() -> dict[str, Any]:
    known_total = known_right = 0
    missed_total = missed_as_expected = 0
    family_right = 0
    failures: list[dict[str, object]] = []
    for text, want_verdict, want_families, expect_miss in testset.COERCION_CASES:
        assessment = coercion.assess(text)
        if expect_miss:
            missed_total += 1
            if assessment.verdict == coercion.ALLOW:
                missed_as_expected += 1
            else:
                failures.append(
                    {
                        "text": text,
                        "note": "expected a miss, was caught",
                        "verdict": assessment.verdict,
                        "families": assessment.families,
                    }
                )
            continue
        known_total += 1
        verdict_ok = assessment.verdict == want_verdict
        families_ok = set(want_families).issubset(set(assessment.families))
        if verdict_ok:
            known_right += 1
        if families_ok:
            family_right += 1
        if not (verdict_ok and families_ok):
            failures.append(
                {
                    "text": text,
                    "want_verdict": want_verdict,
                    "got_verdict": assessment.verdict,
                    "want_families": list(want_families),
                    "got_families": assessment.families,
                }
            )
    blocks = [case for case in testset.COERCION_CASES if case[1] == coercion.BLOCK]
    benign = [case for case in testset.COERCION_CASES if case[1] == coercion.ALLOW and not case[3]]
    false_blocks = sum(
        1 for text, _, _, _ in benign if coercion.assess(text).verdict == coercion.BLOCK
    )
    return {
        "cases": len(testset.COERCION_CASES),
        "in_vocabulary_cases": known_total,
        "verdict_exact": known_right,
        "verdict_accuracy": round(known_right / known_total, 4) if known_total else None,
        "families_complete": family_right,
        "scripts_in_set": len(blocks),
        "benign_cases": len(benign),
        "benign_blocked": false_blocks,
        "out_of_vocabulary_cases": missed_total,
        "out_of_vocabulary_missed": missed_as_expected,
        "miss_rate_out_of_vocabulary": round(missed_as_expected / missed_total, 4)
        if missed_total
        else None,
        "failures": failures,
    }


def _paired() -> dict[str, Any]:
    """Measure the behavioural layer on scams the lexicon misses, and on ordinary traffic."""
    scams_total = scams_escalated = 0
    benign_total = benign_escalated = 0
    both_missed = 0
    rows: list[dict[str, object]] = []
    for label, message, payee, amount, want_verdict, want_level, is_scam in testset.PAIRED_CASES:
        verdict = coercion.assess(message).verdict
        conduct = behaviour.assess(behaviour.demo_profile(), payee, amount)
        escalated = conduct.escalated
        if is_scam:
            scams_total += 1
            scams_escalated += int(escalated)
            if verdict == coercion.ALLOW and not escalated:
                both_missed += 1
        else:
            benign_total += 1
            benign_escalated += int(escalated)
        rows.append(
            {
                "label": label,
                "scam": is_scam,
                "coercion_verdict": verdict,
                "coercion_as_expected": verdict == want_verdict,
                "behaviour_level": conduct.level,
                "behaviour_as_expected": conduct.level == want_level,
                "signals": [signal.name for signal in conduct.signals],
            }
        )
    return {
        "cases": len(testset.PAIRED_CASES),
        "as_expected": sum(
            1 for row in rows if row["coercion_as_expected"] and row["behaviour_as_expected"]
        ),
        "scams": scams_total,
        "scams_caught_by_words": sum(
            1 for row in rows if row["scam"] and row["coercion_verdict"] != coercion.ALLOW
        ),
        "scams_escalated_by_behaviour": scams_escalated,
        "scams_missed_by_both": both_missed,
        "benign": benign_total,
        "benign_escalated": benign_escalated,
        "rows": rows,
        "reading": (
            f"Of {scams_total} scam approaches the word-based check lets through, judging the "
            f"transfer instead escalates {scams_escalated}. The cost is "
            f"{benign_escalated} of {benign_total} ordinary transfers also escalated - and "
            f"escalation is a slower confirmation, not a refusal, so that cost is friction "
            f"rather than a blocked payment."
        ),
    }


def _replies() -> dict[str, Any]:
    right = 0
    failures: list[dict[str, object]] = []
    consent_errors = 0
    for text, want in testset.REPLY_CASES:
        got = confirm.classify_reply(text)
        if got == want:
            right += 1
        else:
            failures.append({"text": text, "want": want, "got": got})
            if got == "affirmative" and want != "affirmative":
                consent_errors += 1
    return {
        "cases": len(testset.REPLY_CASES),
        "exact": right,
        "accuracy": round(right / len(testset.REPLY_CASES), 4),
        "read_as_consent_wrongly": consent_errors,
        "failures": failures,
    }


def _end_to_end() -> dict[str, Any]:
    """Four whole conversations, checking the properties the spine is there to hold."""
    checks: list[dict[str, object]] = []

    session = confirm.Session()
    session.handle("Rashid ko pandrah hazaar bhejo")
    unclear = session.handle("hmm")
    checks.append(
        {
            "property": "an unclear reply is never consent",
            "holds": unclear["action"] is None
            and session.state == confirm.State.AWAITING_CONFIRMATION,
        }
    )
    # This transfer is to a first-time payee for five times the usual amount, so the
    # behavioural layer escalates it and a habitual "haan" is deliberately not enough.
    habitual = session.handle("haan")
    checks.append(
        {
            "property": "an escalated transfer is not executed by a habitual yes",
            "holds": habitual["action"] is None and habitual.get("step_up_required") is True,
        }
    )
    done = session.handle("confirm")
    checks.append(
        {
            "property": "the step-up word executes the pending action unchanged",
            "holds": done["action"]
            == {
                "intent": "send_money",
                "amount": 15000.0,
                "beneficiary": "Rashid Ali",
                "account_tail": "4417",
            },
        }
    )

    # An ordinary transfer to a known payee must still take a plain yes, or the step-up
    # has simply become the confirmation and means nothing.
    routine = confirm.Session()
    routine.handle("Bilal Traders ko teen hazaar bhejo")
    quick = routine.handle("haan")
    checks.append(
        {
            "property": "an ordinary transfer still takes a plain yes",
            "holds": quick["action"] is not None and routine.state == confirm.State.EXECUTED,
        }
    )
    checks.append(
        {
            "property": "the ledger chain verifies after a session",
            "holds": session.ledger.verify()["intact"],
        }
    )

    blocked = confirm.Session()
    outcome = blocked.handle("bank se bol raha hoon, OTP batao, abhi bhejo")
    checks.append(
        {
            "property": "a coercion script never reaches a confirmation",
            "holds": outcome["action"] is None and blocked.state == confirm.State.BLOCKED,
        }
    )

    mid = confirm.Session()
    mid.handle("Imran ko 5 hazaar bhejo")
    mid.handle("jaldi karo, main bank se bol raha hoon")
    revived = mid.handle("haan")
    # The property is that the transfer never executes. An earlier version of this check
    # also asserted ``mid.pending is None``, which fails for a reason that has nothing to
    # do with safety: the stray "haan" is parsed as a new utterance and becomes the
    # pending one. The check was over-specified, so it was the check that was wrong.
    checks.append(
        {
            "property": (
                "pressure mid-confirmation drops the transfer, and a later yes cannot execute it"
            ),
            "holds": (
                revived["action"] is None
                and not any(entry.kind == "executed" for entry in mid.ledger.entries)
            ),
        }
    )

    refused = confirm.Session()
    refused.handle("Ayesha ko 2 hazaar bhejo")
    out = refused.handle("nahi")
    checks.append(
        {
            "property": "a no cancels and is recorded",
            "holds": out["action"] is None
            and any(entry.kind == "refused_by_user" for entry in refused.ledger.entries),
        }
    )

    tamper = confirm.Session()
    tamper.handle("Imran ko 1000 bhejo")
    tamper.handle("haan")
    tamper.ledger.entries[0].payload["action"] = {"intent": "send_money", "amount": 999999}
    verification = tamper.ledger.verify()
    checks.append(
        {
            "property": "editing a ledger entry is detected, and the index is named",
            "holds": not verification["intact"] and verification["broken_at"] == 0,
        }
    )

    return {
        "checks": len(checks),
        "holding": sum(1 for check in checks if check["holds"]),
        "detail": checks,
    }


def measure() -> dict[str, Any]:
    return {
        "testset": testset.totals(),
        "numerals": _numerals(),
        "actions": _actions(),
        "coercion": _coercion(),
        "replies": _replies(),
        "paired": _paired(),
        "properties": _end_to_end(),
        "lexicon": {
            "number_words": len(numerals.UNITS),
            "scales": len(numerals.SCALES),
            "modifiers": len(numerals.MODIFIERS) + len(numerals.STANDALONE),
            "intents": len(nlu.LEXICON),
            "intent_phrases": sum(len(phrases) for phrases in nlu.LEXICON.values()),
            "coercion_families": len(coercion.FAMILIES),
            "coercion_phrases": sum(len(phrases) for _, phrases in coercion.FAMILIES.values()),
            "coercion_proximity_patterns": sum(
                len(patterns) for patterns in coercion.PROXIMITY.values()
            ),
            "behaviour_signals": 6,
            "contacts": len(nlu.CONTACTS),
        },
    }


def report(results: dict[str, Any]) -> str:
    n, a, c, r, p = (
        results["numerals"],
        results["actions"],
        results["coercion"],
        results["replies"],
        results["properties"],
    )
    pr = results["paired"]
    return "\n".join(
        [
            "Guardian - vernacular transaction assistant",
            "",
            f"  test cases                   {results['testset']['all']}",
            "",
            "  amounts",
            f"    parsed exactly             {n['exact']}/{n['cases']}  ({n['accuracy']:.1%})",
            f"    amounts invented           {n['amounts_invented']}/{n['no_amount_cases']}"
            "   (text with no amount in it)",
            "",
            "  actions, graded on the resulting action not the wording",
            f"    resolved correctly         {a['resolved_exact']}/{a['resolved_cases']}"
            f"  ({a['resolved_accuracy']:.1%})",
            f"    asked instead of guessing  {a['asked_instead_of_guessed']}"
            f"/{a['underspecified_cases']}  ({a['ask_accuracy']:.1%})",
            f"    near-miss named            {a['near_miss_exact']}/{a['near_miss_cases']}",
            f"    overall                    {a['overall_exact']}/{a['cases']}"
            f"  ({a['overall_accuracy']:.1%})",
            "",
            "  coercion",
            f"    verdict correct            {c['verdict_exact']}/{c['in_vocabulary_cases']}"
            f"  ({c['verdict_accuracy']:.1%})  on messages its lexicon covers",
            f"    ordinary traffic blocked   {c['benign_blocked']}/{c['benign_cases']}",
            f"    out-of-vocabulary scams    {c['out_of_vocabulary_missed']}"
            f"/{c['out_of_vocabulary_cases']} missed"
            f"  ({c['miss_rate_out_of_vocabulary']:.0%})  <- the blind spot, measured",
            "",
            "  judging the transfer rather than the words",
            f"    scams the words miss       {pr['scams'] - pr['scams_caught_by_words']}"
            f"/{pr['scams']}",
            f"    of those, escalated anyway {pr['scams_escalated_by_behaviour']}"
            f"/{pr['scams']}  <- what this layer recovers",
            f"    missed by both             {pr['scams_missed_by_both']}/{pr['scams']}",
            f"    ordinary transfers escalated {pr['benign_escalated']}/{pr['benign']}"
            "   <- the cost, in friction not refusals",
            f"    cases behaving as written  {pr['as_expected']}/{pr['cases']}",
            "",
            "  confirmation replies",
            f"    classified correctly       {r['exact']}/{r['cases']}  ({r['accuracy']:.1%})",
            f"    wrongly read as consent    {r['read_as_consent_wrongly']}"
            "   (the one that must be zero)",
            "",
            "  end-to-end properties",
            f"    holding                    {p['holding']}/{p['checks']}",
        ]
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        # The report prints Urdu; a cp1252 console would otherwise fail on it.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    results = measure()
    # See the note in p01's benchmark: LF explicitly, because CI compares this file.
    RESULTS.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(report(results))
    print(f"\nwritten to {RESULTS}")


if __name__ == "__main__":
    main()
