"""Measure approval-rate disparity, and then ask whether it survives conditioning.

A parity table on its own is close to useless for a credit policy, in both directions.
It will report a gap that is entirely explained by a variable the policy is entitled to
use, and it will report parity while the policy excludes a segment through a proxy. So
this module always reports two things together:

1. the **raw gap** between the best- and worst-treated segment;
2. the gap **within income bands** - the same comparison with income held roughly still.

If the gap collapses inside the bands, the disparity is income acting through the policy,
which is a product and pricing question. If it survives, the policy is doing something to
that segment which income does not account for, and that is a defect.

Neither output is a compliance verdict. The module does not have one to give: it cannot
see whether a variable is a legitimate business necessity, and it says so rather than
printing a pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backtest import Outcome


@dataclass
class SegmentRow:
    key: str
    applications: int
    approvals: int
    approval_rate: float
    median_income: int

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "applications": self.applications,
            "approvals": self.approvals,
            "approval_rate": round(self.approval_rate, 4),
            "median_income": self.median_income,
        }


def _rows(outcomes: list[Outcome], attribute: str) -> list[SegmentRow]:
    groups: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        groups.setdefault(str(getattr(outcome.borrower, attribute)), []).append(outcome)
    rows: list[SegmentRow] = []
    for key, group in sorted(groups.items()):
        approvals = sum(1 for o in group if o.decision.approved)
        incomes = sorted(o.borrower.monthly_income for o in group)
        rows.append(
            SegmentRow(
                key=key,
                applications=len(group),
                approvals=approvals,
                approval_rate=approvals / len(group),
                median_income=incomes[len(incomes) // 2],
            )
        )
    return rows


def analyse(
    outcomes: list[Outcome], attribute: str = "region", bands: int = 4, min_cell: int = 40
) -> dict[str, object]:
    """Raw disparity on ``attribute``, then the same disparity inside income bands."""
    rows = _rows(outcomes, attribute)
    usable = [row for row in rows if row.applications >= min_cell]
    if len(usable) < 2:
        return {
            "attribute": attribute,
            "withheld": (
                f"fewer than two {attribute} groups have at least {min_cell} applications, "
                f"so a disparity figure would be describing a handful of people"
            ),
            "segments": [row.as_dict() for row in rows],
        }

    best = max(usable, key=lambda row: row.approval_rate)
    worst = min(usable, key=lambda row: row.approval_rate)
    raw_gap = best.approval_rate - worst.approval_rate
    ratio = (worst.approval_rate / best.approval_rate) if best.approval_rate else None

    ordered = sorted(outcomes, key=lambda o: o.borrower.monthly_income)
    size = max(1, len(ordered) // bands)
    band_rows: list[dict[str, object]] = []
    within_gaps: list[float] = []
    thin_cells = 0
    for index in range(bands):
        start = index * size
        end = len(ordered) if index == bands - 1 else (index + 1) * size
        chunk = ordered[start:end]
        if not chunk:
            continue
        inner = [row for row in _rows(chunk, attribute) if row.applications >= min_cell]
        thin_cells += len(_rows(chunk, attribute)) - len(inner)
        entry: dict[str, object] = {
            "band": index + 1,
            "income_from": chunk[0].borrower.monthly_income,
            "income_to": chunk[-1].borrower.monthly_income,
            "applications": len(chunk),
            "segments": [row.as_dict() for row in inner],
        }
        if len(inner) >= 2:
            high = max(inner, key=lambda row: row.approval_rate)
            low = min(inner, key=lambda row: row.approval_rate)
            gap = high.approval_rate - low.approval_rate
            within_gaps.append(gap)
            entry["gap"] = round(gap, 4)
            entry["best"] = high.key
            entry["worst"] = low.key
        else:
            entry["gap"] = None
            entry["withheld"] = (
                f"fewer than two {attribute} groups in this band reach {min_cell} applications"
            )
        band_rows.append(entry)

    measured = [gap for gap in within_gaps]
    within_mean = sum(measured) / len(measured) if measured else None
    if within_mean is None:
        reading = (
            "No band had two groups large enough to compare, so nothing can be said about "
            "whether income accounts for the raw gap."
        )
    elif raw_gap <= 0.02:
        reading = "The raw gap is under two points; there is little disparity to explain."
    elif within_mean <= raw_gap * 0.5:
        reading = (
            f"The gap falls from {raw_gap:.1%} to {within_mean:.1%} once income is held "
            f"roughly still, so most of it is income reaching the decision through the "
            f"policy's income rules rather than {attribute} itself. That is a product "
            f"question - whether this product should serve lower incomes at all - not a "
            f"defect in the rules."
        )
    else:
        reading = (
            f"The gap is {raw_gap:.1%} raw and still {within_mean:.1%} inside income bands, "
            f"so income does not account for it. Something in the policy is tracking "
            f"{attribute} through another field. Check which rules fire differently across "
            f"these groups at equal income."
        )

    return {
        "attribute": attribute,
        "segments": [row.as_dict() for row in rows],
        "best": best.as_dict(),
        "worst": worst.as_dict(),
        "raw_gap": round(raw_gap, 4),
        "ratio": round(ratio, 4) if ratio is not None else None,
        "within_band_gap_mean": round(within_mean, 4) if within_mean is not None else None,
        "bands": band_rows,
        "thin_cells_skipped": thin_cells,
        "reading": reading,
        "not_a_verdict": (
            "This is a measurement, not a compliance opinion. Whether a variable is a "
            "legitimate business necessity is a legal question about the product, and "
            "nothing here can answer it."
        ),
    }
