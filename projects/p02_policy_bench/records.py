"""What the backtest, the sweep and the fairness analysis actually need from a record.

The project started with one synthetic cohort, so those modules were annotated with the
concrete ``Borrower`` class. They never used anything specific to it: they read the
label, the income used for banding, an id for reporting, and the dictionary of fields a
policy may see - plus, for segment tables, whatever attribute the caller names.

Writing that down as a protocol is what lets the same backtest run over a real loan book
of 255,347 applications and a real card book of 438,510 with no change to the engine and
no second copy of the analysis. Anything with these four members can be backtested.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Record(Protocol):
    """One application, from any source."""

    #: Something to name this row by in a findings table.
    id: str
    #: Monthly income in the cohort's own unit. Used for income banding only.
    monthly_income: int
    #: The outcome label: 1 if this applicant went on to default, 0 if not.
    #:
    #: A cohort with no outcomes sets this to 0 for every row and is passed with
    #: ``labelled=False``, which makes the backtest withhold every outcome-dependent
    #: figure rather than computing it against zeros.
    would_default: int

    def record(self) -> dict[str, object]:
        """The fields a policy may read. The label is never in here."""
