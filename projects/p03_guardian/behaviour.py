"""Judge the transfer, not the words - the layer that attacks this project's own blind spot.

The coercion check in :mod:`coercion` reads text, and the test set measures exactly what
that costs: four real scam approaches that use none of its vocabulary, four misses. No
amount of extra phrases fixes that, because the next scammer writes a sentence nobody
listed. So this layer looks at something a scammer cannot phrase their way around: **what
the transfer actually is**, against what this account normally does.

The signals are all of the form "this is not what you usually do":

* the payee has never been paid from this account before;
* the amount is several times the usual transfer;
* it would take most of the balance, or is more than is in it;
* it would push the day's total past the account's own cap;
* several transfers have already gone out within the hour.

**Behaviour escalates; it does not block.** A first large transfer to a new payee is
exactly what paying a new landlord looks like, so treating it as fraud would make the
product unusable. Coercion - words that only appear in a script - blocks outright.
Behaviour asks for a second, slower confirmation that states what is unusual. Conflating
the two would mean either blocking honest people or letting a script through, and the
distinction is the whole design.

The cost of this layer is measured and published alongside the benefit: the test set
counts how many *benign* transfers it also escalates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

NONE = "none"
NOTE = "note"
ELEVATED = "elevated"

#: An amount this many times the usual transfer counts as unusual.
LARGE_MULTIPLE = 3.0
#: Taking at least this share of the balance counts as a sweep.
SWEEP_SHARE = 0.85
#: More than this many transfers inside the window is a burst.
BURST_COUNT = 3
BURST_WINDOW = timedelta(hours=1)

#: Signals that escalate on their own rather than needing a second one. The daily cap is
#: not a soft anomaly - it is a limit the account already has - so passing it always
#: earns the slower confirmation. A cap that a habitual "haan" clears is not a cap.
ALWAYS_ESCALATES = {"beyond_daily_cap", "beyond_monthly_cap", "exceeds_balance"}


@dataclass
class Transfer:
    beneficiary: str
    amount: float
    at: datetime


@dataclass
class Profile:
    """What this account normally does. Supplied by the bank, never inferred from one message."""

    balance: float = 18423.50
    daily_cap: float = 50000.0
    #: The wallet's monthly limit. Branchless banking accounts in Pakistan are tiered and
    #: the tiers are limits - reported figures for the 2016 revision give Level 0 a daily
    #: cap of Rs 25,000 and a monthly cap of Rs 40,000, and Level 1 Rs 50,000 and
    #: Rs 80,000. This layer had only the daily one, and a split spread over several days
    #: never goes near a daily cap: 527 of them in the generated set went entirely
    #: unnoticed, which is why people split that way.
    #:
    #: https://propakistani.pk/2016/07/14/sbp-revises-transactions-limits-for-branchless-banking-accounts/
    monthly_cap: float = 80000.0
    history: list[Transfer] = field(default_factory=list)
    now: datetime | None = None

    @property
    def moment(self) -> datetime:
        return self.now or datetime(2026, 10, 15, 14, 30)

    @property
    def known_beneficiaries(self) -> set[str]:
        return {transfer.beneficiary for transfer in self.history}

    @property
    def usual_transfer(self) -> float:
        """The median past transfer. A median, not a mean, so one large transfer in the
        history does not quietly raise the bar for every later one."""
        amounts = sorted(transfer.amount for transfer in self.history)
        if not amounts:
            return 0.0
        middle = len(amounts) // 2
        if len(amounts) % 2:
            return amounts[middle]
        return (amounts[middle - 1] + amounts[middle]) / 2

    def usual_for(self, beneficiary: str | None) -> tuple[float, str]:
        """The baseline this transfer should be judged against, and what it is.

        A payee with a history of their own sets their own baseline. Judging every
        transfer against one account-wide median marks the regular 12,000 to a parent as
        unusual purely because most *other* transfers are small - which is a false
        positive on the single most routine payment the account makes.
        """
        if beneficiary is not None:
            theirs = sorted(t.amount for t in self.history if t.beneficiary == beneficiary)
            if theirs:
                middle = len(theirs) // 2
                median = (
                    theirs[middle] if len(theirs) % 2 else (theirs[middle - 1] + theirs[middle]) / 2
                )
                return median, f"the usual transfer to {beneficiary}"
        return self.usual_transfer, "the usual transfer from this account"

    def sent_today(self) -> float:
        today = self.moment.date()
        return sum(t.amount for t in self.history if t.at.date() == today)

    def sent_this_month(self) -> float:
        """Everything sent in the calendar month this transfer falls in.

        A calendar month rather than a rolling thirty days, because that is how the
        wallet's own limit is written. A rolling window would be the better fraud
        control and the worse answer to "have I broken my limit".
        """
        moment = self.moment
        return sum(
            transfer.amount
            for transfer in self.history
            if (transfer.at.year, transfer.at.month) == (moment.year, moment.month)
        )

    def recent(self, window: timedelta = BURST_WINDOW) -> list[Transfer]:
        cutoff = self.moment - window
        return [t for t in self.history if t.at >= cutoff]

    def record(self, beneficiary: str, amount: float) -> None:
        self.history.append(Transfer(beneficiary, amount, self.moment))
        self.balance -= amount


@dataclass
class Signal:
    name: str
    detail: str


@dataclass
class Assessment:
    level: str
    signals: list[Signal]
    reason: str

    @property
    def escalated(self) -> bool:
        return self.level == ELEVATED

    def as_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "escalated": self.escalated,
            "signals": [{"name": s.name, "detail": s.detail} for s in self.signals],
            "count": len(self.signals),
            "reason": self.reason,
        }


def assess(profile: Profile, beneficiary: str | None, amount: float | None) -> Assessment:
    """Score one proposed transfer against the account's own history."""
    if amount is None or amount <= 0:
        return Assessment(NONE, [], "no amount to judge")

    signals: list[Signal] = []

    if beneficiary is not None and beneficiary not in profile.known_beneficiaries:
        signals.append(
            Signal(
                "first_time_beneficiary",
                f"{beneficiary} has not been paid from this account before",
            )
        )

    usual, basis = profile.usual_for(beneficiary)
    if usual > 0 and amount >= usual * LARGE_MULTIPLE:
        signals.append(
            Signal(
                "unusually_large",
                f"{amount:,.0f} is {amount / usual:.1f} times {basis}, {usual:,.0f}",
            )
        )

    if profile.balance > 0 and amount > profile.balance:
        # Not a behavioural matter at all: the money is not there. Calling this a "sweep"
        # produced the nonsense line "50,000 is 271% of the balance".
        signals.append(
            Signal(
                "exceeds_balance",
                f"{amount:,.0f} is more than the {profile.balance:,.0f} in the account, so "
                f"it cannot complete as asked",
            )
        )
    elif profile.balance > 0 and amount >= profile.balance * SWEEP_SHARE:
        signals.append(
            Signal(
                "balance_sweep",
                f"{amount:,.0f} is {amount / profile.balance:.0%} of the {profile.balance:,.0f} "
                f"in the account",
            )
        )

    already = profile.sent_today()
    this_month = profile.sent_this_month()
    if profile.monthly_cap and this_month + amount > profile.monthly_cap:
        signals.append(
            # Each transfer on its own is well inside the daily cap, which is exactly
            # what a split across several days looks like.
            Signal(
                "beyond_monthly_cap",
                f"{this_month + amount:,.0f} this month against a cap of "
                f"{profile.monthly_cap:,.0f}",
            )
        )

    if already + amount > profile.daily_cap:
        signals.append(
            Signal(
                "beyond_daily_cap",
                f"{already:,.0f} has already gone out today, so this would reach "
                f"{already + amount:,.0f} against a cap of {profile.daily_cap:,.0f}",
            )
        )

    burst = profile.recent()
    if len(burst) >= BURST_COUNT:
        signals.append(
            Signal(
                "rapid_repeat",
                f"{len(burst)} transfers have already gone out in the last hour",
            )
        )

    if not signals:
        return Assessment(
            NONE,
            [],
            "This looks like an ordinary transfer for this account. That is not proof it "
            "is wanted, only that nothing about it stands out.",
        )
    hard = [signal for signal in signals if signal.name in ALWAYS_ESCALATES]
    if len(signals) == 1 and not hard:
        return Assessment(
            NOTE,
            signals,
            f"One thing is unusual: {signals[0].detail}. On its own that is ordinary "
            f"enough to mention and not act on.",
        )
    if hard and len(signals) == 1:
        return Assessment(
            ELEVATED,
            signals,
            f"This transfer passes a limit the account already has: {hard[0].detail}. "
            f"That is not a refusal - the limit is yours to pass - but it is not "
            f"something to wave through either.",
        )
    return Assessment(
        ELEVATED,
        signals,
        f"{len(signals)} things about this transfer are unusual for this account at once "
        f"({', '.join(signal.name for signal in signals)}). That is not a reason to refuse "
        f"it - it may be exactly what you meant - but it is a reason to slow down and "
        f"check the details rather than confirm out of habit.",
    )


def demo_profile(now: datetime | None = None) -> Profile:
    """A plausible month of history for the sandbox account.

    Ordinary habits: a monthly transfer to a parent, a regular one to a shopkeeper, two
    small ones to a sibling. The median lands near 3,000, which is what makes a 50,000
    transfer to a stranger stand out without any special-casing.
    """
    base = now or datetime(2026, 10, 15, 14, 30)
    history = [
        Transfer("Nasreen Akhtar", 12000, base - timedelta(days=29)),
        Transfer("Bilal Traders", 3200, base - timedelta(days=24)),
        Transfer("Imran Shah", 1500, base - timedelta(days=21)),
        Transfer("Bilal Traders", 2800, base - timedelta(days=17)),
        Transfer("Imran Shah", 900, base - timedelta(days=12)),
        Transfer("Bilal Traders", 3400, base - timedelta(days=8)),
        Transfer("Nasreen Akhtar", 12000, base - timedelta(days=2)),
    ]
    return Profile(balance=18423.50, daily_cap=50000.0, history=history, now=base)


ADVICE = {
    "en": (
        "Nothing is wrong with a transfer being unusual. Read the name and the last four "
        "digits back to yourself before you confirm, and if someone is on the phone with "
        "you right now, hang up first and call them back on a number you already have."
    ),
    "ur": (
        "Transfer ka ghair-mamooli hona koi kharabi nahi. Tasdeeq se pehle naam aur "
        "account ke aakhri char hindse khud parh lein, aur agar is waqt koi phone par aap "
        "ke saath hai to pehle call band karein aur apne paas mojood number par wapas call "
        "karein."
    ),
}


def advice(language: str = "en") -> str:
    return ADVICE.get(language, ADVICE["en"])
