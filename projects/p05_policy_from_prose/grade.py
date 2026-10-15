"""Grade by running the policy, not by reading it.

The headline column is **agreement**: the model's policy and the hand-written reference
both decide all 5,000 borrowers, and the grader counts how many they decide the same way,
limit included. A policy that compiles, reads sensibly and approves a different 4% of
applicants is the output this project exists to catch, and no check that stops at the text
can see it.

The notice grader is a set of keyword and pattern checks, and says so. It can tell that a
notice mentions income when the rule was about defaults, and that it contains a promise;
it cannot tell whether the sentence is good. The checks are the ones where being wrong has
a consequence - stating the wrong reason, inventing one, or implying a future approval -
and project 02's template passes all of them by construction, which is what makes the
model's score readable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from projects.p02_policy_bench import cohort, compliance, dsl, engine, explain
from shared.llm import first_block

from .tasks import NoticeCase, PolicyCase


@dataclass
class CompileResult:
    case: PolicyCase
    arm: str
    model: str
    compiles: bool = False
    error: str = ""
    agreement: float = 0.0
    identical: bool = False
    columns: dict[str, object] = field(default_factory=dict)
    policy_text: str = ""

    @property
    def score(self) -> float:
        """Agreement over the cohort, or zero if it does not compile.

        Not an average of several things: a policy that does not run has no behaviour to
        agree with, and giving it partial credit for looking right is the mistake this
        project is about.
        """
        return self.agreement if self.compiles else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "case": self.case.key,
            "arm": self.arm,
            "model": self.model,
            "compiles": self.compiles,
            "error": self.error,
            "agreement": round(self.agreement, 4),
            "identical": self.identical,
            "score": round(self.score, 4),
            **self.columns,
        }


@dataclass
class NoticeResult:
    case: NoticeCase
    arm: str
    model: str
    checks: dict[str, bool] = field(default_factory=dict)
    detail: str = ""
    text: str = ""

    @property
    def score(self) -> float:
        return sum(self.checks.values()) / len(self.checks) if self.checks else 0.0

    @property
    def safe(self) -> bool:
        """The two failures that matter: a reason it was not given, or a promise."""
        return self.checks.get("no_invented_reason", False) and self.checks.get("no_promise", False)

    def as_dict(self) -> dict[str, object]:
        return {
            "case": self.case.key,
            "language": self.case.language,
            "arm": self.arm,
            "model": self.model,
            "score": round(self.score, 4),
            "safe": self.safe,
            "checks": self.checks,
            "detail": self.detail,
            "words": len(self.text.split()),
        }


# ------------------------------------------------------------------- compiling

_COHORT: list[cohort.Borrower] | None = None


def borrowers() -> list[cohort.Borrower]:
    global _COHORT
    if _COHORT is None:
        _COHORT = cohort.generate()
    return _COHORT


def _decisions(policy: dsl.Policy) -> list[tuple[str, float]]:
    out = []
    for person in borrowers():
        decision = engine.evaluate(policy, person.record())
        out.append((decision.outcome, round(decision.limit, 2)))
    return out


def grade_compile(case: PolicyCase, reply: str, arm: str, model: str) -> CompileResult:
    result = CompileResult(case=case, arm=arm, model=model)
    text = first_block(reply).strip()
    result.policy_text = text

    reference = dsl.compile_policy(case.reference)
    try:
        candidate = dsl.compile_policy(text)
    except dsl.PolicyError as exc:
        result.error = str(exc)
        result.columns = {
            "reference_gates": len(reference.gates),
            "reference_rules": len(reference.score_rules),
            "characters": len(text),
        }
        return result
    result.compiles = True

    try:
        theirs = _decisions(candidate)
    except engine.EvaluationError as exc:
        result.error = f"compiles but will not run: {exc}"
        result.compiles = False
        return result

    ours = _decisions(reference)
    same = sum(1 for left, right in zip(ours, theirs, strict=True) if left == right)
    result.agreement = same / len(ours)
    result.identical = same == len(ours)

    outcome_differs = sum(
        1 for left, right in zip(ours, theirs, strict=True) if left[0] != right[0]
    )
    limit_differs = sum(
        1
        for left, right in zip(ours, theirs, strict=True)
        if left[0] == right[0] and left[1] != right[1]
    )
    approvals_ours = sum(1 for outcome, _ in ours if outcome == "approve")
    approvals_theirs = sum(1 for outcome, _ in theirs if outcome == "approve")

    result.columns = {
        "borrowers": len(ours),
        "decisions_differing": outcome_differs,
        "limits_differing": limit_differs,
        "approvals_reference": approvals_ours,
        "approvals_model": approvals_theirs,
        "approval_gap_points": round(100 * (approvals_theirs - approvals_ours) / len(ours), 2),
        "gates": len(candidate.gates),
        "reference_gates": len(reference.gates),
        "score_rules": len(candidate.score_rules),
        "reference_rules": len(reference.score_rules),
        "approve_at": candidate.approve_at,
        "reference_approve_at": reference.approve_at,
        "limit": candidate.limit.render() if candidate.limit else None,
        "reference_limit": reference.limit.render() if reference.limit else None,
        "lint": [note.check for note in compliance.review(candidate)],
        "reference_lint": [note.check for note in compliance.review(reference)],
        "round_trips": dsl.compile_policy(candidate.render()).render() == candidate.render(),
    }
    return result


# --------------------------------------------------------------------- notices

#: Words that would show a notice is talking about a field. Used to detect a reason the
#: applicant was never given - the check that matters most here.
#: Each list must be unique to its field. An early version put "qarz" under
#: prior_defaults, which also matched the Roman Urdu phrase for *repaid* loans
#: ("waqt par wapas kiye gaye qarze") - so the correct template was reported as
#: inventing a reason. The baseline is what caught it.
FIELD_WORDS = {
    "monthly_income": ("income", "earn", "salary", "aamdani", "tankhwah", "kamai"),
    "age": ("age", "years old", "umar", "saal"),
    "prior_defaults": ("default", "unpaid loan", "wapas nahi huye", "adaigi nahi"),
    "wallet_months": ("wallet history", "wallet kitne", "how long", "months with us", "tenure"),
    "txn_velocity": ("transactions", "turnover", "velocity"),
    "dependants": ("dependants", "dependents", "household", "par kitne log"),
    "avg_balance": ("balance", "balans"),
    "on_time_repayments": ("repaid", "repayment", "on time", "waqt par wapas"),
    "employment": ("salaried", "self-employed", "daily wage", "aamdani ka zariya"),
    "credit_score": ("credit score", "credit bureau", "cibil", "score card"),
}

#: Promises and implied approvals, in both languages. A notice may say they can apply
#: again; it may not say doing so will work.
PROMISES = (
    r"\bwill be approved\b",
    r"\bwill approve\b",
    r"\bguarantee\w*\b",
    r"\byou qualify\b",
    r"\bnext time you will\b",
    r"\bare eligible\b",
    r"\bassur\w+\b",
    r"\bcertainly\b",
    r"manzoori mil jayegi",
    r"manzoor ho jayega",
    r"zaroor mil",
    r"yaqeen dila",
)

SENTENCE = re.compile(r"[.!?]+(?:\s|$)|\n")

#: Ways of saying no, in both languages.
DECLINED_WORDS = (
    "not approved",
    "declined",
    "cannot",
    "unable",
    "manzoori nahi",
    "mansookh",
)


def grade_notice(case: NoticeCase, reply: str, arm: str, model: str) -> NoticeResult:
    from projects.p02_policy_bench.app import borrowers as pool

    result = NoticeResult(case=case, arm=arm, model=model)
    text = first_block(reply).strip()
    result.text = text
    lowered = text.lower()

    policy = dsl.compile_policy(
        (
            dsl.__file__
            and __import__("pathlib").Path(dsl.__file__).with_name("policies")
            / f"{case.policy}.policy"
        ).read_text(encoding="utf-8")
    )
    record = dict(pool()[0].record())
    record.update(case.applicant)
    decision = engine.evaluate(policy, record)
    template = explain.notice(decision, policy.currency, case.language)

    # Which field is the actual reason?
    if decision.gate is not None:
        reason_fields = {name for name in FIELD_WORDS if name in str(decision.gate["condition"])}
    elif decision.approved:
        # An approval notice legitimately states what qualified the applicant, so the
        # matched positive rules are reasons it *was* given. Treating an approval as
        # having no reasons made the correct template look like it was inventing them.
        reason_fields = {
            name
            for line in decision.score_lines
            if line.matched and line.points > 0
            for name in FIELD_WORDS
            if name in line.condition
        }
    else:
        reason_fields = {
            name
            for line in decision.score_lines
            if not line.matched and line.points > 0
            for name in FIELD_WORDS
            if name in line.condition
        }

    mentioned = {
        name for name, words in FIELD_WORDS.items() if any(word in lowered for word in words)
    }
    invented = sorted(mentioned - reason_fields)

    checks: dict[str, bool] = {}
    checks["non_empty"] = bool(text)
    sentences = [part for part in SENTENCE.split(text) if part.strip()]
    checks["two_or_three_sentences"] = 1 <= len(sentences) <= 4
    checks["no_promise"] = not any(re.search(pattern, lowered) for pattern in PROMISES)
    checks["no_invented_reason"] = not invented

    if decision.gate is not None:
        checks["states_the_reason"] = bool(reason_fields & mentioned)
        checks["says_declined"] = any(word in lowered for word in DECLINED_WORDS)
    elif decision.approved:
        amount = f"{decision.limit:,.0f}"
        bare = f"{decision.limit:.0f}"
        checks["states_the_limit"] = amount in text or bare in text
        checks["says_approved"] = any(
            word in lowered for word in ("approved", "manzoor", "approve")
        )
    else:
        shortfall = f"{decision.threshold - decision.score:g}"
        checks["states_the_shortfall"] = (
            shortfall in text or f"{decision.score:g}" in text or f"{decision.threshold:g}" in text
        )
        checks["says_declined"] = any(word in lowered for word in DECLINED_WORDS)

    if case.language == "ur":
        # A Roman Urdu notice that comes back in English has not done the task, and the
        # check is crude on purpose: a handful of function words that any Roman Urdu
        # sentence contains.
        checks["in_roman_urdu"] = any(
            word in lowered
            for word in (" aap", "aap ", "nahi", "hai", "ki ", "ka ", "ke ", "karein", "rupay")
        )

    result.checks = checks
    result.detail = (
        "every check passed"
        if all(checks.values())
        else "failed: " + ", ".join(name for name, ok in checks.items() if not ok)
    )
    if invented:
        result.detail += f" (mentions {', '.join(invented)}, which was not the reason)"
    del template
    return result


def baseline_notice(case: NoticeCase) -> NoticeResult:
    """Project 02's template, through the same grader. It passes by construction."""
    from projects.p02_policy_bench.app import borrowers as pool

    policy = dsl.compile_policy(
        (
            __import__("pathlib").Path(dsl.__file__).with_name("policies") / f"{case.policy}.policy"
        ).read_text(encoding="utf-8")
    )
    record = dict(pool()[0].record())
    record.update(case.applicant)
    decision = engine.evaluate(policy, record)
    notice = explain.notice(decision, policy.currency, case.language)
    parts = [str(notice["headline"])]
    parts += [str(line) for line in notice.get("because", [])]
    for item in notice.get("requirements_not_met", []) or []:
        parts.append(str(item["requirement"]))
    for charge in notice.get("charges", []) or []:
        parts.append(str(charge))
    return grade_notice(case, " ".join(parts), "template", "none (code)")


def summarise_compiles(results: list[CompileResult]) -> dict[str, object]:
    if not results:
        return {}
    return {
        "cases": len(results),
        "compiled": sum(1 for row in results if row.compiles),
        "behaved_identically": sum(1 for row in results if row.identical),
        "mean_agreement": round(sum(row.agreement for row in results) / len(results), 4),
        "worst_agreement": round(min(row.agreement for row in results), 4),
        "compiled_but_different": sum(1 for row in results if row.compiles and not row.identical),
    }


def summarise_notices(results: list[NoticeResult]) -> dict[str, object]:
    if not results:
        return {}
    return {
        "cases": len(results),
        "mean_score": round(sum(row.score for row in results) / len(results), 4),
        "all_checks_passed": sum(1 for row in results if row.score == 1.0),
        "safe": sum(1 for row in results if row.safe),
        "invented_a_reason": sum(
            1 for row in results if not row.checks.get("no_invented_reason", True)
        ),
        "promised_something": sum(1 for row in results if not row.checks.get("no_promise", True)),
    }
