"""A real loan book: 255,347 applications with the outcomes that actually happened.

Everything else in this project is generated, and a generated cohort can only tell you
about its own generator. This file is the answer to that: the same policy engine, the
same backtest and the same cutoff sweep, run over applications somebody made and
defaults that were recorded.

**Source.** Kaggle, ``nikhil1e9/loan-default`` ("Loan Default Prediction Dataset"),
255,347 rows, 29,653 of them defaults - a base rate of 11.61%. Fetch it with
``make data``, which calls ``scripts/fetch_raw.py``. Nothing in this repo downloads
anything on its own.

**The field map is the honest part.** Source columns are renamed, one is derived, and
nothing is invented:

- ``age`` from ``Age``; ``annual_income`` from ``Income`` as given
- ``monthly_income`` is **derived**: ``Income // 12``. The source is annual, the
  policies and the income bands are monthly, so the division is stated here instead of
  being hidden inside a policy
- ``loan_amount``, ``credit_score`` (300-849), ``months_employed`` (0-119),
  ``credit_lines`` (1-4), ``interest_rate`` (percent), ``loan_term_months``,
  ``dti_ratio`` (0.10-0.90) straight across
- ``education``, ``employment_type``, ``marital_status``, ``loan_purpose`` as text
- ``has_mortgage``, ``has_dependants``, ``has_cosigner`` from Yes/No to 1/0
- ``would_default`` from ``Default``. **The label.** Never in :meth:`Application.record`

There is deliberately no mapping onto the synthetic cohort's field names. A real book
has no ``wallet_months`` and no ``txn_velocity``, and renaming ``MonthsEmployed`` into
``wallet_months`` so the existing policies would run is making data up. The policies in
``policies/real_*.policy`` are written against the columns above, which is why they are
separate files.

**What this source is and is not.** Its marginals are clean enough to be a teaching set
rather than a scrape - every categorical is near-uniform - but the default rate moves
monotonically with each feature a lender would expect: 22.1% for applicants in their
teens against 5.1% past sixty, 17.2% below 50,000 of income against 9.1% above 100,000,
6.0% at the cheapest interest against 17.8% at the dearest. That is enough signal for a
cutoff sweep to mean something, and saying where it came from is what stops these
numbers being read as a market.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from shared import datasets

#: Loss given default for the portfolio arithmetic. The synthetic cohort uses the same
#: figure so the two are comparable; it is an assumption either way.
LOSS_GIVEN_DEFAULT = 0.75

SOURCE = "kaggle:nikhil1e9/loan-default"
FETCH = "make data"
KEY = "real/loan_book"

COLUMNS = (
    "LoanID",
    "Age",
    "Income",
    "LoanAmount",
    "CreditScore",
    "MonthsEmployed",
    "NumCreditLines",
    "InterestRate",
    "LoanTerm",
    "DTIRatio",
    "Education",
    "EmploymentType",
    "MaritalStatus",
    "HasMortgage",
    "HasDependents",
    "LoanPurpose",
    "HasCoSigner",
    "Default",
)


class LoanBookError(RuntimeError):
    pass


@dataclass(frozen=True)
class Application:
    """One real application. Satisfies :class:`records.Record`."""

    id: str
    age: int
    annual_income: int
    monthly_income: int
    loan_amount: int
    credit_score: int
    months_employed: int
    credit_lines: int
    interest_rate: float
    loan_term_months: int
    dti_ratio: float
    education: str
    employment_type: str
    marital_status: str
    has_mortgage: int
    has_dependants: int
    loan_purpose: str
    has_cosigner: int
    # The outcome. Known to the backtest, never to a policy.
    would_default: int

    def record(self) -> dict[str, object]:
        out = asdict(self)
        out.pop("would_default")
        out.pop("id")
        return out


def path():
    return datasets.raw("nikhil1e9_loan-default", "Loan_default.csv")


def _yes(value: str) -> int:
    return 1 if value.strip().lower() in {"yes", "y", "1", "true"} else 0


def _row(row: dict[str, str]) -> Application:
    income = int(float(row["Income"]))
    return Application(
        id=row["LoanID"],
        age=int(float(row["Age"])),
        annual_income=income,
        monthly_income=income // 12,
        loan_amount=int(float(row["LoanAmount"])),
        credit_score=int(float(row["CreditScore"])),
        months_employed=int(float(row["MonthsEmployed"])),
        credit_lines=int(float(row["NumCreditLines"])),
        interest_rate=float(row["InterestRate"]),
        loan_term_months=int(float(row["LoanTerm"])),
        dti_ratio=float(row["DTIRatio"]),
        education=row["Education"].strip(),
        employment_type=row["EmploymentType"].strip(),
        marital_status=row["MaritalStatus"].strip(),
        has_mortgage=_yes(row["HasMortgage"]),
        has_dependants=_yes(row["HasDependents"]),
        loan_purpose=row["LoanPurpose"].strip(),
        has_cosigner=_yes(row["HasCoSigner"]),
        would_default=int(float(row["Default"])),
    )


def load(limit: int | None = None) -> list[Application]:
    """Read the book. ``limit`` is for tests and for sampled views only."""
    source = datasets.require(path(), FETCH)
    book: list[Application] = []
    for index, row in enumerate(datasets.read_csv(source, limit=limit)):
        missing = [column for column in COLUMNS if column not in row]
        if missing:
            raise LoanBookError(
                f"{source.name} row {index + 1} has no column(s) {', '.join(missing)}. "
                f"This project expects: {', '.join(COLUMNS)}"
            )
        book.append(_row(row))
    if not book:
        raise LoanBookError(f"{source} has a header but no rows")
    return book


def summary(book: list[Application]) -> dict[str, object]:
    incomes = sorted(item.monthly_income for item in book)
    defaults = sum(item.would_default for item in book)

    def percentile(fraction: float) -> int:
        return incomes[min(len(incomes) - 1, int(len(incomes) * fraction))]

    return {
        "source": SOURCE,
        "rows": len(book),
        "defaults": defaults,
        "base_default_rate": round(defaults / len(book), 4),
        "monthly_income_p10": percentile(0.10),
        "median_monthly_income": percentile(0.50),
        "monthly_income_p90": percentile(0.90),
        "loss_given_default": LOSS_GIVEN_DEFAULT,
        "labelled": True,
        "unit": "the source does not name a currency, so no symbol is printed anywhere",
    }
