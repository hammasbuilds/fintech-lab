"""A synthetic borrower cohort with a documented data-generating process.

**This is a sandbox, not a portfolio.** The borrowers are generated, not observed, and
every number a backtest produces is a statement about this generator rather than about
any real market. Saying so is the whole reason the generator is written out in full here
instead of being shipped as an opaque CSV: a reader can see what was assumed, and
therefore which conclusions are circular.

What the generator deliberately contains, because they are the cases a policy sandbox
has to be able to show:

* **Income that is correlated with region.** The south-rural segment earns less and has
  shorter wallet tenure. No policy in ``policies/`` mentions region at all, so any
  approval-rate gap by region is produced entirely by income and tenure. That is the
  realistic version of the fairness problem and the one a parity table alone will
  misread.
* **Turnover that is not income.** Some borrowers move large sums through a wallet
  without earning them - a shopkeeper float, a family remittance hub. Their repayment
  behaviour tracks their *income*, not their turnover, so a policy that rewards
  turnover is fooled and a policy that penalises it gives up good borrowers.
* **Repayment driven by factors a policy can see and factors it cannot.** Half the
  signal is in the observable fields; the rest is an unobserved shock. A sandbox where
  the outcome is perfectly predictable from the inputs would make every policy look
  better than any policy can be.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass

REGIONS = ("central-urban", "north-urban", "south-rural", "east-rural")
EMPLOYMENT = ("salaried", "self-employed", "daily-wage")

#: Loss given default. A nano loan that goes bad is rarely a total loss - some is
#: recovered from the wallet balance and from later repayments - so the portfolio
#: arithmetic uses this rather than writing the full limit off.
LOSS_GIVEN_DEFAULT = 0.75


@dataclass(frozen=True)
class Borrower:
    id: str
    age: int
    monthly_income: int
    wallet_months: int
    txn_velocity: int
    prior_defaults: int
    on_time_repayments: int
    dependants: int
    avg_balance: int
    employment: str
    region: str
    has_smartphone: int
    # Ground truth, known to the generator and to nothing else in the project.
    would_default: int

    def record(self) -> dict[str, object]:
        """The fields a policy may read. ``would_default`` is deliberately withheld."""
        out = asdict(self)
        out.pop("would_default")
        out.pop("id")
        return out


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def generate(size: int = 50_000, seed: int = 20261015) -> list[Borrower]:
    """Build the cohort. Same seed, same cohort, on every machine."""
    rng = random.Random(seed)
    borrowers: list[Borrower] = []
    for index in range(size):
        region = rng.choices(REGIONS, weights=(38, 24, 24, 14))[0]
        rural = region.endswith("rural")

        employment = rng.choices(
            EMPLOYMENT,
            weights=(20, 34, 46) if rural else (44, 38, 18),
        )[0]

        base = {"salaried": 42000, "self-employed": 34000, "daily-wage": 19000}[employment]
        if rural:
            base *= 0.62
        monthly_income = max(6000, int(rng.lognormvariate(math.log(base), 0.42)))

        tenure_mean = 7.5 if rural else 14.0
        wallet_months = max(0, min(60, int(rng.gauss(tenure_mean, 7.0))))

        age = max(18, min(68, int(rng.gauss(33 if rural else 35, 9))))
        dependants = max(0, min(9, int(rng.gauss(4.4 if rural else 2.9, 1.9))))

        # Turnover: mostly a multiple of income, but one borrower in eleven runs a float
        # through the wallet that has nothing to do with what they earn.
        if rng.random() < 1 / 11:
            txn_velocity = int(rng.uniform(90, 260))
        else:
            txn_velocity = max(1, int(rng.gauss(monthly_income / 1400, 7)))

        avg_balance = max(
            0, int(rng.lognormvariate(math.log(max(400, monthly_income * 0.11)), 0.7))
        )
        prior_defaults = rng.choices((0, 1, 2, 3), weights=(84, 11, 4, 1))[0]
        on_time_repayments = rng.choices((0, 1, 2, 3, 4, 5), weights=(30, 20, 18, 14, 10, 8))[0]
        has_smartphone = 1 if rng.random() < (0.72 if rural else 0.93) else 0

        # Repayment propensity. Income, tenure and track record help; defaults and a
        # thin balance hurt. Turnover does NOT appear - that is the trap.
        linear = (
            -2.55
            + 1.35 * prior_defaults
            - 0.52 * math.log1p(monthly_income / 10000)
            - 0.040 * wallet_months
            - 0.21 * on_time_repayments
            + 0.10 * dependants
            - 0.00004 * avg_balance
            + rng.gauss(0, 0.85)  # the part no policy can see
        )
        would_default = 1 if rng.random() < _logistic(linear) else 0

        borrowers.append(
            Borrower(
                id=f"B{index + 1:05d}",
                age=age,
                monthly_income=monthly_income,
                wallet_months=wallet_months,
                txn_velocity=txn_velocity,
                prior_defaults=prior_defaults,
                on_time_repayments=on_time_repayments,
                dependants=dependants,
                avg_balance=avg_balance,
                employment=employment,
                region=region,
                has_smartphone=has_smartphone,
                would_default=would_default,
            )
        )
    return borrowers


def summary(borrowers: list[Borrower]) -> dict[str, object]:
    """Describe the cohort so a reader can judge the backtest against it."""
    count = len(borrowers)
    if not count:
        return {"size": 0}
    incomes = sorted(b.monthly_income for b in borrowers)
    by_region: dict[str, dict[str, float]] = {}
    for region in REGIONS:
        group = [b for b in borrowers if b.region == region]
        if not group:
            continue
        by_region[region] = {
            "count": len(group),
            "share": round(len(group) / count, 4),
            "median_income": sorted(b.monthly_income for b in group)[len(group) // 2],
            "median_wallet_months": sorted(b.wallet_months for b in group)[len(group) // 2],
            "base_default_rate": round(sum(b.would_default for b in group) / len(group), 4),
        }
    return {
        "size": count,
        "median_income": incomes[count // 2],
        "income_p10": incomes[count // 10],
        "income_p90": incomes[(9 * count) // 10],
        "base_default_rate": round(sum(b.would_default for b in borrowers) / count, 4),
        "loss_given_default": LOSS_GIVEN_DEFAULT,
        "by_region": by_region,
        "by_employment": {
            kind: round(
                sum(b.would_default for b in borrowers if b.employment == kind)
                / max(1, sum(1 for b in borrowers if b.employment == kind)),
                4,
            )
            for kind in EMPLOYMENT
        },
    }


# --------------------------------------------------------------- your own data


REQUIRED_COLUMNS = tuple(
    name for name in Borrower.__dataclass_fields__ if name not in {"id", "would_default"}
)


class CohortError(Exception):
    """A supplied cohort that cannot be used, with the row and column named."""


def from_csv(text: str, *, label_column: str = "would_default") -> tuple[list[Borrower], dict]:
    """Read a cohort from CSV text, so the sandbox can be pointed at a real portfolio.

    The label column is optional and the difference matters: **without an outcome column
    there is no bad rate, no expected loss and no tightening precision**, because every
    one of those figures is computed against a known outcome. A cohort with no label can
    still be used to see who a policy approves and what it would disburse, and this
    function says which of the two it returned rather than filling the label with zeros
    and reporting a 0% bad rate.
    """
    import csv
    import io

    stream = io.StringIO(text.lstrip("\ufeff"))
    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        raise CohortError("the CSV has no header row")
    header = [name.strip() for name in reader.fieldnames]
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        raise CohortError(
            f"these columns are required and missing: {', '.join(missing)}. "
            f"The header needs: {', '.join(REQUIRED_COLUMNS)}"
        )
    labelled = label_column in header

    borrowers: list[Borrower] = []
    for number, row in enumerate(reader, start=2):  # row 1 is the header
        clean = {key.strip(): (value or "").strip() for key, value in row.items() if key}
        values: dict[str, object] = {}
        for name in REQUIRED_COLUMNS:
            raw = clean.get(name, "")
            if raw == "":
                raise CohortError(f"row {number}: column {name!r} is empty")
            if name in {"employment", "region"}:
                values[name] = raw
                continue
            try:
                values[name] = int(float(raw))
            except ValueError:
                raise CohortError(
                    f"row {number}: column {name!r} is {raw!r}, which is not a number"
                ) from None
        label = 0
        if labelled:
            raw_label = clean.get(label_column, "")
            if raw_label not in {"0", "1"}:
                raise CohortError(
                    f"row {number}: {label_column!r} is {raw_label!r}; it must be 0 or 1"
                )
            label = int(raw_label)
        borrowers.append(
            Borrower(id=clean.get("id") or f"R{number - 1:05d}", would_default=label, **values)  # type: ignore[arg-type]
        )

    if not borrowers:
        raise CohortError("the CSV has a header but no rows")
    return borrowers, {
        "rows": len(borrowers),
        "labelled": labelled,
        "label_column": label_column if labelled else None,
        "unknown_columns": [
            name for name in header if name not in REQUIRED_COLUMNS + ("id", label_column)
        ],
        "note": (
            "Outcome column found, so bad rate, expected loss and tightening precision "
            "are all available."
            if labelled
            else "No outcome column, so this cohort can show who is approved and what "
            "would be disbursed, but every figure that needs a known outcome - bad rate, "
            "expected loss, the precision of a tightening - is withheld rather than "
            "computed against zeros."
        ),
    }


def to_csv(borrowers: list[Borrower], *, include_label: bool = True) -> str:
    """Write a cohort out, so the bundled one can be used as a template."""
    import csv
    import io

    columns = ["id", *REQUIRED_COLUMNS] + (["would_default"] if include_label else [])
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for borrower in borrowers:
        row = {name: getattr(borrower, name) for name in columns}
        writer.writerow(row)
    return out.getvalue()
