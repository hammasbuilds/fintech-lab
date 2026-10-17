"""Lint a policy for the things a reviewer would send it back for.

Thirteen checks: eight on how the policy is written, and five on what Pakistan's regulator
caps. They read the syntax tree, not the output, so they catch a problem in a policy that
has never been run - which is the point of having the policy be a document.

The five regulatory checks come from **SECP Circular 10 of 2023** and the pricing circular
that followed it, which govern digital nano lending by NBFCs - the product this project is
written for. The limits are a single borrower capped at Rs 25,000 from one app and Rs 75,000
across all apps, a tenor of no more than 30 days, total recoveries (markup, fees and
penalties together) no greater than the principal, and an APR capped at ten times the policy
rate. Before this, the engine could not express the aggregate cap and the cohort had no
tenor, so three of the five could not be checked even in principle.

R5 deliberately stops short of asserting a policy rate. The rate moves, this file would go
stale, and a lint that quotes a stale number is worse than one that asks: it reports the
APR the policy implies and the minimum policy rate that would make it legal, and leaves the
comparison to whoever has today's number.

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

#: SECP Circular 10 of 2023 and the September 2023 pricing circular, for digital nano
#: lending by NBFCs in Pakistan. Quoted here with their source so a reader can check them
#: rather than trust this file.
#:
#: https://www.secp.gov.pk/document/circular-no-10-of-2023-requirements-for-lending-nbfcs-providing-digital-nano-lending
NANO_SINGLE_APP_CAP = 25_000
NANO_AGGREGATE_CAP = 75_000
NANO_MAX_TENOR_DAYS = 30
#: Total recoveries - markup, fees and penalties together - may not exceed the principal.
NANO_TOTAL_COST_MULTIPLE = 1.0
#: The APR ceiling is a multiple of the State Bank's policy rate rather than a number.
NANO_APR_POLICY_RATE_MULTIPLE = 10

#: The field a policy has to read to keep a borrower inside the aggregate cap. Naming it
#: here rather than in the check keeps the cohort and the lint honest with each other.
AGGREGATE_EXPOSURE_FIELD = "other_app_outstanding"


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
    ("R1", "The limit can exceed the single-app cap", "critical"),
    ("R2", "The policy cannot see another app's loan", "serious"),
    ("R3", "The tenor is longer than nano lending allows", "serious"),
    ("R4", "Total recoveries can exceed the principal", "critical"),
    ("R5", "The APR needs a policy rate to be legal", "warning"),
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


def _limit_cap(policy: Policy) -> float | None:
    """The largest amount this policy can lend, when that is knowable from the text.

    Returns the constant in ``min(<expression>, <cap>)``, or None when the limit has no
    constant ceiling - in which case C5 already has something to say about it.
    """
    from .dsl import Call, Number

    expr = policy.limit
    if not isinstance(expr, Call) or expr.name != "min":
        return None
    constants = [arg.value for arg in expr.args if isinstance(arg, Number)]
    return min(constants) if constants else None


def _declared_term_days(policy: Policy) -> tuple[float | None, str]:
    """How many days the money is out, from the policy's own ``term`` statement."""
    from .dsl import Number

    if policy.term_months is None:
        return None, "the policy states no term"
    if isinstance(policy.term_months, Number):
        return policy.term_months.value * 30.0, f"{policy.term_months.value:g} months"
    return None, f"a term taken from {policy.term_months.render()!r}, which varies per applicant"


def _cost_as_a_share_of_principal(policy: Policy, term_months: float) -> tuple[float, list[str]]:
    """Every charge this policy makes, as a multiple of the limit, with the arithmetic."""
    total = 0.0
    parts: list[str] = []
    for charge in policy.charges:
        if charge.rate is None:
            continue
        months = term_months if charge.period else 1.0
        share = charge.rate * months
        total += share
        parts.append(
            f"{charge.kind} {charge.rate * 100:g}%"
            + (f" x {months:g} months" if charge.period else "")
            + f" = {share * 100:g}% of principal"
        )
    return total, parts


def regulatory(policy: Policy) -> list[Note]:
    """The five nano-lending checks. See the module docstring for the source."""
    notes: list[Note] = []

    # R1 - the single-app cap.
    cap = _limit_cap(policy)
    if cap is not None and cap > NANO_SINGLE_APP_CAP:
        notes.append(
            Note(
                "R1",
                "critical",
                "The limit can exceed the single-app cap",
                f"the limit is capped at {cap:,.0f} and a single digital nano loan may "
                f"not exceed {NANO_SINGLE_APP_CAP:,} (SECP Circular 10 of 2023)",
                None,
            )
        )

    # R2 - the aggregate cap needs a field nobody is reading.
    if AGGREGATE_EXPOSURE_FIELD not in policy.fields_used:
        notes.append(
            Note(
                "R2",
                "serious",
                "The policy cannot see another app's loan",
                f"nothing in this policy reads {AGGREGATE_EXPOSURE_FIELD!r}, so a borrower "
                f"already holding {NANO_AGGREGATE_CAP:,} from other apps can be lent to "
                f"again. The single-app cap can be satisfied while the aggregate one is "
                f"broken, and only this field can tell the difference.",
                None,
            )
        )

    # R3 - the tenor.
    term_days, how = _declared_term_days(policy)
    if term_days is not None and term_days > NANO_MAX_TENOR_DAYS:
        notes.append(
            Note(
                "R3",
                "serious",
                "The tenor is longer than nano lending allows",
                f"the policy prices over {how}, about {term_days:,.0f} days, and the tenor "
                f"for digital nano lending is capped at {NANO_MAX_TENOR_DAYS} days",
                None,
            )
        )

    # R4 and R5 - what it all costs.
    # When the term is a field rather than a number, the cost is computed at the 30-day
    # cap and said so: a per-applicant term can only make it larger, never smaller, so
    # this is the most favourable reading of the policy rather than a guess.
    at_the_cap = term_days is None
    months_for_cost = (term_days or float(NANO_MAX_TENOR_DAYS)) / 30.0
    total, parts = _cost_as_a_share_of_principal(policy, months_for_cost)
    if total > NANO_TOTAL_COST_MULTIPLE:
        notes.append(
            Note(
                "R4",
                "critical",
                "Total recoveries can exceed the principal",
                f"charges add up to {total * 100:,.0f}% of the principal over "
                f"{months_for_cost:g} months ({'; '.join(parts)}), and total recoveries "
                f"may not exceed the principal"
                + (
                    f". {how}, so this is the figure at the {NANO_MAX_TENOR_DAYS}-day cap "
                    f"and a longer tenor multiplies it"
                    if at_the_cap
                    else ""
                ),
                None,
            )
        )
    if total > 0 and months_for_cost > 0:
        apr = total * (12.0 / months_for_cost)
        implied = apr / NANO_APR_POLICY_RATE_MULTIPLE
        notes.append(
            Note(
                "R5",
                "warning",
                "The APR needs a policy rate to be legal",
                f"these charges imply an APR of about {apr * 100:,.0f}% over "
                f"{months_for_cost:g} months. The ceiling is "
                f"{NANO_APR_POLICY_RATE_MULTIPLE}x the policy rate, so this is inside it "
                f"only while the policy rate is at least {implied * 100:,.1f}%. This lint "
                f"does not hold a policy rate on purpose - it would go stale."
                + (
                    f" Computed at the {NANO_MAX_TENOR_DAYS}-day cap, because {how}."
                    if at_the_cap
                    else ""
                ),
                None,
            )
        )
    return notes


def review(policy: Policy) -> list[Note]:
    """Every note this policy earns, worst first."""
    notes: list[Note] = list(regulatory(policy))

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
