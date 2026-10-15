"""Sweep the approval threshold, and report discrimination separately from the cutoff.

The first round of this project produced a sharp finding and then left the obvious
follow-up unanswered: the v2 cutoff declines mostly good borrowers, so **where should the
cutoff be?** A sweep answers that directly - score every borrower once, then re-decide at
every candidate threshold - and it is cheap, because the scorecard does not depend on the
cutoff at all.

Two things are kept apart on purpose, because conflating them is how a scorecard gets
blamed for a cutoff or a cutoff for a scorecard:

* **Discrimination** - how well the score separates borrowers who repay from borrowers who
  do not, as Gini and KS. A property of the scorecard. Moving the cutoff cannot change it.
* **The cutoff** - what the business gives up and gains at each point on that curve. A
  property of the decision, which is what the sweep is for.

The net-maximising threshold this prints is **in-sample**. It is chosen on the same
borrowers it is evaluated on, so it is the best cutoff for the past and an optimistic
estimate for the future. The output says so next to the number, because a single
"optimal threshold" with no such caveat is the most quotable wrong figure a sandbox can
produce.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cohort import LOSS_GIVEN_DEFAULT, Borrower
from .dsl import Policy
from .engine import Decision, evaluate


@dataclass
class Scored:
    """One borrower, scored once. Re-deciding at a new threshold needs nothing else."""

    borrower: Borrower
    decision: Decision
    gated: bool

    @property
    def score(self) -> float:
        return self.decision.score


def score_all(policy: Policy, borrowers: list[Borrower]) -> list[Scored]:
    out: list[Scored] = []
    for borrower in borrowers:
        decision = evaluate(policy, borrower.record())
        out.append(Scored(borrower, decision, decision.gate is not None))
    return out


# -------------------------------------------------------------- discrimination


def discrimination(scored: list[Scored]) -> dict[str, object]:
    """Gini and KS of the score against the realised default label.

    Measured on the **scored** population only. Gated applicants never received a score,
    so including them would be measuring the gates and calling it the scorecard. The
    excluded count is reported rather than dropped, because that exclusion biases the
    figure upwards whenever the gates remove bad borrowers - which is what gates are for.
    """
    pool = [item for item in scored if not item.gated]
    excluded = len(scored) - len(pool)
    bads = [item for item in pool if item.borrower.would_default]
    goods = [item for item in pool if not item.borrower.would_default]
    if not bads or not goods:
        return {
            "withheld": (
                "the scored population contains only one outcome class, so a ranking "
                "measure has nothing to rank"
            ),
            "scored": len(pool),
            "gated_out": excluded,
            "bads": len(bads),
            "goods": len(goods),
        }

    # AUC by the rank-sum identity, with ties shared. A low score should mean a high
    # chance of default, so the score is negated to make "higher is worse" the direction
    # the statistic expects.
    values = sorted((-item.score, item.borrower.would_default) for item in pool)
    ranks: dict[float, list[int]] = {}
    for position, (value, _label) in enumerate(values, start=1):
        ranks.setdefault(value, []).append(position)
    average_rank = {value: sum(positions) / len(positions) for value, positions in ranks.items()}
    rank_sum_bad = sum(average_rank[-item.score] for item in bads)
    n_bad, n_good = len(bads), len(goods)
    auc = (rank_sum_bad - n_bad * (n_bad + 1) / 2) / (n_bad * n_good)

    # KS: the largest gap between the two cumulative distributions over the score.
    thresholds = sorted({item.score for item in pool})
    ks = 0.0
    ks_at = thresholds[0] if thresholds else 0.0
    for threshold in thresholds:
        bad_below = sum(1 for item in bads if item.score <= threshold) / n_bad
        good_below = sum(1 for item in goods if item.score <= threshold) / n_good
        gap = abs(bad_below - good_below)
        if gap > ks:
            ks, ks_at = gap, threshold

    return {
        "scored": len(pool),
        "gated_out": excluded,
        "bads": n_bad,
        "goods": n_good,
        "auc": round(auc, 4),
        "gini": round(2 * auc - 1, 4),
        "ks": round(ks, 4),
        "ks_at_score": ks_at,
        "reading": _read_gini(2 * auc - 1),
        "caveat": (
            f"Measured on the {len(pool):,} borrowers who reached the scorecard. The "
            f"{excluded:,} stopped by a gate are excluded, which flatters the figure "
            f"whenever the gates are removing bad borrowers."
        ),
    }


def _read_gini(gini: float) -> str:
    if gini < 0:
        return (
            "Negative: the score ranks borrowers the wrong way round. Check the sign of "
            "the scoring rules before reading anything else here."
        )
    if gini < 0.2:
        return "Close to no discrimination; the score is barely separating the two groups."
    if gini < 0.4:
        return "Weak separation. A cutoff on this score will cut good and bad alike."
    if gini < 0.6:
        return "Moderate separation, which is a normal range for thin-file scorecards."
    return "Strong separation on this cohort. Expect less on borrowers it has not seen."


# --------------------------------------------------------------------- sweeping


@dataclass
class Point:
    threshold: float
    approvals: int
    #: Two denominators, both named. The share of the *scored* population is what the
    #: cutoff actually controls; the share of *all* applicants is the number the rest of
    #: the project reports, and it is smaller because the gates already removed people
    #: the cutoff never sees. One unlabelled "approval rate" across the two is how a
    #: 63% and a 32% end up in the same conversation.
    approval_rate_of_scored: float
    approval_rate_of_all: float
    bad_rate: float
    disbursed: float
    fee_revenue: float
    expected_loss: float
    net: float
    good_declined: int
    bad_declined: int

    def as_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "approvals": self.approvals,
            "approval_rate_of_scored": round(self.approval_rate_of_scored, 4),
            "approval_rate_of_all": round(self.approval_rate_of_all, 4),
            "bad_rate": round(self.bad_rate, 4),
            "disbursed": round(self.disbursed, 2),
            "fee_revenue": round(self.fee_revenue, 2),
            "expected_loss": round(self.expected_loss, 2),
            "net": round(self.net, 2),
            "good_declined": self.good_declined,
            "bad_declined": self.bad_declined,
        }


def sweep(policy: Policy, borrowers: list[Borrower], step: float = 5.0) -> dict[str, object]:
    """Re-decide at every threshold from the lowest score to the highest."""
    scored = score_all(policy, borrowers)
    pool = [item for item in scored if not item.gated]
    if not pool:
        return {"withheld": "every applicant was stopped by a gate, so the cutoff does nothing"}

    limits = {id(item): item.decision.limit for item in pool}
    # The limit expression can reference the score, so a borrower's limit is re-derived
    # at each threshold rather than reused from the policy's own cutoff.
    charges = policy.charges

    low = min(item.score for item in pool)
    high = max(item.score for item in pool)
    thresholds: list[float] = []
    value = (low // step) * step
    while value <= high + step:
        thresholds.append(round(value, 4))
        value += step

    points: list[Point] = []
    for threshold in thresholds:
        approvals = 0
        disbursed = fee_revenue = expected_loss = 0.0
        defaults = good_declined = bad_declined = 0
        for item in pool:
            if item.score >= threshold:
                approvals += 1
                limit = limits[id(item)] or _limit_at(policy, item, threshold)
                disbursed += limit
                for charge in charges:
                    if charge.period is None:
                        amount = (
                            charge.flat if charge.flat is not None else (charge.rate or 0.0) * limit
                        )
                        fee_revenue += amount
                if item.borrower.would_default:
                    defaults += 1
                    expected_loss += limit * LOSS_GIVEN_DEFAULT
            elif item.borrower.would_default:
                bad_declined += 1
            else:
                good_declined += 1
        points.append(
            Point(
                threshold=threshold,
                approvals=approvals,
                approval_rate_of_scored=approvals / len(pool),
                approval_rate_of_all=approvals / len(scored),
                bad_rate=defaults / approvals if approvals else 0.0,
                disbursed=disbursed,
                fee_revenue=fee_revenue,
                expected_loss=expected_loss,
                net=fee_revenue - expected_loss,
                good_declined=good_declined,
                bad_declined=bad_declined,
            )
        )

    best = max(points, key=lambda point: point.net)
    current = min(points, key=lambda point: abs(point.threshold - (policy.approve_at or 0.0)))
    return {
        "step": step,
        "applications": len(scored),
        "scored_population": len(pool),
        "gated_out": len(scored) - len(pool),
        "points": [point.as_dict() for point in points],
        "current_threshold": policy.approve_at,
        "current": current.as_dict(),
        "net_maximising": best.as_dict(),
        "net_gain_available": round(best.net - current.net, 2),
        "in_sample_warning": (
            f"The net-maximising threshold of {best.threshold:g} is chosen on the same "
            f"{len(pool):,} borrowers it is scored against. It is the best cutoff for this "
            f"cohort's past, and an optimistic estimate for anyone it has not seen. Treat "
            f"the shape of the curve as the finding, not the argmax."
        ),
    }


def _limit_at(policy: Policy, item: Scored, threshold: float) -> float:
    """Re-derive a limit for a borrower who is approved at ``threshold`` but was not at
    the policy's own cutoff, so the sweep does not quietly disburse zero to them."""
    from .engine import _expr

    del threshold
    return _expr(policy.limit, item.borrower.record(), item.score)
