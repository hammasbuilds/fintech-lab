"""Policy requirements the language cannot say, written down on purpose.

The generated bank is 60,000 policies sampled from the grammar, and 100% of them compile.
That is a strong property result and a weak coverage one: it says the parser can read
everything the printer writes, and nothing at all about what a real policy document
contains that this language has no words for.

So this file holds the other half - requirements taken from how credit policies are
actually written, each one labelled with the construct it would need. They are **not**
expected to compile. A test asserts that every one of them fails, and the benchmark
reports the count beside the 100%, which turns a flattering number into a useful one:
*the language reads everything it writes, and here are the fourteen things it cannot
express at all.*

Writing these down is also the cheapest form of roadmap. Each entry says what a product
owner asked for, what the policy engine would need, and - where there is one - the
workaround a team would reach for, which is usually worse than the feature.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    key: str
    asked_for: str
    #: The nearest thing somebody would write.
    attempt: str
    needs: str
    workaround: str
    #: Why it does not work, and the two are not the same problem:
    #:
    #: ``grammar``  the language has no syntax for it, and the compiler refuses the text.
    #: ``data``     the grammar accepts it and no such field exists, so it compiles, is
    #:              reviewed, and then fails on the first applicant. That is the more
    #:              dangerous of the two.
    limit: str = "grammar"


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        "effective_date",
        "This version applies to applications from 1 March, and the old one before that.",
        'effective from "2026-03-01"',
        "a date on the policy, and an engine that picks the version by application date",
        "run two policies and route by hand, which is how the wrong one gets used",
    ),
    Requirement(
        "segment_cascade",
        "Salaried applicants are scored on this card, everyone else on the other one.",
        'segment "salaried" { approve if score >= 40 }',
        "nested policies, or a policy that can call another",
        "two files and an if in the calling code, where nobody reviews it",
    ),
    Requirement(
        "exceptions_queue",
        "Anything between 35 and 45 points goes to an officer with the file attached.",
        "queue if score between 35 and 45 to officer",
        "a third outcome with a destination, not just approve, refer and decline",
        "refer, and hope the operations team knows which queue",
    ),
    Requirement(
        "override_with_approver",
        "A manager may override a decline, and the override has to be on the record.",
        'override allowed by "credit_manager" because "documented exception"',
        "an authority model and an audit entry inside the policy",
        "change the data, which leaves no trace of why",
    ),
    Requirement(
        "affordability",
        "Instalments across all lenders may not exceed 40% of income.",
        "decline if (instalment + other_instalments) / monthly_income > 0.4",
        "arithmetic over several fields in a gate, which the grammar does not allow",
        "precompute a ratio outside the policy, where the definition drifts",
    ),
    Requirement(
        "time_window",
        "No more than two loans to the same borrower in ninety days.",
        "decline if loans_in(90 days) >= 2",
        "a window function over the borrower's history",
        "a precomputed counter that is stale by the time it is read",
    ),
    Requirement(
        "stepped_limit",
        "First loan 5,000; after two on-time repayments 10,000; after five, 20,000.",
        "limit ladder(5000, 10000, 20000) by on_time_repayments",
        "a limit that is a table rather than one expression",
        "three policies and a router, with the ladder in the router",
    ),
    Requirement(
        "pricing_by_risk",
        "Price the fee by score band: 3% below 50 points, 2% above.",
        "fee 3% of limit if score < 50 else 2% of limit",
        "a conditional charge",
        "two policies that differ only in the fee, which then diverge",
    ),
    Requirement(
        "cooling_off",
        "A declined applicant may not reapply for thirty days.",
        'decline if days_since_last_decline < 30 because "applied again too soon"',
        "the applicant's own history as a field, and a clock",
        "block in the app, where the policy cannot see or explain it",
        "data",
    ),
    Requirement(
        "portfolio_limit",
        "Stop lending in a region once it holds a fifth of the book.",
        'decline if portfolio_share("region") > 0.2',
        "a rule about the book rather than about the applicant",
        "a manual switch somebody has to remember to flip",
    ),
    Requirement(
        "data_freshness",
        "The bureau pull has to be under seven days old or the application pauses.",
        'refer if bureau_age_days > 7 because "stale bureau data"',
        "the field itself; the grammar already says this much",
        "none for the syntax - the cohort has no bureau_age_days, so it compiles and then "
        "fails on the first applicant",
        "data",
    ),
    Requirement(
        "shariah_structure",
        "Charge a Murabaha mark-up on a stated cost price, not a fee on the limit.",
        "murabaha cost_price * 1.08 payable in 3 instalments",
        "a charge model with a cost basis and instalments",
        "a flat fee that looks like the same number and is not the same contract",
    ),
    Requirement(
        "co_borrower",
        "Two applicants, the household's income, and both liable.",
        "applicants 2 where income = sum(monthly_income)",
        "more than one applicant per decision",
        "add the incomes before the policy sees them, which hides who is liable",
    ),
    Requirement(
        "regulatory_reference",
        "Tag the aggregate cap rule with the circular it comes from.",
        'decline if other_app_outstanding > 55000 under "SECP Circular 10 of 2023"',
        "a citation on a rule, so a change of law finds every rule it touches",
        "a comment, which no report can read",
    ),
)

#: Requirements whose text the compiler accepts. They are the control for a test that
#: would otherwise pass against a compiler which refuses everything - and they are the
#: more interesting half, because a policy that compiles and then dies on the first
#: applicant has already passed review.
COMPILES_BUT_HAS_NO_FIELD = tuple(item.key for item in REQUIREMENTS if item.limit == "data")


def attempts() -> list[tuple[str, str, bool]]:
    """``(key, the text somebody would write, whether the compiler accepts it)``."""
    return [(item.key, item.attempt, item.limit == "data") for item in REQUIREMENTS]


def summary() -> dict[str, object]:
    by_limit: dict[str, int] = {}
    for item in REQUIREMENTS:
        by_limit[item.limit] = by_limit.get(item.limit, 0) + 1
    return {
        "requirements": len(REQUIREMENTS),
        "refused_by_the_grammar": by_limit.get("grammar", 0),
        "compile_but_have_no_field": by_limit.get("data", 0),
        "constructs_missing": sorted(
            {item.needs for item in REQUIREMENTS if item.limit == "grammar"}
        ),
        "reading": (
            "The generated bank reports that 100% of 60,000 policies compile. That is "
            "100% of what this language can say. These are the things it cannot, taken "
            "from how credit policies are actually written - and they split in two. Most "
            "are refused outright, which is the safe failure. The rest compile, pass "
            "review and then fail on the first applicant because the field they name "
            "does not exist, which is the failure worth knowing about."
        ),
    }
