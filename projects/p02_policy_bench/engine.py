"""Run a compiled policy against one applicant.

The decision carries its whole derivation: which gate fired, every scoring rule that
matched and what it was worth, the total, the limit expression's value, and the charges.
A decision that cannot be read back line by line is not auditable, and an unauditable
credit decision is one nobody can defend to a regulator or to the applicant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dsl import (
    Call,
    Charge,
    Comparison,
    Condition,
    Expr,
    FieldRef,
    Number,
    Policy,
)


class EvaluationError(Exception):
    """The policy asked for something the applicant record does not have."""


@dataclass
class ScoreLine:
    points: float
    condition: str
    reason: str
    matched: bool


@dataclass
class Decision:
    outcome: str  # "approve" | "decline" | "refer"
    score: float
    threshold: float
    limit: float
    charges: list[dict[str, object]] = field(default_factory=list)
    gate: dict[str, object] | None = None
    score_lines: list[ScoreLine] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.outcome == "approve"

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "score": self.score,
            "threshold": self.threshold,
            "limit": round(self.limit, 2),
            "charges": self.charges,
            "gate": self.gate,
            "reasons": self.reasons,
            "score_lines": [
                {
                    "points": line.points,
                    "condition": line.condition,
                    "reason": line.reason,
                    "matched": line.matched,
                }
                for line in self.score_lines
            ],
        }


def evaluate(policy: Policy, applicant: dict[str, object]) -> Decision:
    """Apply ``policy`` to ``applicant``. Gates first, then the scorecard."""
    for gate in policy.gates:
        if _condition(gate.condition, applicant):
            decision = Decision(
                outcome=gate.outcome,
                score=0.0,
                threshold=policy.approve_at or 0.0,
                limit=0.0,
                gate={
                    "outcome": gate.outcome,
                    "condition": gate.condition.render(),
                    "reason": gate.reason,
                    "line": gate.line,
                },
                reasons=[gate.reason],
            )
            return decision

    lines: list[ScoreLine] = []
    score = 0.0
    for rule in policy.score_rules:
        matched = _condition(rule.condition, applicant)
        if matched:
            score += rule.points
        lines.append(
            ScoreLine(
                points=rule.points,
                condition=rule.condition.render(),
                reason=rule.reason,
                matched=matched,
            )
        )

    threshold = policy.approve_at or 0.0
    approved = score >= threshold
    limit = _expr(policy.limit, applicant, score) if (policy.limit and approved) else 0.0
    periods = _expr(policy.term_months, applicant, score) if policy.term_months else None
    charges = [_charge(charge, limit, periods) for charge in policy.charges] if approved else []

    if approved:
        reasons = [
            line.reason or line.condition for line in lines if line.matched and line.points > 0
        ]
    else:
        shortfall = threshold - score
        reasons = [
            f"scored {_round(score)} against the {_round(threshold)} this policy requires, "
            f"{_round(shortfall)} short"
        ]
        reasons += [
            f"missed: {line.reason or line.condition}"
            for line in lines
            if not line.matched and line.points > 0
        ]

    return Decision(
        outcome="approve" if approved else "decline",
        score=score,
        threshold=threshold,
        limit=limit,
        charges=charges,
        score_lines=lines,
        reasons=reasons,
    )


def _round(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _charge(charge: Charge, limit: float, periods: float | None = None) -> dict[str, object]:
    """One charge, in money.

    A charge priced per unit of time is multiplied by ``periods`` when the policy states
    a term. When it does not - which is every policy written before ``term`` existed -
    the charge is counted once, exactly as before: inventing a number of months for a
    policy that never named one would change what those policies are measured to earn.
    """
    amount = charge.flat if charge.flat is not None else (charge.rate or 0.0) * limit
    months = periods if (charge.period and periods) else None
    if months:
        amount *= months
    return {
        "kind": charge.kind,
        "amount": round(amount, 2),
        "basis": charge.render(),
        "period": charge.period,
        "periods_charged": round(months, 2) if months else None,
    }


def _condition(condition: Condition, applicant: dict[str, object]) -> bool:
    if isinstance(condition, Comparison):
        return _compare(condition, applicant)
    results = [_condition(part, applicant) for part in condition.parts]
    return all(results) if condition.op == "and" else any(results)


def _compare(comparison: Comparison, applicant: dict[str, object]) -> bool:
    if comparison.field_name not in applicant:
        raise EvaluationError(
            f"line {comparison.line}: the applicant record has no field "
            f"{comparison.field_name!r}; it has {', '.join(sorted(applicant))}"
        )
    left = applicant[comparison.field_name]
    right = comparison.value
    if isinstance(right, str):
        if comparison.op == "==":
            return str(left) == right
        return str(left) != right
    if isinstance(left, bool):
        left = int(left)
    if not isinstance(left, (int, float)):
        raise EvaluationError(
            f"line {comparison.line}: {comparison.field_name} is {left!r}, which cannot be "
            f"compared with the number {right}"
        )
    if comparison.op == "<":
        return left < right
    if comparison.op == "<=":
        return left <= right
    if comparison.op == ">":
        return left > right
    if comparison.op == ">=":
        return left >= right
    if comparison.op == "==":
        return left == right
    return left != right


def _expr(expr: Expr | None, applicant: dict[str, object], score: float) -> float:
    if expr is None:
        return 0.0
    if isinstance(expr, Number):
        return expr.value
    if isinstance(expr, FieldRef):
        if expr.name == "score":
            return score
        if expr.name not in applicant:
            raise EvaluationError(
                f"line {expr.line}: the limit refers to {expr.name!r}, which the applicant "
                f"record does not have"
            )
        value = applicant[expr.name]
        if isinstance(value, bool):
            return float(value)
        if not isinstance(value, (int, float)):
            raise EvaluationError(
                f"line {expr.line}: {expr.name} is {value!r} and cannot be used in an amount"
            )
        return float(value)
    if isinstance(expr, Call):
        left = _expr(expr.args[0], applicant, score)
        right = _expr(expr.args[1], applicant, score)
        return min(left, right) if expr.name == "min" else max(left, right)
    left = _expr(expr.left, applicant, score)
    right = _expr(expr.right, applicant, score)
    if expr.op == "+":
        return left + right
    if expr.op == "-":
        return left - right
    if expr.op == "*":
        return left * right
    if right == 0:
        raise EvaluationError("the limit expression divides by zero")
    return left / right
