"""The prose policies, the notice cases, and the prompts.

The prose is written the way a product owner writes it - prose, not a spec - and each one
has a hand-written DSL policy as its reference, so "did the model get it right" means
"does it decide the same thing about 5,000 borrowers", not "does it look similar".

Three of the five prose policies are deliberately awkward in ways real documents are:

* ``thresholds`` states a floor in words ("at least eighteen thousand") rather than digits;
* ``implicit`` leaves the limit formula to a sentence rather than a formula;
* ``contradiction`` contains two rules that overlap, so a faithful translation has to keep
  both and let the earlier one win - which is what the reference does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from projects.p02_policy_bench import compliance, dsl, engine

POLICIES = Path(dsl.__file__).with_name("policies")

COMPILE = "compile"
NOTICE = "notice"

BARE = "bare"
SPEC = "spec"


@dataclass(frozen=True)
class PolicyCase:
    key: str
    prose: str
    #: The hand-written policy this must behave identically to.
    reference: str
    note: str = ""


def _reference(name: str) -> str:
    return (POLICIES / f"{name}.policy").read_text(encoding="utf-8")


#: A reference written here rather than taken from projects/ when the prose describes
#: something the bundled policies do not.
SIMPLE_REFERENCE = """policy "Starter"
version 1
currency PKR

decline if age < 18 because "below the legal age to contract"
decline if monthly_income < 25000 because "monthly income below the floor of 25,000"

score +40 if wallet_months >= 12 because "a year of wallet history"
score +30 if on_time_repayments >= 1 because "repaid on time before"

approve if score >= 40
limit min(0.2 * monthly_income, 15000)
fee 3% of limit
"""

THRESHOLD_REFERENCE = """policy "Worded thresholds"
version 1
currency PKR

decline if age < 21 because "under twenty-one"
decline if monthly_income < 18000 because "monthly income below eighteen thousand"
decline if prior_defaults >= 1 because "a default already on file"

score +25 if wallet_months >= 6 because "six months of wallet history"
score +25 if avg_balance >= 5000 because "keeps a balance of five thousand or more"

approve if score >= 25
limit min(0.25 * monthly_income, 20000)
fee 2% of limit
"""

CONTRADICTION_REFERENCE = """policy "Overlapping gates"
version 1
currency PKR

decline if monthly_income < 20000 because "monthly income below 20,000"
decline if monthly_income < 30000 because "monthly income below 30,000"

score +50 if wallet_months >= 3 because "three months of wallet history"

approve if score >= 50
limit min(0.25 * monthly_income, 25000)
fee 2.5% of limit
"""

POLICY_CASES: tuple[PolicyCase, ...] = (
    PolicyCase(
        key="starter",
        prose=(
            "We will not lend to anyone under 18, and we will not lend to anyone earning "
            "less than 25,000 rupees a month. Beyond that we score people: a year or more "
            "of wallet history is worth 40 points, and having repaid a loan on time before "
            "is worth 30. We approve at 40 points or above. The limit is a fifth of monthly "
            "income, capped at 15,000 rupees, and we charge a one-off 3% service fee on the "
            "limit."
        ),
        reference=SIMPLE_REFERENCE,
        note="The straightforward case: every number is a digit and every rule is a sentence.",
    ),
    PolicyCase(
        key="thresholds",
        prose=(
            "Applicants must be twenty-one or older and must earn at least eighteen "
            "thousand rupees a month; anyone with a default already on file is turned away. "
            "Six months of wallet history earns twenty-five points, and so does keeping an "
            "average balance of five thousand or more. Twenty-five points is enough to "
            "approve. Lend a quarter of monthly income up to twenty thousand rupees, with a "
            "two percent fee on the limit."
        ),
        reference=THRESHOLD_REFERENCE,
        note="Every threshold is in words. A transcription error here is a credit decision.",
    ),
    PolicyCase(
        key="contradiction",
        prose=(
            "We decline anyone earning under 20,000 rupees a month. Separately, and this "
            "was added later by the risk committee without removing the first rule, we "
            "decline anyone earning under 30,000. Three months of wallet history is worth "
            "50 points and 50 points is enough to approve. The limit is a quarter of income "
            "capped at 25,000, with a 2.5% fee."
        ),
        reference=CONTRADICTION_REFERENCE,
        note=(
            "Two overlapping gates, as a real document would have them. Keeping both is "
            "faithful; silently merging them changes the reason an applicant is given."
        ),
    ),
    PolicyCase(
        key="nano_v1",
        prose=(
            "This is our launch product. Nobody under 18. Nobody earning less than 12,000 "
            "a month. Nobody with two or more defaults on file. For scoring: 20 points for "
            "six months of wallet history, another 15 for eighteen months, 20 points for "
            "earning 30,000 or more, 15 for having repaid on time at least twice, and 10 "
            "for keeping an average balance of 4,000 or more. We approve from 35 points. "
            "The limit is a quarter of monthly income capped at 20,000 rupees and the fee "
            "is 2.5% of the limit."
        ),
        reference=_reference("nano_v1"),
        note="The real v1 policy, described in prose.",
    ),
    PolicyCase(
        key="nano_v2",
        prose=(
            "Version two tightens v1. Keep the age rule. Raise the income floor to 18,000. "
            "Decline anyone with any default on file, not just two. Decline anyone whose "
            "wallet turnover is above 120 transactions a month, because we cannot read "
            "income through that. Refer - do not decline - anyone with six or more "
            "dependants, so an officer can size the limit. Scoring is as v1, plus minus 15 "
            "points for turnover above 80, and plus 10 for being salaried. Approve from 45. "
            "Limit and fee unchanged from v1."
        ),
        reference=_reference("nano_v2"),
        note="A change described as a diff against another policy, which is how they arrive.",
    ),
)


@dataclass(frozen=True)
class NoticeCase:
    key: str
    applicant: dict[str, object]
    policy: str
    language: str


#: Applicants chosen so the notice has something specific to say: one stopped by a gate,
#: one short on score, one approved.
NOTICE_CASES: tuple[NoticeCase, ...] = (
    NoticeCase(
        key="gate-income-en",
        applicant={"monthly_income": 9000, "age": 30, "prior_defaults": 0},
        policy="nano_v2",
        language="en",
    ),
    NoticeCase(
        key="gate-default-ur",
        applicant={"monthly_income": 45000, "age": 30, "prior_defaults": 2},
        policy="nano_v2",
        language="ur",
    ),
    NoticeCase(
        key="short-score-en",
        applicant={
            "monthly_income": 22000,
            "age": 30,
            "prior_defaults": 0,
            "wallet_months": 2,
            "on_time_repayments": 0,
            "avg_balance": 500,
            "txn_velocity": 10,
            "employment": "daily-wage",
        },
        policy="nano_v2",
        language="en",
    ),
    NoticeCase(
        key="short-score-ur",
        applicant={
            "monthly_income": 22000,
            "age": 30,
            "prior_defaults": 0,
            "wallet_months": 2,
            "on_time_repayments": 0,
            "avg_balance": 500,
            "txn_velocity": 10,
            "employment": "daily-wage",
        },
        policy="nano_v2",
        language="ur",
    ),
    NoticeCase(
        key="approved-en",
        applicant={
            "monthly_income": 60000,
            "age": 34,
            "prior_defaults": 0,
            "wallet_months": 24,
            "on_time_repayments": 3,
            "avg_balance": 9000,
            "txn_velocity": 20,
            "employment": "salaried",
        },
        policy="nano_v2",
        language="en",
    ),
)

# ------------------------------------------------------------------- the prompts

SYSTEM = (
    "You turn written credit policy into a small policy language, exactly and without "
    "adding rules. You reply with only what is asked for."
)

NOTICE_SYSTEM = (
    "You write the notice a loan applicant receives. You state only the reason you are "
    "given, you never invent a reason, and you never promise or imply a future approval."
)

GRAMMAR = """The language, in full:

    policy      "<name>"
    version     <number>
    currency    <CODE>

    decline if  <field> <op> <value> because "<reason>"
    refer   if  <field> <op> <value> because "<reason>"
    score  +<n> if <field> <op> <value> because "<reason>"
    approve if  score >= <n>
    limit       min(<expr>, <expr>)
    fee         <n>% of limit

Operators are < <= > >= == != . Conditions may be joined with 'and' and 'or'. A number is
plain digits. A text value is in double quotes. 'limit' takes an arithmetic expression over
the fields, and min() and max() of two expressions. 'fee' may also be a flat number, and
may be followed by 'per <period>' - though a charge priced per unit of time is rejected by
the compliance lint, so do not use it.

Statement order matters: gates are tested in the order written and the first one that
matches decides, so keep the order of the source document.

The applicant fields available are: age, monthly_income, wallet_months, txn_velocity,
prior_defaults, on_time_repayments, dependants, avg_balance, employment, region,
has_smartphone. 'employment' is one of "salaried", "self-employed", "daily-wage".
'region' must not be used in a decision."""


def compile_prompt(case: PolicyCase, arm: str) -> str:
    head = (
        "Here is a credit policy as it was written by the product owner:\n\n"
        f"{case.prose}\n\n"
        "Write it in the policy language. Reply with only the policy text, no code fence "
        "and no commentary. Keep every rule and do not add any. Give each gate a reason "
        "that states the actual threshold."
    )
    if arm == SPEC:
        head += "\n\n" + GRAMMAR
        head += (
            "\n\nHere is an unrelated policy in the language, as an example of the shape:\n\n"
            + SIMPLE_REFERENCE
        )
    return head


def notice_prompt(case: NoticeCase, arm: str) -> str:
    from projects.p02_policy_bench.app import borrowers

    policy = dsl.compile_policy(_reference(case.policy))
    record = dict(borrowers()[0].record())
    record.update(case.applicant)
    decision = engine.evaluate(policy, record)

    if decision.gate is not None:
        what = (
            f"The application was declined by a rule. The rule is: "
            f"{decision.gate['condition']}. The reason recorded for the applicant is: "
            f'"{decision.gate["reason"]}".'
        )
    elif decision.approved:
        what = (
            f"The application was approved. The limit is {decision.limit:,.0f} "
            f"{policy.currency}. The charges are: "
            + "; ".join(str(charge["basis"]) for charge in decision.charges)
            + "."
        )
    else:
        missed = [
            line.reason or line.condition
            for line in decision.score_lines
            if not line.matched and line.points > 0
        ]
        what = (
            f"The application was declined on score. The applicant scored "
            f"{decision.score:g} and this product needs {decision.threshold:g}. The "
            f"requirements they did not meet are: " + "; ".join(missed) + "."
        )

    language = "English" if case.language == "en" else "Roman Urdu (Urdu written in Latin letters)"
    head = (
        f"{what}\n\n"
        f"Write the notice this applicant receives, in {language}. Two or three sentences. "
        "State the reason above and nothing else. Do not invent any other reason, do not "
        "suggest what would have been approved, and do not promise or imply that a future "
        "application will succeed. Reply with only the notice."
    )
    if arm == SPEC:
        head += (
            "\n\nThe notice must also tell them they may ask for the decision to be "
            "reviewed by a person, and that they can apply again when the reason has "
            "changed - without suggesting that doing so will succeed."
        )
    return head


def reference_lint(case: PolicyCase) -> list[str]:
    """What the compliance lint says about the reference, for comparison."""
    policy = dsl.compile_policy(case.reference)
    return [note.check for note in compliance.review(policy)]
