"""Run a policy over the whole cohort and report what it would have done.

The report deliberately includes the two figures a dashboard usually leaves out:

* **What the declines cost.** Every policy can cut its bad rate by declining more. The
  only way to see the price of that is to count the borrowers it declined who would
  have repaid, which the sandbox can do and a live portfolio never can.
* **Whether a tightening is paid for.** A version comparison reports the swing in
  approvals, in bad rate *and* in money, because the first two move in opposite
  directions and either one alone makes any tightening look like a win.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cohort import LOSS_GIVEN_DEFAULT
from .dsl import Policy
from .engine import Decision, evaluate
from .records import Record

SEGMENTS = ("region", "employment")


@dataclass
class Outcome:
    borrower: Record
    decision: Decision


@dataclass
class Backtest:
    policy_name: str
    policy_version: str
    currency: str
    #: False when the cohort has no outcome column. Everything derived from a known
    #: outcome - bad rate, expected loss, net, the cost of the declines - is then
    #: withheld rather than computed against a label that is absent. Filling a missing
    #: label with zeros and reporting a 0% bad rate is the worst available option.
    labelled: bool = True
    applications: int = 0
    approvals: int = 0
    refers: int = 0
    declines: int = 0
    disbursed: float = 0.0
    fee_revenue: float = 0.0
    #: Interest actually expected to be collected over the policy's stated term, after
    #: the default-timing assumption below. Counted in ``net``.
    interest_revenue: float = 0.0
    #: Interest the policy would have contracted but is not expected to collect, because
    #: the borrower defaults part-way through the term.
    interest_lost_to_default: float = 0.0
    #: Penalty charges that are priced per unit of time - a late fee. Never counted in
    #: ``net``: nobody in a backtest has been late yet, so treating it as earned would
    #: book revenue that depends on an event this cohort has no data about.
    time_priced_revenue: float = 0.0
    expected_loss: float = 0.0
    defaults_among_approved: int = 0
    declined_who_would_repay: int = 0
    declined_who_would_default: int = 0
    decline_reasons: dict[str, int] = field(default_factory=dict)
    score_histogram: list[dict[str, object]] = field(default_factory=list)
    segments: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    income_bands: list[dict[str, object]] = field(default_factory=list)

    @property
    def approval_rate(self) -> float:
        return self.approvals / self.applications if self.applications else 0.0

    @property
    def refer_rate(self) -> float:
        return self.refers / self.applications if self.applications else 0.0

    @property
    def bad_rate(self) -> float:
        return self.defaults_among_approved / self.approvals if self.approvals else 0.0

    @property
    def loss_rate(self) -> float:
        return self.expected_loss / self.disbursed if self.disbursed else 0.0

    @property
    def net(self) -> float:
        """Contracted revenue less expected loss.

        Contracted means the one-off fee plus the interest expected to be collected.
        Late fees
        are excluded on purpose - see :attr:`time_priced_revenue`. An earlier version
        split revenue by whether a charge had a period at all, which put contracted
        interest in the same bucket as a penalty and reported a policy that earns 82
        million as making less than one that earns 3.
        """
        return self.fee_revenue + self.interest_revenue - self.expected_loss

    @property
    def average_limit(self) -> float:
        return self.disbursed / self.approvals if self.approvals else 0.0

    def as_dict(self) -> dict[str, object]:
        outcome_figures: dict[str, object] = {
            "bad_rate": round(self.bad_rate, 4),
            "loss_rate": round(self.loss_rate, 4),
            "expected_loss": round(self.expected_loss, 2),
            "net": round(self.net, 2),
            "defaults_among_approved": self.defaults_among_approved,
            "declined_who_would_repay": self.declined_who_would_repay,
            "declined_who_would_default": self.declined_who_would_default,
        }
        if not self.labelled:
            outcome_figures = dict.fromkeys(outcome_figures)
        return {
            "labelled": self.labelled,
            "withheld": None
            if self.labelled
            else (
                "This cohort has no outcome column, so bad rate, expected loss, net and "
                "the cost of the declines cannot be computed and are left empty."
            ),
            "policy": {
                "name": self.policy_name,
                "version": self.policy_version,
                "currency": self.currency,
            },
            "applications": self.applications,
            "approvals": self.approvals,
            "refers": self.refers,
            "declines": self.declines,
            "approval_rate": round(self.approval_rate, 4),
            "refer_rate": round(self.refer_rate, 4),
            "disbursed": round(self.disbursed, 2),
            "average_limit": round(self.average_limit, 2),
            "fee_revenue": round(self.fee_revenue, 2),
            "interest_revenue": round(self.interest_revenue, 2),
            "interest_lost_to_default": round(self.interest_lost_to_default, 2),
            "time_priced_revenue": round(self.time_priced_revenue, 2),
            **outcome_figures,
            "decline_reasons": self.decline_reasons,
            "score_histogram": self.score_histogram,
            "segments": self.segments,
            "income_bands": self.income_bands,
        }


def run(
    policy: Policy,
    borrowers: list[Record],
    *,
    labelled: bool = True,
    segments: tuple[str, ...] = SEGMENTS,
) -> tuple[Backtest, list[Outcome]]:
    """Score every borrower and aggregate. Returns the report and the per-borrower rows.

    ``labelled`` is False for a supplied cohort with no outcome column; see
    :class:`Backtest`.

    ``segments`` names the attributes to break approvals down by. It is a parameter and
    not a constant because the synthetic cohort and the real books have different
    columns - the real loan book has no region - and a hard-coded pair of names is how
    this function used to refuse to run on anything but the cohort it was written for.
    Each name must be an attribute of the records, or this raises.
    """
    for name in segments:
        if borrowers and not hasattr(borrowers[0], name):
            raise AttributeError(
                f"cannot segment by {name!r}: these records have "
                f"{', '.join(sorted(vars(borrowers[0])))}"
            )
    outcomes = [Outcome(borrower, evaluate(policy, borrower.record())) for borrower in borrowers]
    report = Backtest(
        policy_name=policy.name,
        policy_version=str(policy.version),
        currency=policy.currency,
        labelled=labelled,
    )
    report.applications = len(outcomes)
    reasons: dict[str, int] = {}

    for outcome in outcomes:
        decision = outcome.decision
        if decision.outcome == "approve":
            report.approvals += 1
            report.disbursed += decision.limit
            for charge in decision.charges:
                amount = float(charge["amount"])  # type: ignore[arg-type]
                if charge["kind"] == "interest":
                    # A borrower who defaults stops paying, so booking the whole term's
                    # interest against them would be revenue nobody collects. The
                    # assumption - stated here because it moves the answer - is that a
                    # default lands halfway through the term, so half the interest is
                    # collected and half is lost. It is a single number and it is
                    # arguable; what is not arguable is that the figure is not the full
                    # term, which is what this used to book.
                    collected = amount * (0.5 if outcome.borrower.would_default else 1.0)
                    report.interest_revenue += collected
                    report.interest_lost_to_default += amount - collected
                elif charge["period"]:
                    report.time_priced_revenue += amount
                else:
                    report.fee_revenue += amount
            if outcome.borrower.would_default:
                report.defaults_among_approved += 1
                report.expected_loss += decision.limit * LOSS_GIVEN_DEFAULT
        else:
            if decision.outcome == "refer":
                report.refers += 1
            else:
                report.declines += 1
            if outcome.borrower.would_default:
                report.declined_who_would_default += 1
            else:
                report.declined_who_would_repay += 1
            primary = decision.gate["reason"] if decision.gate else "score below the threshold"
            reasons[str(primary)] = reasons.get(str(primary), 0) + 1

    report.decline_reasons = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
    report.score_histogram = _score_histogram(outcomes, policy)
    report.segments = {name: _segment(outcomes, name) for name in segments}
    report.income_bands = _income_bands(outcomes)
    return report, outcomes


def _score_histogram(outcomes: list[Outcome], policy: Policy) -> list[dict[str, object]]:
    """Scores in buckets of ten, each carrying how much of it the threshold approves.

    A bucket is not simply approved or declined. A threshold of 45 cuts the 40-49 bucket
    in half, and labelling the whole bucket "declined" hides the borrowers inside it who
    were approved - which matters here, because the bucket straddling the threshold is
    exactly the one an analyst is looking at when they argue about moving it.
    """
    buckets: dict[int, dict[str, int]] = {}
    for outcome in outcomes:
        if outcome.decision.gate is not None:
            continue  # gated out before scoring; a zero here would be an invented score
        bucket = int(outcome.decision.score // 10) * 10
        slot = buckets.setdefault(bucket, {"count": 0, "defaults": 0, "approved": 0})
        slot["count"] += 1
        slot["defaults"] += outcome.borrower.would_default
        slot["approved"] += int(outcome.decision.approved)
    rows: list[dict[str, object]] = []
    for bucket, slot in sorted(buckets.items()):
        if slot["approved"] == slot["count"]:
            standing = "all approved"
        elif slot["approved"] == 0:
            standing = "all declined"
        else:
            standing = "split by the threshold"
        rows.append(
            {
                "from": bucket,
                "to": bucket + 9,
                "count": slot["count"],
                "defaults": slot["defaults"],
                "approved": slot["approved"],
                "default_rate": round(slot["defaults"] / slot["count"], 4)
                if slot["count"]
                else 0.0,
                "standing": standing,
            }
        )
    del policy
    return rows


def _segment(outcomes: list[Outcome], attribute: str) -> list[dict[str, object]]:
    groups: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        key = str(getattr(outcome.borrower, attribute))
        groups.setdefault(key, []).append(outcome)
    rows: list[dict[str, object]] = []
    for key, group in sorted(groups.items()):
        approved = [o for o in group if o.decision.approved]
        rows.append(
            {
                "key": key,
                "applications": len(group),
                "approvals": len(approved),
                "approval_rate": round(len(approved) / len(group), 4),
                "bad_rate": round(
                    sum(o.borrower.would_default for o in approved) / len(approved), 4
                )
                if approved
                else None,
                "median_income": sorted(o.borrower.monthly_income for o in group)[len(group) // 2],
                "average_limit": round(sum(o.decision.limit for o in approved) / len(approved), 2)
                if approved
                else 0.0,
            }
        )
    return rows


def _income_bands(outcomes: list[Outcome], bands: int = 5) -> list[dict[str, object]]:
    """Approval rate by income band, so a region gap can be read inside a band."""
    ordered = sorted(outcomes, key=lambda o: o.borrower.monthly_income)
    size = max(1, len(ordered) // bands)
    rows: list[dict[str, object]] = []
    for index in range(bands):
        start = index * size
        end = len(ordered) if index == bands - 1 else (index + 1) * size
        chunk = ordered[start:end]
        if not chunk:
            continue
        approved = [o for o in chunk if o.decision.approved]
        rows.append(
            {
                "band": index + 1,
                "income_from": chunk[0].borrower.monthly_income,
                "income_to": chunk[-1].borrower.monthly_income,
                "applications": len(chunk),
                "approval_rate": round(len(approved) / len(chunk), 4),
                "bad_rate": round(
                    sum(o.borrower.would_default for o in approved) / len(approved), 4
                )
                if approved
                else None,
            }
        )
    return rows


@dataclass
class Comparison:
    base: dict[str, object]
    candidate: dict[str, object]
    newly_approved: int
    newly_declined: int
    unchanged: int
    newly_declined_who_would_repay: int
    newly_declined_who_would_default: int
    newly_declined_by_segment: dict[str, dict[str, int]]
    examples: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        base_rate = float(self.base["approval_rate"])  # type: ignore[arg-type]
        candidate_rate = float(self.candidate["approval_rate"])  # type: ignore[arg-type]
        labelled = bool(self.base["labelled"])
        base_bad = float(self.base["bad_rate"] or 0.0) if labelled else 0.0
        candidate_bad = float(self.candidate["bad_rate"] or 0.0) if labelled else 0.0
        return {
            "base": self.base,
            "candidate": self.candidate,
            "newly_approved": self.newly_approved,
            "newly_declined": self.newly_declined,
            "unchanged": self.unchanged,
            "newly_declined_by_segment": self.newly_declined_by_segment,
            "examples": self.examples,
            "labelled": labelled,
            "delta": {
                "approval_rate_points": round(100 * (candidate_rate - base_rate), 2),
                "bad_rate_points": round(100 * (candidate_bad - base_bad), 2) if labelled else None,
                "net": round(
                    float(self.candidate["net"]) - float(self.base["net"]),  # type: ignore[arg-type]
                    2,
                )
                if labelled
                else None,
                "disbursed": round(
                    float(self.candidate["disbursed"]) - float(self.base["disbursed"]),  # type: ignore[arg-type]
                    2,
                ),
            },
            # Precision needs a known outcome. Without one the numerator is a count of
            # zeros, which would report every tightening as perfectly imprecise.
            "precision_of_the_tightening": (
                round(self.newly_declined_who_would_default / self.newly_declined, 4)
                if self.newly_declined and labelled
                else None
            ),
            "newly_declined_who_would_repay": self.newly_declined_who_would_repay
            if labelled
            else None,
            "newly_declined_who_would_default": self.newly_declined_who_would_default
            if labelled
            else None,
        }


def compare(
    base: Policy, candidate: Policy, borrowers: list[Record], *, labelled: bool = True
) -> Comparison:
    """What changing from ``base`` to ``candidate`` does, borrower by borrower."""
    base_report, base_outcomes = run(base, borrowers, labelled=labelled)
    candidate_report, candidate_outcomes = run(candidate, borrowers, labelled=labelled)

    newly_approved = newly_declined = unchanged = 0
    would_repay = would_default = 0
    by_segment: dict[str, dict[str, int]] = {name: {} for name in SEGMENTS}
    examples: list[dict[str, object]] = []

    for before, after in zip(base_outcomes, candidate_outcomes, strict=True):
        was, now = before.decision.approved, after.decision.approved
        if was == now:
            unchanged += 1
            continue
        if now:
            newly_approved += 1
            continue
        newly_declined += 1
        if before.borrower.would_default:
            would_default += 1
        else:
            would_repay += 1
        for name in SEGMENTS:
            key = str(getattr(before.borrower, name))
            by_segment[name][key] = by_segment[name].get(key, 0) + 1
        if len(examples) < 8:
            examples.append(
                {
                    "id": before.borrower.id,
                    "monthly_income": before.borrower.monthly_income,
                    "wallet_months": before.borrower.wallet_months,
                    "txn_velocity": before.borrower.txn_velocity,
                    "region": before.borrower.region,
                    "employment": before.borrower.employment,
                    "was": before.decision.outcome,
                    "now": after.decision.outcome,
                    "why": after.decision.reasons[0] if after.decision.reasons else "",
                    "would_have_repaid": not before.borrower.would_default,
                }
            )

    return Comparison(
        base=base_report.as_dict(),
        candidate=candidate_report.as_dict(),
        newly_approved=newly_approved,
        newly_declined=newly_declined,
        unchanged=unchanged,
        newly_declined_who_would_repay=would_repay,
        newly_declined_who_would_default=would_default,
        newly_declined_by_segment={k: dict(sorted(v.items())) for k, v in by_segment.items()},
        examples=examples,
    )
