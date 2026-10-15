"""Lint a policy for the things a reviewer would send it back for.

Eight checks. They read the syntax tree, not the output, so they catch a problem in a
policy that has never been run - which is the point of having the policy be a document.

The Shariah check is the one that needs its scope stated plainly. It flags a charge that
is **priced per unit of time**, because a fee that grows with the number of days the
money is outstanding is a return on lending rather than a charge for a service, and that
is what a Shariah-compliant nano-loan product must not have. It is a lint on the
*structure of the charge*, nothing more. It cannot rule on a product, it does not know
whether a service fee is proportionate to real cost, and it is not a fatwa. A policy that
passes has only passed this one structural test.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dsl import Comparison, Condition, Policy

#: Fields that must not drive a decision directly. Using one of these is not a
#: borderline modelling choice; it is the thing fair-lending rules exist to forbid.
PROTECTED = ("region", "gender", "ethnicity", "religion", "marital_status")


@dataclass(frozen=True)
class Note:
    check: str
    severity: str  # critical | serious | warning
    title: str
    detail: str
    line: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "check": self.check,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "line": self.line,
        }


CHECKS = (
    ("C1", "A charge is priced per unit of time", "critical"),
    ("C2", "A protected attribute drives the decision", "critical"),
    ("C3", "A gate has no reason the applicant can be given", "serious"),
    ("C4", "A positive scoring rule has no stated reason", "warning"),
    ("C5", "The limit is not capped", "serious"),
    ("C6", "A scoring rule can never fire", "warning"),
    ("C7", "Two gates overlap, so the later one is unreachable", "warning"),
    ("C8", "The approval threshold is unreachable in practice", "serious"),
)


def _fields(condition: Condition) -> set[str]:
    if isinstance(condition, Comparison):
        return {condition.field_name}
    out: set[str] = set()
    for part in condition.parts:
        out |= _fields(part)
    return out


def review(policy: Policy) -> list[Note]:
    """Every note this policy earns, worst first."""
    notes: list[Note] = []

    # C1 - time-priced charges.
    for charge in policy.charges:
        if charge.period:
            notes.append(
                Note(
                    "C1",
                    "critical",
                    "A charge is priced per unit of time",
                    f"{charge.render()!r} grows with how long the money is outstanding, "
                    f"which prices time rather than a service. For a Shariah-compliant "
                    f"product this has to become a one-off service charge, or a fee tied "
                    f"to a real cost that does not accrue.",
                    charge.line,
                )
            )

    # C2 - protected attributes.
    for gate in policy.gates:
        for name in sorted(_fields(gate.condition) & set(PROTECTED)):
            notes.append(
                Note(
                    "C2",
                    "critical",
                    "A protected attribute drives the decision",
                    f"the gate on line {gate.line} reads {name!r} directly",
                    gate.line,
                )
            )
    for rule in policy.score_rules:
        for name in sorted(_fields(rule.condition) & set(PROTECTED)):
            notes.append(
                Note(
                    "C2",
                    "critical",
                    "A protected attribute drives the decision",
                    f"the scoring rule on line {rule.line} reads {name!r} directly",
                    rule.line,
                )
            )

    # C3 / C4 - reasons.
    for gate in policy.gates:
        if not gate.reason.strip():
            notes.append(
                Note(
                    "C3",
                    "serious",
                    "A gate has no reason the applicant can be given",
                    f"the gate on line {gate.line} declines without a reason",
                    gate.line,
                )
            )
    for rule in policy.score_rules:
        if rule.points > 0 and not rule.reason.strip():
            notes.append(
                Note(
                    "C4",
                    "warning",
                    "A positive scoring rule has no stated reason",
                    f"the rule on line {rule.line} awards {rule.points:g} points with no "
                    f"reason, so an approval cannot be explained in full",
                    rule.line,
                )
            )

    # C5 - an uncapped limit.
    if policy.limit is not None and "min(" not in policy.limit.render().replace(" ", ""):
        notes.append(
            Note(
                "C5",
                "serious",
                "The limit is not capped",
                f"the limit {policy.limit.render()} has no ceiling, so a borrower who "
                f"reports a very large income gets a very large nano loan",
            )
        )

    # C6 - a scoring rule shadowed by an earlier, looser one on the same field.
    seen: dict[tuple[str, str], Comparison] = {}
    for rule in policy.score_rules:
        if not isinstance(rule.condition, Comparison):
            continue
        comparison = rule.condition
        if not isinstance(comparison.value, (int, float)):
            continue
        key = (comparison.field_name, comparison.op)
        earlier = seen.get(key)
        if earlier is not None and isinstance(earlier.value, (int, float)):
            looser = (comparison.op in {">", ">="} and comparison.value <= earlier.value) or (
                comparison.op in {"<", "<="} and comparison.value >= earlier.value
            )
            if looser:
                notes.append(
                    Note(
                        "C6",
                        "warning",
                        "A scoring rule can never fire",
                        f"line {rule.line} is implied by line {earlier.line}: anything "
                        f"matching {comparison.render()} already matched "
                        f"{earlier.render()}, so the two always fire together and the "
                        f"points add up in a way the policy may not intend",
                        rule.line,
                    )
                )
        else:
            seen[key] = comparison

    # C7 - a gate shadowed by an earlier one.
    gate_seen: dict[tuple[str, str], int] = {}
    for gate in policy.gates:
        if not isinstance(gate.condition, Comparison):
            continue
        key = (gate.condition.field_name, gate.condition.op)
        if key in gate_seen:
            notes.append(
                Note(
                    "C7",
                    "warning",
                    "Two gates overlap, so the later one is unreachable",
                    f"line {gate.line} tests {gate.condition.field_name} the same way as "
                    f"line {gate_seen[key]}; whichever is stricter makes the other silent",
                    gate.line,
                )
            )
        else:
            gate_seen[key] = gate.line

    # C8 - a threshold needing almost every point available.
    available = sum(rule.points for rule in policy.score_rules if rule.points > 0)
    if policy.approve_at is not None and available and policy.approve_at > 0.85 * available:
        notes.append(
            Note(
                "C8",
                "serious",
                "The approval threshold is unreachable in practice",
                f"approval needs {policy.approve_at:g} of the {available:g} points this "
                f"policy can award ({policy.approve_at / available:.0%}), so an applicant "
                f"must satisfy nearly every rule at once",
            )
        )

    order = {"critical": 0, "serious": 1, "warning": 2}
    return sorted(notes, key=lambda note: (order[note.severity], note.check, note.line or 0))


def verdict(notes: list[Note]) -> dict[str, object]:
    counts = {"critical": 0, "serious": 0, "warning": 0}
    for note in notes:
        counts[note.severity] += 1
    return {
        "checks_run": len(CHECKS),
        "counts": counts,
        "blocking": counts["critical"] > 0,
        "notes": [note.as_dict() for note in notes],
        "scope": (
            "Structural checks on the policy text only. Passing means these eight "
            "problems are absent, not that the policy is compliant."
        ),
    }
