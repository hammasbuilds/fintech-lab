"""What a cohort with dates makes possible: vintages, stability, and out-of-time honesty.

Everything in this project used to happen at the same instant. A cohort without dates can
be backtested and swept, and it cannot answer the three questions a credit team asks after
a policy ships:

**Vintage curves.** Default rate by months on book, per origination cohort. A book three
months old always looks better than it is, because defaults have not had time to arrive -
so comparing January's bad rate with June's is comparing a six-month-old vintage with a
one-month-old one. The curve is how a lender sees a policy change working before the annual
numbers arrive.

**Population stability.** The applicants change while the scorecard stands still. PSI
compares the score distribution of a reference window with a later one; the conventional
reading is that **under 0.10** is stable, **0.10 to 0.25** needs looking at, and **over
0.25** means the population has moved far enough that the score was fitted on a different
one. The thresholds are convention rather than theory, and they are quoted here as
convention.

**Out-of-time validation.** :mod:`sweep` already warns that its net-maximising cutoff is
chosen on the same borrowers it is scored against. With an origination window that warning
becomes a measurement: choose the cutoff on the early months, apply it to the late ones,
and the difference between the gain it promised and the gain it delivered is the optimism.
That number is the one worth publishing, because every in-sample optimum has it and almost
nobody reports it.
"""

from __future__ import annotations

from .cohort import LOSS_GIVEN_DEFAULT, WINDOW_MONTHS
from .dsl import Policy
from .engine import evaluate
from .records import Record
from .sweep import score_all, sweep

#: Where the early window ends. Eighteen of twenty-four months leaves six months to test
#: on, which is the shortest span over which a bad rate means anything.
SPLIT_MONTH = 18

#: The conventional PSI reading. Not a law of nature; a convention, named as one.
PSI_BANDS = (
    (0.10, "stable"),
    (0.25, "worth looking at"),
    (float("inf"), "the population has moved"),
)


def _months_on_book(borrower: Record) -> int:
    """How long this borrower's loan has had to go bad, at the end of the window."""
    return WINDOW_MONTHS - int(getattr(borrower, "origination_month", 0))


def vintage_curves(policy: Policy, borrowers: list[Record], quarters: int = 8) -> dict[str, object]:
    """Default rate by months on book, for each origination quarter.

    Only approved borrowers appear: a declined applicant has no book to be on. That is
    also what makes the curves honest - they are what the lender would see.
    """
    rows: dict[int, dict[int, list[int]]] = {}
    approved = 0
    for borrower in borrowers:
        decision = evaluate(policy, borrower.record())
        if decision.outcome != "approve":
            continue
        approved += 1
        month = int(getattr(borrower, "origination_month", 0))
        quarter = min(quarters - 1, month // max(1, WINDOW_MONTHS // quarters))
        on_book = _months_on_book(borrower)
        bucket = rows.setdefault(quarter, {})
        went_bad = int(getattr(borrower, "months_to_default", 0) or 0)
        for age in range(1, on_book + 1):
            slot = bucket.setdefault(age, [0, 0])
            slot[1] += 1
            if went_bad and went_bad <= age:
                slot[0] += 1

    curves = []
    for quarter in sorted(rows):
        points = []
        for age in sorted(rows[quarter]):
            bad, total = rows[quarter][age]
            points.append(
                {
                    "months_on_book": age,
                    "loans": total,
                    "defaulted_by_now": bad,
                    "rate": round(bad / total, 4) if total else None,
                }
            )
        curves.append(
            {
                "origination_quarter": quarter + 1,
                "months": f"{quarter * (WINDOW_MONTHS // quarters)}"
                f"-{(quarter + 1) * (WINDOW_MONTHS // quarters) - 1}",
                "points": points,
            }
        )
    return {
        "approved_loans": approved,
        "quarters": len(curves),
        "curves": curves,
        "reading": (
            "Read down a column, not across a row. The youngest vintage has had the least "
            "time to go bad, so its headline rate is lower for a reason that has nothing "
            "to do with the policy."
        ),
    }


def psi(
    policy: Policy,
    borrowers: list[Record],
    *,
    split: int = SPLIT_MONTH,
    bins: int = 10,
) -> dict[str, object]:
    """Population stability on the score, early window against late."""
    scored = score_all(policy, borrowers)
    early = [
        item.score for item in scored if int(getattr(item.borrower, "origination_month", 0)) < split
    ]
    late = [
        item.score
        for item in scored
        if int(getattr(item.borrower, "origination_month", 0)) >= split
    ]
    if len(early) < bins * 10 or len(late) < bins * 10:
        return {
            "withheld": True,
            "why": (
                f"one window has too few scored applicants to bin: {len(early):,} early and "
                f"{len(late):,} late, against a floor of {bins * 10:,} each"
            ),
        }

    ordered = sorted(early)
    edges = [ordered[int(len(ordered) * (index + 1) / bins) - 1] for index in range(bins - 1)]

    def bucket(value: float) -> int:
        for index, edge in enumerate(edges):
            if value <= edge:
                return index
        return bins - 1

    early_counts = [0] * bins
    late_counts = [0] * bins
    for value in early:
        early_counts[bucket(value)] += 1
    for value in late:
        late_counts[bucket(value)] += 1

    total = 0.0
    rows = []
    for index in range(bins):
        # A zero in either window would make the log undefined, so an empty bucket is
        # floored at one applicant rather than dropped - dropping it would quietly
        # shrink the index towards stability.
        reference = max(early_counts[index], 1) / len(early)
        current = max(late_counts[index], 1) / len(late)
        contribution = (current - reference) * (__import__("math").log(current / reference))
        total += contribution
        rows.append(
            {
                "bucket": index + 1,
                "early_share": round(reference, 4),
                "late_share": round(current, 4),
                "contribution": round(contribution, 5),
            }
        )

    verdict = next(label for threshold, label in PSI_BANDS if total < threshold)
    return {
        "withheld": False,
        "split_month": split,
        "bins": bins,
        "early_applicants": len(early),
        "late_applicants": len(late),
        "psi": round(total, 4),
        "reading": verdict,
        "bands": "under 0.10 stable, 0.10-0.25 worth looking at, over 0.25 moved",
        "buckets": rows,
        "note": (
            "Scored applicants only. A borrower stopped by a gate never reaches the "
            "scorecard, so including them would measure the gates rather than the score."
        ),
    }


def characteristic_psi(
    borrowers: list[Record], fields: tuple[str, ...], *, split: int = SPLIT_MONTH, bins: int = 10
) -> dict[str, object]:
    """PSI per input field, which is where a population shift shows up first.

    A scorecard turns several fields into one coarse number, so a shift that is plain in
    the inputs can be almost invisible in the score - the score moves in steps of ten
    points and the inputs move continuously. Reporting only the score's PSI is how a
    drifting population gets called stable.
    """
    out: dict[str, object] = {}
    for field in fields:
        early = [
            float(getattr(b, field))
            for b in borrowers
            if int(getattr(b, "origination_month", 0)) < split
            and isinstance(getattr(b, field, None), (int, float))
        ]
        late = [
            float(getattr(b, field))
            for b in borrowers
            if int(getattr(b, "origination_month", 0)) >= split
            and isinstance(getattr(b, field, None), (int, float))
        ]
        if len(early) < bins * 10 or len(late) < bins * 10:
            out[field] = {"withheld": True, "why": "too few applicants in one window"}
            continue
        out[field] = _psi_between(early, late, bins)
    return out


def _psi_between(early: list[float], late: list[float], bins: int) -> dict[str, object]:
    import math

    ordered = sorted(early)
    edges = [ordered[int(len(ordered) * (index + 1) / bins) - 1] for index in range(bins - 1)]

    def bucket(value: float) -> int:
        for index, edge in enumerate(edges):
            if value <= edge:
                return index
        return bins - 1

    early_counts = [0] * bins
    late_counts = [0] * bins
    for value in early:
        early_counts[bucket(value)] += 1
    for value in late:
        late_counts[bucket(value)] += 1

    total = 0.0
    for index in range(bins):
        reference = max(early_counts[index], 1) / len(early)
        current = max(late_counts[index], 1) / len(late)
        total += (current - reference) * math.log(current / reference)
    verdict = next(label for threshold, label in PSI_BANDS if total < threshold)
    return {"withheld": False, "psi": round(total, 4), "reading": verdict}


def out_of_time(
    policy: Policy, borrowers: list[Record], *, split: int = SPLIT_MONTH, step: float = 5.0
) -> dict[str, object]:
    """Choose the cutoff on the early window; see what it earns on the late one.

    This is the measurement that :mod:`sweep`'s in-sample warning asks for. Three numbers
    matter: what the early window promised, what that same cutoff actually earned later,
    and what the best cutoff for the later window would have earned. The gap between the
    first two is the optimism, and the gap between the second and third is what chasing
    the in-sample optimum cost.
    """
    early = [b for b in borrowers if int(getattr(b, "origination_month", 0)) < split]
    late = [b for b in borrowers if int(getattr(b, "origination_month", 0)) >= split]
    if not early or not late:
        return {"withheld": True, "why": "the cohort has no origination window"}

    early_sweep = sweep(policy, early, step=step)
    chosen = float(early_sweep["net_maximising"]["threshold"])

    def at(threshold: float, pool: list[Record]) -> dict[str, object]:
        scored = score_all(policy, pool)
        approvals = disbursed = loss = 0.0
        fee = 0.0
        bads = 0
        for item in scored:
            if item.score < threshold:
                continue
            decision = evaluate(policy, item.borrower.record())
            limit = decision.limit or 0.0
            approvals += 1
            disbursed += limit
            for charge in decision.charges:
                if charge["kind"] != "late_fee":
                    fee += float(charge["amount"])  # type: ignore[arg-type]
            if item.borrower.would_default:
                bads += 1
                loss += limit * LOSS_GIVEN_DEFAULT
        return {
            "threshold": threshold,
            "approvals": int(approvals),
            "approval_rate": round(approvals / len(pool), 4) if pool else None,
            "bad_rate": round(bads / approvals, 4) if approvals else None,
            "disbursed": round(disbursed, 2),
            "net": round(fee - loss, 2),
            "net_per_loan": round((fee - loss) / approvals, 2) if approvals else None,
        }

    # Both windows are measured by the function above rather than one by the sweep and
    # one by this module. The sweep re-derives a limit for borrowers it approves at a
    # threshold the policy itself would not have, so its net and this one are not the same
    # arithmetic - and comparing them made the out-of-time figure come out *better* than
    # the in-sample one, which is not a thing that happens.
    promised = at(chosen, early)
    delivered = at(chosen, late)
    thresholds = [chosen]
    value = float(early_sweep["points"][0]["threshold"]) if early_sweep["points"] else chosen
    for point in early_sweep["points"]:
        thresholds.append(float(point["threshold"]))
    del value
    best_late = max(
        (at(threshold, late) for threshold in sorted(set(thresholds))),
        key=lambda row: float(row["net"]),
    )

    per_loan_promised = (
        float(promised["net"]) / float(promised["approvals"]) if promised["approvals"] else None
    )
    per_loan_delivered = (
        float(delivered["net"]) / float(delivered["approvals"])  # type: ignore[arg-type]
        if delivered["approvals"]
        else None
    )
    return {
        "withheld": False,
        "split_month": split,
        "early_applicants": len(early),
        "late_applicants": len(late),
        "cutoff_chosen_on_the_early_window": chosen,
        "promised_in_sample": promised,
        "delivered_out_of_time": delivered,
        "best_for_the_late_window": best_late,
        "net_per_loan_promised": round(per_loan_promised, 2) if per_loan_promised else None,
        "net_per_loan_delivered": round(per_loan_delivered, 2) if per_loan_delivered else None,
        "optimism_per_loan": (
            round(per_loan_promised - per_loan_delivered, 2)
            if (per_loan_promised is not None and per_loan_delivered is not None)
            else None
        ),
        "reading": (
            "The two windows hold different numbers of applicants, so the totals are not "
            "comparable and the per-loan figures are. A positive optimism is the amount "
            "per loan that the in-sample optimum overstated - the usual cost of choosing "
            "a cutoff on the same borrowers you then report it against. A *negative* one "
            "means the later book performed better than the cutoff promised, and on this "
            "cohort that is what happens: the population drifts towards borrowers already "
            "carrying another app's loan, the policy gates on exactly that, and the "
            "applicants who survive are a more selected group. What falls is volume, not "
            "quality - compare the approval rates, not the totals."
        ),
    }


def approvals_only_view(policy: Policy, borrowers: list[Record]) -> dict[str, object]:
    """What the book would look like if, as in life, declines had no known outcome.

    A real lender's data is censored: a declined applicant never borrows, so never repays
    or defaults, so cannot be in a bad rate. Everything this project reports uses the full
    population, which is a luxury of a generated cohort - and the gap between the two is
    the size of the bias that reject inference exists to argue about.
    """
    full_bad = full_total = 0
    approved_bad = approved_total = 0
    declined_would_have_repaid = 0
    for borrower in borrowers:
        full_total += 1
        full_bad += int(borrower.would_default)
        decision = evaluate(policy, borrower.record())
        if decision.outcome == "approve":
            approved_total += 1
            approved_bad += int(borrower.would_default)
        elif not borrower.would_default:
            declined_would_have_repaid += 1
    return {
        "population_bad_rate": round(full_bad / full_total, 4) if full_total else None,
        "approved_bad_rate": round(approved_bad / approved_total, 4) if approved_total else None,
        "approval_rate": round(approved_total / full_total, 4) if full_total else None,
        "declined_who_would_have_repaid": declined_would_have_repaid,
        "share_of_the_population_with_no_observable_outcome": (
            round((full_total - approved_total) / full_total, 4) if full_total else None
        ),
        "reading": (
            "A scorecard fitted on the approved rows is fitted on this sample, not on the "
            "population - and the approved bad rate is lower than the population's by "
            "construction, because the policy declined the applicants it thought would "
            "default. Reject inference is the usual answer and it is an argument, not a "
            "measurement, so this project reports the gap and stops there."
        ),
    }
