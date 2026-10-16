"""Project 01 against the whole corpus: 120,120 messages, 50,960 lifecycles.

``benchmark.py`` checks the 14 shipped scenarios: 33 messages and 14 flows, built from
one fixed context. It answers "do the rules fire on the cases they were written for".
This answers the question that actually matters for a switch - "do they fire on traffic
nobody curated" - by running the same 22 invariants over 50,960 lifecycles whose PANs,
amounts, currencies, terminals, merchants and timestamps are all drawn at random.

Four passes:

1. **Codec, every message.** Pack, re-parse, pack again, and require the text to be
   identical; the same in binary framing; and the character-class check over every
   field. 120,120 messages, no sampling.
2. **Invariants, every lifecycle.** All 50,960 are valid by construction, so *every*
   finding is a false positive and the count has to be zero. This is the pass that finds
   rules which are accidentally specific to the one context the scenarios ship with.
3. **Fuzz, widened.** 60,000 single-character corruptions over an ASCII pool built from
   50 random contexts and 30,000 single-byte corruptions over the binary pool, against
   the 2,000 and 1,000 trials over a 10-message pool that the shipped benchmark runs.
4. **Mutation, at every amount.** The 38 curated faults, re-applied to flows built from
   200 random contexts rather than one, so a detector that only works at the default
   amount of 5,000.00 is exposed.

Committed to ``results_datasets.json``, which CI does not re-run: the corpus is 128 MB.
"""

from __future__ import annotations

import json
import pathlib
import time

from shared import datasets

from . import binary, corpus, lifecycle, mutations, scenarios
from .codec import CodecError, check_values, pack, unpack

RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"

ASCII_FUZZ_TRIALS = 60_000
BINARY_FUZZ_TRIALS = 30_000
FUZZ_CONTEXTS = 50
MUTATION_CONTEXTS = 200


def codec_pass(limit: int | None = None) -> dict[str, object]:
    """Round-trip every message in the corpus, in both framings."""
    checked = ascii_stable = binary_stable = reparse_equal = 0
    class_violations: list[dict[str, object]] = []
    framing_detected: dict[str, int] = {}
    broken: list[dict[str, object]] = []

    for row in corpus.read(limit=limit):
        checked += 1
        message = corpus.message_from_row(row)
        try:
            text = pack(message)
            again = pack(unpack(text))
            ascii_stable += int(text == row["ascii"])
            reparse_equal += int(again == text)
            raw = binary.pack_binary(message)
            binary_stable += int(binary.unpack_binary(raw).fields == message.fields)
            if checked % 1000 == 0:
                # Detection is the expensive one, so it is sampled rather than skipped.
                name = str(binary.detect(text).get("framing", "?"))
                framing_detected[name] = framing_detected.get(name, 0) + 1
        except CodecError as exc:
            if len(broken) < 10:
                broken.append({"flow": row["flow_id"], "step": row["step"], "error": str(exc)})
            continue
        problems = check_values(message.fields)
        if problems and len(class_violations) < 10:
            class_violations.append(
                {"flow": row["flow_id"], "scenario": row["scenario"], "problems": problems}
            )

    return {
        "messages_checked": checked,
        "ascii_round_trip_stable": ascii_stable,
        "reparse_equal": reparse_equal,
        "binary_round_trip_stable": binary_stable,
        "messages_that_failed_to_pack": len(broken),
        "examples_that_failed": broken,
        "character_class_violations": len(class_violations),
        "examples_of_violations": class_violations,
        "framing_detected_on_a_sample": framing_detected,
        "reading": (
            "Every one of these messages was built by the packer, so a round-trip that "
            "is not stable is a defect in the codec and not in the corpus. The count to "
            "look at is messages_that_failed_to_pack, which has to be zero."
        ),
    }


def invariant_pass(flows: int = corpus.DEFAULT_FLOWS) -> dict[str, object]:
    """Run the 22 invariants over every generated lifecycle. All of them are valid."""
    checked = 0
    with_findings = 0
    by_rule: dict[str, int] = {}
    examples: list[dict[str, object]] = []
    by_scenario: dict[str, int] = {}

    for item in corpus.flows(flows):
        checked += 1
        findings = lifecycle.check(item.flow)
        if findings:
            with_findings += 1
            by_scenario[item.flow.key] = by_scenario.get(item.flow.key, 0) + 1
            for finding in findings:
                by_rule[finding.rule] = by_rule.get(finding.rule, 0) + 1
                if len(examples) < 20:
                    examples.append(
                        {
                            "flow": item.flow_id,
                            "scenario": item.flow.key,
                            "rule": finding.rule,
                            "detail": finding.detail,
                        }
                    )

    return {
        "rules": len(lifecycle.RULES) if hasattr(lifecycle, "RULES") else None,
        "flows_checked": checked,
        "flows_with_findings": with_findings,
        "false_positive_rate": round(with_findings / checked, 6) if checked else None,
        "findings_by_rule": dict(sorted(by_rule.items())),
        "flows_with_findings_by_scenario": dict(sorted(by_scenario.items())),
        "examples": examples,
        "reading": (
            "Every flow here is valid by construction, so flows_with_findings is a count "
            "of false positives and has to be zero. The shipped benchmark checks 14 "
            "flows; this checks "
            f"{checked:,}."
        ),
    }


def mutation_pass(contexts: int = MUTATION_CONTEXTS) -> dict[str, object]:
    """Re-apply the curated faults to flows built from many random contexts."""
    import random

    rng = random.Random(4242)
    totals = {"mutants": 0, "killed": 0, "by_intended": 0}
    survivors: list[dict[str, object]] = []
    per_mutator: dict[str, dict[str, int]] = {}

    for _ in range(contexts):
        context = corpus.random_context(rng)
        for mutant in mutations.generate(context):
            detection = mutations.detect(mutant)
            totals["mutants"] += 1
            totals["killed"] += int(detection.killed)
            totals["by_intended"] += int(mutant.expected in detection.by)
            row = per_mutator.setdefault(
                mutant.mutator, {"mutants": 0, "killed": 0, "by_intended": 0}
            )
            row["mutants"] += 1
            row["killed"] += int(detection.killed)
            row["by_intended"] += int(mutant.expected in detection.by)
            if not detection.killed and len(survivors) < 20:
                survivors.append(
                    {
                        "mutator": mutant.mutator,
                        "scenario": mutant.scenario,
                        "expected": mutant.expected,
                        "amount": context.amount,
                        "currency": context.currency,
                    }
                )

    weakest = sorted(
        (
            {
                "mutator": name,
                "kill_rate": round(row["killed"] / row["mutants"], 4),
                "by_intended_rate": round(row["by_intended"] / row["mutants"], 4),
                "mutants": row["mutants"],
            }
            for name, row in per_mutator.items()
        ),
        key=lambda entry: (entry["kill_rate"], entry["by_intended_rate"]),
    )[:8]

    return {
        "contexts": contexts,
        "mutants": totals["mutants"],
        "killed": totals["killed"],
        "kill_rate": round(totals["killed"] / totals["mutants"], 4) if totals["mutants"] else None,
        "killed_by_the_intended_rule": totals["by_intended"],
        "by_intended_rate": (
            round(totals["by_intended"] / totals["mutants"], 4) if totals["mutants"] else None
        ),
        "weakest_detectors": weakest,
        "survivors": survivors,
        "reading": (
            "The shipped benchmark applies each of these 38 faults once, to a flow built "
            "from one context. Here each is applied "
            f"{contexts} times at different amounts, currencies, PANs and terminals. A "
            "detector that depended on the default amount shows up as a kill rate below "
            "1.0 for its own mutator."
        ),
    }


def run(verbose: bool = True) -> dict[str, object]:
    def say(text: str) -> None:
        if verbose:
            print(text, flush=True)

    started = time.perf_counter()
    results: dict[str, object] = {}

    say("=== codec: every message in the corpus, both framings")
    results["codec"] = codec_pass()

    say("=== invariants: every lifecycle")
    results["invariants"] = invariant_pass()

    say(f"=== fuzz: {ASCII_FUZZ_TRIALS:,} ASCII trials over {FUZZ_CONTEXTS} contexts")
    every_scenario = tuple(s.key for s in scenarios.SCENARIOS)
    results["fuzz_ascii"] = mutations.fuzz(
        trials=ASCII_FUZZ_TRIALS, contexts=FUZZ_CONTEXTS, scenarios=every_scenario
    )

    say(f"=== fuzz: {BINARY_FUZZ_TRIALS:,} binary trials")
    results["fuzz_binary"] = mutations.fuzz_binary(
        trials=BINARY_FUZZ_TRIALS,
        contexts=FUZZ_CONTEXTS,
        scenarios=every_scenario,
    )

    say(f"=== mutation: 38 faults over {MUTATION_CONTEXTS} contexts")
    results["mutation"] = mutation_pass()

    results["setup"] = {
        "corpus": datasets.load_manifest().get(corpus.KEY, {"note": "not in the manifest yet"}),
        "seconds": round(time.perf_counter() - started, 1),
        "why_this_is_a_separate_file": (
            "The corpus is 128 MB of generated JSON lines, so CI cannot re-derive these "
            "numbers in a checkout. results.json stays small and reproducible."
        ),
    }
    RESULTS.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    say(f"wrote {RESULTS.name} in {results['setup']['seconds']}s")
    return results


if __name__ == "__main__":
    run()
