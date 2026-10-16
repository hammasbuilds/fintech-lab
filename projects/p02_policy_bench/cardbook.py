"""A second real cohort: 438,557 card applications, most of them with no outcome.

This is the cohort shape a credit team actually has to work with. The applications are
real and there are a lot of them; the repayment history covers a fraction of them; and
the fraction with history is not a random sample of the rest.

**Source.** Kaggle, ``rikdifos/credit-card-approval-prediction``: ``application_record.csv``
(438,557 applications) and ``credit_record.csv`` (1,048,575 monthly status rows for
45,985 accounts). Fetch both with ``make data``.

**The join, and why the labelled part is a slice and not the dataset.** Joining the two
files on ``ID`` leaves **36,457** applications with any repayment history at all - 8.3%
of the book. That is below this repo's own 50,000-row floor, so it is reported as a
labelled *slice* and never as the headline: the full 438,557 are scored with every
outcome-dependent figure withheld, which is exactly the path
``backtest.run(..., labelled=False)`` exists for.

**The label.** ``credit_record.STATUS`` is per account-month: ``X`` no loan that month,
``C`` paid off, ``0`` 1-29 days past due, then ``1``-``5`` for 30-59, 60-89, 90-119,
120-149 and 150+ days. Two definitions of "bad" are computed because they disagree by
nearly seven times, and picking one quietly would be the whole finding:

* **ever 30+ days past due** - 4,291 accounts, 11.8% of the slice
* **ever 60+ days past due** - 616 accounts, 1.7% of the slice

**Two real-data traps that are handled here rather than averaged over.**

``DAYS_EMPLOYED`` is negative days before the application for people in work, and the
sentinel ``365243`` for pensioners - a positive number where every real value is
negative. Divided by -30 it becomes *minus twelve thousand months* of employment, which
a scorecard reads as the worst possible applicant. Pensioners are marked instead, with
``months_employed`` set to 0 and ``retired`` set to 1, so a policy can say something
about them on purpose.

``OCCUPATION_TYPE`` is blank for about a third of the book. Blank is kept as the literal
string ``"not stated"`` rather than guessed at, so a policy that reads occupation has to
decide what to do about not knowing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from shared import datasets

SOURCE = "kaggle:rikdifos/credit-card-approval-prediction"
FETCH = "make data"
KEY_APPLICATIONS = "real/card_applications"
KEY_RECORDS = "real/card_credit_records"

#: The sentinel ``DAYS_EMPLOYED`` carries for a pensioner. Every genuine value is
#: negative (days before the application), so this one positive number is a flag.
PENSIONER_SENTINEL = 365243

#: STATUS to days-past-due rank. X and C are not delinquency.
STATUS_RANK = {"X": -2, "C": -1, "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}

#: The two thresholds, because they disagree by nearly seven times on this book.
BAD_DEFINITIONS = {"ever_30_plus_dpd": 1, "ever_60_plus_dpd": 2}


class CardBookError(RuntimeError):
    pass


@dataclass(frozen=True)
class CardApplication:
    """One real card application. Satisfies :class:`records.Record`."""

    id: str
    age: int
    monthly_income: int
    annual_income: int
    months_employed: int
    retired: int
    dependants: int
    family_members: int
    income_type: str
    education: str
    family_status: str
    housing: str
    occupation: str
    owns_car: int
    owns_realty: int
    has_work_phone: int
    has_phone: int
    has_email: int
    #: 1 when this application joined to a repayment history that went bad under the
    #: definition in use, 0 when it joined and stayed good, and 0 when it did not join
    #: at all - which is why an unjoined cohort must be passed with ``labelled=False``.
    would_default: int
    #: Whether this row has any repayment history behind it.
    labelled: int

    def record(self) -> dict[str, object]:
        out = asdict(self)
        out.pop("would_default")
        out.pop("labelled")
        out.pop("id")
        return out


def applications_path():
    return datasets.raw("rikdifos_credit-card-approval-prediction", "application_record.csv")


def records_path():
    return datasets.raw("rikdifos_credit-card-approval-prediction", "credit_record.csv")


def worst_status(limit: int | None = None) -> dict[str, int]:
    """The worst delinquency rank each account ever reached, over 1,048,575 rows."""
    source = datasets.require(records_path(), FETCH)
    worst: dict[str, int] = {}
    for row in datasets.read_csv(source, limit=limit):
        status = row["STATUS"].strip()
        if status not in STATUS_RANK:
            raise CardBookError(
                f"{source.name} has STATUS {status!r}, which is not one of "
                f"{', '.join(sorted(STATUS_RANK))}"
            )
        rank = STATUS_RANK[status]
        account = row["ID"]
        if rank > worst.get(account, -3):
            worst[account] = rank
    return worst


def _yes(value: str) -> int:
    return 1 if value.strip().upper() in {"Y", "YES", "1", "TRUE"} else 0


def load(
    *, bad_definition: str = "ever_30_plus_dpd", limit: int | None = None
) -> tuple[list[CardApplication], dict[str, object]]:
    """Load the book, joined to its repayment history. Returns the rows and a summary."""
    if bad_definition not in BAD_DEFINITIONS:
        raise CardBookError(
            f"{bad_definition!r} is not a definition of bad here; use one of "
            f"{', '.join(sorted(BAD_DEFINITIONS))}"
        )
    cutoff = BAD_DEFINITIONS[bad_definition]
    worst = worst_status()
    source = datasets.require(applications_path(), FETCH)

    book: list[CardApplication] = []
    pensioners = unstated_occupation = 0
    for row in datasets.read_csv(source, limit=limit):
        days_employed = int(float(row["DAYS_EMPLOYED"]))
        retired = 1 if days_employed == PENSIONER_SENTINEL else 0
        pensioners += retired
        occupation = (row.get("OCCUPATION_TYPE") or "").strip() or "not stated"
        unstated_occupation += occupation == "not stated"
        annual = int(float(row["AMT_INCOME_TOTAL"]))
        rank = worst.get(row["ID"])
        book.append(
            CardApplication(
                id=row["ID"],
                age=abs(int(float(row["DAYS_BIRTH"]))) // 365,
                monthly_income=annual // 12,
                annual_income=annual,
                months_employed=0 if retired else abs(days_employed) // 30,
                retired=retired,
                dependants=int(float(row["CNT_CHILDREN"])),
                family_members=int(float(row["CNT_FAM_MEMBERS"])),
                income_type=row["NAME_INCOME_TYPE"].strip(),
                education=row["NAME_EDUCATION_TYPE"].strip(),
                family_status=row["NAME_FAMILY_STATUS"].strip(),
                housing=row["NAME_HOUSING_TYPE"].strip(),
                occupation=occupation,
                owns_car=_yes(row["FLAG_OWN_CAR"]),
                owns_realty=_yes(row["FLAG_OWN_REALTY"]),
                has_work_phone=int(float(row["FLAG_WORK_PHONE"])),
                has_phone=int(float(row["FLAG_PHONE"])),
                has_email=int(float(row["FLAG_EMAIL"])),
                would_default=1 if (rank is not None and rank >= cutoff) else 0,
                labelled=1 if rank is not None else 0,
            )
        )
    if not book:
        raise CardBookError(f"{source} has a header but no rows")

    labelled = [item for item in book if item.labelled]
    bads = sum(item.would_default for item in labelled)
    summary = {
        "source": SOURCE,
        "applications": len(book),
        "credit_record_accounts": len(worst),
        "joined_with_history": len(labelled),
        "join_rate": round(len(labelled) / len(book), 4),
        "bad_definition": bad_definition,
        "bads_in_the_slice": bads,
        "bad_rate_of_the_slice": round(bads / len(labelled), 4) if labelled else None,
        "pensioners_flagged": pensioners,
        "occupation_not_stated": unstated_occupation,
        "why_the_slice_is_not_the_headline": (
            f"{len(labelled):,} of {len(book):,} applications have any repayment history, "
            f"which is {len(labelled) / len(book):.1%} and under this repo's 50,000-row "
            f"floor. The full book is scored with every outcome figure withheld; the slice "
            f"is reported separately and labelled as a slice."
        ),
    }
    return book, summary


def labelled_slice(book: list[CardApplication]) -> list[CardApplication]:
    return [item for item in book if item.labelled]
