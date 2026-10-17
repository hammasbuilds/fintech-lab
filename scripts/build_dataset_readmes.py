"""Write each project's at-scale section into its README, from the committed results.

    uv run python scripts/build_dataset_readmes.py

Every project is measured twice: on a small reproducible run that CI re-derives, and on
a dataset of between 50,000 and a million rows that CI cannot download. The second set of
numbers moves whenever a dataset is rebuilt, so the section between the ``datasets``
markers is generated from ``results_datasets.json`` and ``data/MANIFEST.json`` rather than
typed. The prose around the markers is written by hand and never touched.

The root README gets the summary table by the same route.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.datasets import load_manifest  # noqa: E402

START = "<!-- datasets:start -->"
END = "<!-- datasets:end -->"


def read(folder: str) -> dict:
    path = ROOT / "projects" / folder / "results_datasets.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def dig(data: object, path: str, default: object = None) -> object:
    current = data
    for step in path.split("."):
        if not isinstance(current, dict) or step not in current:
            return default
        current = current[step]
    return current


def num(value: object) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def table(rows: list[tuple[str, str, str]], headers: tuple[str, str, str]) -> list[str]:
    out = [f"| {headers[0]} | {headers[1]} | {headers[2]} |", "| --- | ---: | --- |"]
    for label, value, over in rows:
        out.append(f"| {label} | **{value}** | {over} |")
    return out


def dataset_lines(keys: list[str]) -> list[str]:
    manifest = load_manifest()
    lines = ["| Dataset | Rows | Size | Role | Source |", "| --- | ---: | ---: | --- | --- |"]
    for key in keys:
        entry = manifest.get(key)
        if not entry:
            lines.append(f"| `{key}` | not built | | | |")
            continue
        size = int(entry["bytes"]) / (1024 * 1024)
        lines.append(
            f"| `{key}` | {entry['rows']:,} | {size:,.0f} MB | "
            f"{entry.get('role', 'cohort')} | {entry.get('source', '')} |"
        )
    return lines


def section_01() -> list[str]:
    data = read("p01_switchboard")
    lines = dataset_lines(["generated/p01_messages"])
    lines += [""]
    lines += table(
        [
            (
                "messages packed, re-parsed and packed again",
                num(dig(data, "codec.ascii_round_trip_stable")),
                f"of {num(dig(data, 'codec.messages_checked'))} - no sampling",
            ),
            (
                "binary framing round trip",
                num(dig(data, "codec.binary_round_trip_stable")),
                f"of {num(dig(data, 'codec.messages_checked'))}",
            ),
            (
                "lifecycles checked against 22 invariants",
                num(dig(data, "invariants.flows_checked")),
                "all valid by construction",
            ),
            (
                "false positives",
                num(dig(data, "invariants.flows_with_findings")),
                "every finding here would be one",
            ),
            (
                "illegal characters rejected",
                num(dig(data, "fuzz_ascii.groups.class_violating.rejection_rate")),
                f"of {num(dig(data, 'fuzz_ascii.groups.class_violating.trials'))} trials",
            ),
            (
                "framing damage rejected",
                num(dig(data, "fuzz_ascii.groups.framing.rejection_rate")),
                f"of {num(dig(data, 'fuzz_ascii.groups.framing.trials'))} trials",
            ),
            (
                "binary damage rejected",
                num(dig(data, "fuzz_binary.rejection_rate")),
                f"of {num(dig(data, 'fuzz_binary.trials'))} trials",
            ),
            (
                "mutants killed by the rule written for them",
                num(dig(data, "mutation.by_intended_rate")),
                f"{num(dig(data, 'mutation.mutants'))} mutants over "
                f"{num(dig(data, 'mutation.contexts'))} random contexts",
            ),
        ],
        ("At full size", "Result", "Over"),
    )
    lines += [
        "",
        "The corpus now carries advice, reconciliation and chargeback traffic as well as "
        "authorisation, and transactions in currencies with **three different minor-unit "
        "exponents** - 0 for yen, 2 for rupees, 3 for the Gulf dinars. Adding them found "
        "four defects at once: the dictionary had no reconciliation elements, so a 0500 "
        "could not be packed; function digit 5 was missing from the MTI table, so a "
        "chargeback had no response; request/response pairing ignored notifications, so "
        "every 0440 matched nothing; and the rule that every request is answered had no "
        "concept of an advice, so the one scenario where the issuer is unreachable "
        "reported itself as broken.",
        "",
        "The mutation row is the one worth reading twice. Against the 14 shipped scenarios "
        "the 38 curated faults are killed 38 times out of 38. Re-injected into flows with "
        "random currencies, the first run scored 0.9725 - and the two detectors that missed "
        "were right every time. Both currency mutators wrote a literal `840`, which is no "
        "fault at all when the transaction is already in dollars: a mutant that does not "
        "mutate is scored as a survivor and is not one. The mutators now derive a currency "
        "that differs from the message, and the rate is back to 1.0000 over 7,600 mutants.",
    ]
    return lines


V1 = "loan_book.policies.real_v1_scoregate"


def section_02() -> list[str]:
    data = read("p02_policy_bench")
    lines = dataset_lines(["real/loan_book", "real/card_applications", "real/card_credit_records"])
    lines += [""]
    lines += table(
        [
            (
                "base default rate",
                num(dig(data, "loan_book.cohort.base_default_rate")),
                f"{num(dig(data, 'loan_book.cohort.defaults'))} of "
                f"{num(dig(data, 'loan_book.cohort.rows'))} applications",
            ),
            (
                "v1 approval rate",
                num(dig(data, "loan_book.policies.real_v1_scoregate.backtest.approval_rate")),
                "the whole book",
            ),
            (
                "v1 bad rate among approvals",
                num(dig(data, "loan_book.policies.real_v1_scoregate.backtest.bad_rate")),
                f"{num(dig(data, V1 + '.backtest.approvals'))} approvals",
            ),
            (
                "v1 Gini",
                num(dig(data, "loan_book.policies.real_v1_scoregate.discrimination.gini")),
                f"{num(dig(data, V1 + '.discrimination.scored'))} scored",
            ),
            (
                "v2 Gini, price signal removed",
                num(dig(data, "loan_book.policies.real_v2_no_price.discrimination.gini")),
                "same book, one rule dropped",
            ),
            (
                "v1 net, one-off 2% fee",
                num(dig(data, "loan_book.policies.real_v1_scoregate.backtest.net")),
                "negative at every cutoff",
            ),
            (
                "v3 net, priced over the term",
                num(dig(data, "loan_book.policies.real_v3_priced.backtest.net")),
                "and flagged critical by the compliance lint",
            ),
        ],
        ("The real loan book", "Result", "Over"),
    )
    lines += [""]
    lines += table(
        [
            (
                "applications scored",
                num(dig(data, "card_book.cohort.applications")),
                "every outcome figure withheld",
            ),
            (
                "joined to a repayment history",
                num(dig(data, "card_book.cohort.joined_with_history")),
                f"a join rate of {num(dig(data, 'card_book.cohort.join_rate'))}",
            ),
            (
                "pensioners caught by the 365243 sentinel",
                num(dig(data, "card_book.cohort.pensioners_flagged")),
                "would otherwise read as -12,175 months of work",
            ),
            (
                "Gini, bad = ever 30+ days past due",
                num(dig(data, "card_book.definitions.ever_30_plus_dpd.discrimination.gini")),
                "the 36,457-row slice",
            ),
            (
                "Gini, bad = ever 60+ days past due",
                num(dig(data, "card_book.definitions.ever_60_plus_dpd.discrimination.gini")),
                "the same 36,457 rows",
            ),
        ],
        ("The real card book", "Result", "Over"),
    )
    lines += [""]
    lines += table(
        [
            (
                "PSI on the score, early window against late",
                num(dig(data, "over_time.population_stability.psi")),
                str(dig(data, "over_time.population_stability.reading", "")),
            ),
            (
                "PSI on other-app exposure",
                num(dig(data, "over_time.characteristic_stability.other_app_outstanding.psi")),
                "the same two windows, one input",
            ),
            (
                "net per loan the early window promised",
                num(dig(data, "over_time.out_of_time.net_per_loan_promised")),
                "cutoff chosen on the early months of the window",
            ),
            (
                "net per loan it delivered later",
                num(dig(data, "over_time.out_of_time.net_per_loan_delivered")),
                "the same cutoff, months 18-23",
            ),
            (
                "optimism per loan",
                num(dig(data, "over_time.out_of_time.optimism_per_loan")),
                "negative means the later book did better",
            ),
            (
                "share of the population with no observable outcome",
                num(
                    dig(
                        data,
                        "over_time.approvals_only.share_of_the_population_with_no_observable_outcome",
                    )
                ),
                "declined applicants never borrow",
            ),
            (
                "bad rate, approvals only",
                num(dig(data, "over_time.approvals_only.approved_bad_rate")),
                f"against {num(dig(data, 'over_time.approvals_only.population_bad_rate'))} "
                f"for the whole population",
            ),
        ],
        ("With a time axis", "Result", "Over"),
    )
    lines += [
        "",
        "These four need a cohort with dates and were impossible before this round. The "
        "pair worth reading together is the two PSI rows: the score looks stable while one "
        "of its inputs has moved twenty times as far. A scorecard turns several fields "
        "into one coarse number, so reporting only the score's stability is how a drifting "
        "population gets called stable.",
        "",
        "Three findings come out of the real books that the synthetic cohort cannot show. "
        "The strongest feature in the loan book is `interest_rate` - the lender's own price "
        "for that applicant - and dropping it costs a quarter of the separation, so a "
        "scorecard that uses it is partly scoring a decision the lender already made. A "
        "one-off 2% service fee loses money at every cutoff, which is why the sweep's "
        "net-maximising threshold for v1 and v2 is the one that approves nobody; only "
        "pricing over the term earns anything, and that is the version rule C1 flags as "
        'critical. And on the card book, two defensible definitions of "bad" on the same '
        "36,457 rows give Gini figures that differ by more than three times - whoever picks "
        "the definition picks the result.",
    ]
    return lines


def section_03() -> list[str]:
    data = read("p03_guardian")
    lines = dataset_lines(["generated/p03_utterances"])
    lines += [""]
    lines += table(
        [
            (
                "amounts read exactly",
                num(dig(data, "generated.amounts.exact_rate")),
                f"{num(dig(data, 'generated.amounts.cases'))} cases carrying an amount",
            ),
            (
                "amounts not found at all",
                num(dig(data, "generated.amounts.none_found")),
                "same cases",
            ),
            (
                "amounts wrong by ten times or more",
                num(dig(data, "generated.amounts.off_by_ten_or_more")),
                "same cases",
            ),
            (
                "intent accuracy",
                num(dig(data, "generated.intents.accuracy")),
                f"{num(dig(data, 'generated.intents.cases'))} cases with an intent",
            ),
            (
                "payees resolved",
                num(dig(data, "generated.beneficiaries.resolve_rate")),
                f"{num(dig(data, 'generated.beneficiaries.should_resolve'))} naming one contact",
            ),
            (
                "asked instead of guessing",
                num(dig(data, "generated.beneficiaries.ask_rate")),
                f"{num(dig(data, 'generated.beneficiaries.should_ask'))} ambiguous cases",
            ),
            (
                "coercion caught, families it knows",
                num(dig(data, "generated.coercion.catch_rate_within_the_vocabulary")),
                "the twelve families in the product's own vocabulary",
            ),
            (
                "coercion caught, types it does not know",
                num(dig(data, "generated.coercion.catch_rate_outside_the_vocabulary")),
                "job offers, QR swaps, SIM swaps, refund overpayment",
            ),
            (
                "coercion caught, everything together",
                num(dig(data, "generated.coercion.catch_rate")),
                f"{num(dig(data, 'generated.coercion.cases'))} scam scripts; the blend of "
                f"the two rows above",
            ),
            (
                "ordinary traffic blocked",
                num(dig(data, "generated.benign.false_block_rate")),
                f"{num(dig(data, 'generated.benign.cases'))} ordinary messages",
            ),
            (
                "confirmations read correctly",
                num(dig(data, "generated.replies.accuracy")),
                f"{num(dig(data, 'generated.replies.cases'))} replies",
            ),
        ],
        ("At full size", "Result", "Over"),
    )
    lines += [""]
    lines += table(
        [
            (
                "scam types the product has no words for",
                num(dig(data, "generated.scam_types_with_no_vocabulary.catch_rate")),
                f"{num(dig(data, 'generated.scam_types_with_no_vocabulary.cases'))} scripts: "
                f"job offers, QR swaps, SIM swaps, refund overpayment",
            ),
            (
                "obfuscated security words",
                num(dig(data, "generated.obfuscated_security_words.leetspeak.catch_rate")),
                "leetspeak; the other four techniques are within 0.01 of it",
            ),
            (
                "Urdu script, ordinary spelling",
                num(dig(data, "generated.urdu_script_variants.urdu.catch_rate")),
                f"{num(dig(data, 'generated.urdu_script_variants.urdu.cases'))} scripts",
            ),
            (
                "Urdu script, Arabic codepoints",
                num(dig(data, "generated.urdu_script_variants.urdu_arabic_codepoints.catch_rate")),
                "the same sentences, pasted from elsewhere - identical to the row above, "
                "which is the point of folding the codepoints",
            ),
            (
                "splits across one day",
                num(dig(data, "generated.structuring.by_pattern.same_day.catch_rate")),
                f"{num(dig(data, 'generated.structuring.by_pattern.same_day.threads'))} splits",
            ),
            (
                "splits across several days",
                num(dig(data, "generated.structuring.by_pattern.across_days.catch_rate")),
                f"{num(dig(data, 'generated.structuring.by_pattern.across_days.threads'))} splits",
            ),
            (
                "whole conversations handled correctly",
                num(dig(data, "generated.threads.handled_correctly_rate")),
                f"{num(dig(data, 'generated.threads.threads'))} threads, "
                f"{num(dig(data, 'generated.threads.executed_after_a_no'))} executed after a no",
            ),
        ],
        ("The dimensions added this round", "Result", "Over"),
    )
    lines += [
        "",
        "Two of those rows were zero before the work in this round. Splits spread over "
        "several days never came near the daily cap and the layer had no monthly view, so "
        "527 of them went entirely unnoticed; with a monthly signal they are caught 0.987 "
        "of the time and the false-block rate on ordinary traffic is unchanged. And a "
        "conversation where the assistant asked which payee was meant could not read the "
        "answer - 53 of 1,667 threads completed - because the session parsed every reply "
        "as a fresh instruction.",
    ]

    families = dig(data, "generated.coercion.by_family", {}) or {}
    if families:
        lines += [
            "",
            "| Coercion family | Caught | Named correctly | Scripts |",
            "| --- | ---: | ---: | ---: |",
        ]
        for name, entry in sorted(families.items()):
            lines.append(
                f"| {name.replace('_', ' ')} | {num(entry.get('catch_rate'))} | "
                f"{num(entry.get('named_the_family_rate'))} | {num(entry.get('cases'))} |"
            )
    lines += [
        "",
        "Sixteen defects came out of this run: nine in the product, and seven of my own "
        "in the generator and the scorer. Both halves belong here, because a generated "
        "test set nobody audits measures the generator.",
        "",
        "**In the product.** Three in the amount parser: the mobile-number guard matched "
        'starting *inside* an amount, so "42000 03461124202 ko bhejo" came back with no '
        "amount at all; the word scanner read the phone number as a continuation of the "
        "amount and produced 3,104,061,991, which only the guard's own reserved span "
        "stopped being offered as a transfer; and `hzar`, a spelling attested in the "
        "mined corpora, was missing from the scale table. Three in the intent lexicon: "
        "the bare stem `bhej` - the commonest way of saying it - matched nothing, "
        "`X ke account me 154000 dein` needed a pattern rather than a substring, and "
        "`250 ka load do` and `account me kitna hai` were both unknown. Three in the "
        "coercion corpus: the commonest account-redirect phrasings were absent, which "
        "took that family from 0.300 to 0.981 once added - and it had been *naming* none "
        'of them, because "purana account band hai" contains "account band" and was '
        "being caught as a threat.",
        "",
        "**In the generator, which is mine.** Fractional modifiers were multiplied "
        'instead of added, so "saadhe do crore" was labelled 30,000,000 rather than '
        "25,000,000; benign scripts naming a relation carried a random contact as the "
        "gold payee; `k` was used as a word scale because the corpora attest it - as the "
        'shortened conjunction *ke* - producing "chaar k paanch sau"; typos were injected '
        "into the amount itself, so the sentence no longer held the number it was scored "
        "on; a transfer to a phone number was labelled a top-up; the giving verb `do` "
        "was placed next to a number, where it is genuinely ambiguous with the numeral "
        "two; and confirmations were scored through the intent detector, which has no "
        "affirmative intent to return - that one read 0.5034, which is chance.",
        "",
        "The false-block rate is printed next to the catch rate because they move together. "
        'Adding the bare words for "quickly" took the catch rate to 0.9802 and the '
        "false-block rate from 0.0211 to 0.1232 - one ordinary message in eight, since "
        '"jaldi nahi koi baat nahi" means *no rush*. Requiring a verb alongside them gives '
        "0.9609 at 0.0211, which is what shipped.",
    ]
    return lines


def section_04() -> list[str]:
    data = read("p04_spec_to_wire")
    lines = dataset_lines(["generated/p04_tasks"])
    lines += [""]
    lines += table(
        [
            (
                "decode",
                num(dig(data, "product_on_the_whole_bank.by_family.decode.accuracy")),
                f"{num(dig(data, 'product_on_the_whole_bank.by_family.decode.cases'))} tasks",
            ),
            (
                "fields",
                num(dig(data, "product_on_the_whole_bank.by_family.fields.accuracy")),
                f"{num(dig(data, 'product_on_the_whole_bank.by_family.fields.cases'))} tasks",
            ),
            (
                "wire",
                num(dig(data, "product_on_the_whole_bank.by_family.wire.accuracy")),
                f"{num(dig(data, 'product_on_the_whole_bank.by_family.wire.cases'))} tasks",
            ),
            (
                "negative: found a fault",
                num(dig(data, "product_on_the_whole_bank.by_family.negative.found_a_fault_rate")),
                f"{num(dig(data, 'product_on_the_whole_bank.by_family.negative.cases'))} tasks",
            ),
            (
                "negative: named the injected fault",
                num(
                    dig(
                        data,
                        "product_on_the_whole_bank.by_family.negative.named_the_injected_fault_rate",
                    )
                ),
                "same tasks, stricter rule",
            ),
            (
                "the model's share of the bank",
                num(dig(data, "model_on_a_sample.share_of_the_bank")),
                f"{num(dig(data, 'model_on_a_sample.cases_the_model_answered'))} of "
                f"{num(dig(data, 'model_on_a_sample.tasks_in_the_bank'))} tasks",
            ),
        ],
        ("At full size", "Result", "Over"),
    )
    lines += [
        "",
        "The gap between the last two negative rows is the bitmap family. A flipped bitmap "
        "bit shifts the layout, so the field that lands where the amount should be is "
        "genuinely not numeric - and an ISO 8583 bitmap carries no redundancy, so nothing "
        "downstream can say which bit moved. Calling that message malformed is correct; "
        'demanding the word "bitmap" would be scoring a guess. Two faults were also '
        "removed from the bank for not surviving the packer: a two-digit trace number comes "
        "back padded to six, so the task asked about damage the message no longer carried.",
    ]
    return lines


def section_05() -> list[str]:
    data = read("p05_policy_from_prose")
    lines = dataset_lines(["generated/p05_policies"])
    lines += [""]
    lines += table(
        [
            (
                "compiled",
                num(dig(data, "language_on_the_whole_bank.compile_rate")),
                f"{num(dig(data, 'language_on_the_whole_bank.policies'))} policies",
            ),
            (
                "printed back and re-compiled identically",
                num(dig(data, "language_on_the_whole_bank.round_trip_rate")),
                "same policies",
            ),
            (
                "evaluated without an exception",
                num(dig(data, "language_on_the_whole_bank.evaluate_rate")),
                "same policies",
            ),
            (
                "decisions unchanged across a reprint",
                num(dig(data, "language_on_the_whole_bank.stable_decision_rate")),
                f"{num(dig(data, 'language_on_the_whole_bank.decisions_made'))} decisions",
            ),
            (
                "the model's share of the bank",
                num(dig(data, "model_on_a_sample.share_of_the_bank")),
                f"{num(dig(data, 'model_on_a_sample.policies_the_model_compiled'))} of "
                f"{num(dig(data, 'model_on_a_sample.policies_in_the_bank'))} policies",
            ),
        ],
        ("At full size", "Result", "Over"),
    )
    lines += [""]
    lines += table(
        [
            (
                "requirements taken from real policy documents",
                num(dig(data, "what_the_language_cannot_say.requirements")),
                "each one run through the compiler by a test",
            ),
            (
                "refused by the grammar",
                num(dig(data, "what_the_language_cannot_say.refused_by_the_grammar")),
                "the safe failure: the policy never ships",
            ),
            (
                "compile, then fail on the first applicant",
                num(dig(data, "what_the_language_cannot_say.compile_but_have_no_field")),
                "the dangerous one: they pass review",
            ),
        ],
        ("What the language cannot say", "Count", "Note"),
    )
    lines += [
        "",
        "A compile rate of 1.0000 is 100% of what this language *can* say. The table above "
        "is the other half, and the split in it is the finding: most of these are refused "
        "outright, and a couple compile, pass review and die on the first applicant because "
        "the field they name does not exist.",
        "",
        "This doubles as a grammar fuzzer, and the four rates above are the properties that "
        "have to hold: the text came out of this project's own printer, so a parse failure "
        "would be the language unable to read what it writes. Two faults in the *generator* "
        "showed up on the way: a sampler that could produce a policy whose every rule took "
        "points away, which can never approve anybody and which the compiler correctly "
        "refuses; and a phrase table that paired a comparator with the opposite English, so "
        '"supports fewer than 6 dependants is worth 5 points" sat next to '
        "`score +5 if dependants >= 6`. A bank of 60,000 of those would have marked every "
        "correct answer wrong.",
    ]
    return lines


def section_06() -> list[str]:
    data = read("p06_model_vs_lexicon")
    lines = dataset_lines(["generated/p03_utterances"])
    lines += [""]
    lines += table(
        [
            (
                "amounts exact",
                num(dig(data, "rules_on_the_whole_set.amount_exact_rate")),
                "the cases carrying an amount",
            ),
            (
                "intent accuracy",
                num(dig(data, "rules_on_the_whole_set.intent_accuracy")),
                "the cases with an intent",
            ),
            (
                "coercion caught",
                num(dig(data, "rules_on_the_whole_set.coercion_catch_rate")),
                "40,000 scripts",
            ),
            (
                "ordinary traffic blocked",
                num(dig(data, "rules_on_the_whole_set.false_block_rate_on_ordinary_traffic")),
                "25,000 messages",
            ),
            (
                "the model's share of the set",
                num(dig(data, "model_on_a_sample.share_of_the_set")),
                f"{num(dig(data, 'model_on_a_sample.cases'))} of "
                f"{num(dig(data, 'the_same_dataset.rows'))} cases",
            ),
        ],
        ("The rules, on the whole set", "Result", "Over"),
    )
    lines += [
        "",
        "The asymmetry is the point rather than an omission. A lexicon and a grammar answer "
        "200,000 cases in about a minute; 200,000 local model calls is a fortnight of GPU "
        "time for a result whose shape is visible at 65. What the comparison cannot say is "
        "which side is better at the blind spot, because the blind spot is four cases - and "
        "scaling that family needs new hand-written scams rather than generated ones, since "
        "a generator able to write them would have to know which words the lexicon lacks, "
        "and would then be writing the answer key.",
    ]
    return lines


SECTIONS = {
    "p01_switchboard": section_01,
    "p02_policy_bench": section_02,
    "p03_guardian": section_03,
    "p04_spec_to_wire": section_04,
    "p05_policy_from_prose": section_05,
    "p06_model_vs_lexicon": section_06,
}


def root_table() -> list[str]:
    manifest = load_manifest()
    order = [
        ("01", "generated/p01_messages", "ISO 8583 messages, generated from a seed"),
        ("02", "real/loan_book", "Real loan applications with their outcomes"),
        ("02", "real/card_applications", "Real card applications, outcome mostly unknown"),
        ("02", "real/card_credit_records", "Monthly repayment status, joined as a lookup"),
        ("03", "generated/p03_utterances", "Wallet utterances, labelled by construction"),
        ("04", "generated/p04_tasks", "Wire tasks drawn from the 01 corpus"),
        ("05", "generated/p05_policies", "Policies in prose and in the DSL"),
        ("06", "generated/p03_utterances", "The same set project 03 is scored on"),
    ]
    lines = [
        "| Project | Dataset | Rows | Size | Source |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    total = 0
    seen: set[str] = set()
    for project, key, what in order:
        entry = manifest.get(key)
        if not entry:
            lines.append(f"| {project} | {what} | not built | | |")
            continue
        if key not in seen:
            total += int(entry["rows"])
            seen.add(key)
        size = int(entry["bytes"]) / (1024 * 1024)
        lines.append(
            f"| {project} | {what} | {entry['rows']:,} | {size:,.0f} MB | "
            f"{entry.get('source', '')} |"
        )
    lines.append(f"| | **distinct rows across the repo** | **{total:,}** | | |")
    return lines


def write(path: Path, body: list[str]) -> str:
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        return "no markers"
    before = text.split(START)[0]
    after = text.split(END)[1]
    rebuilt = before + START + "\n" + "\n".join(body) + "\n" + END + after
    if rebuilt == text:
        return "unchanged"
    path.write_text(rebuilt, encoding="utf-8", newline="\n")
    return "wrote"


def main() -> int:
    for folder, section in SECTIONS.items():
        path = ROOT / "projects" / folder / "README.md"
        print(f"{write(path, section()):<10} {path.relative_to(ROOT)}")
    print(f"{write(ROOT / 'README.md', root_table()):<10} README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
