"""Turn a decision into the sentences an applicant can be given, in English and Roman Urdu.

The translation is template-based, not free text. Each phrase is generated from the
*structure* of the rule that fired - a field, a comparator and a number - so the Urdu
sentence cannot say anything the rule does not. That is a deliberate limitation: a
decline notice is a regulated communication, and a layer that can paraphrase is a layer
that can be wrong about a live credit decision.

Fields with no phrase entry fall back to naming the field and the threshold rather than
inventing a sentence. The fallback is visible on purpose - a missing translation should
look missing, not fluent.
"""

from __future__ import annotations

from .engine import Decision

#: field -> (English noun phrase, Roman Urdu noun phrase, unit)
PHRASES: dict[str, tuple[str, str, str]] = {
    "age": ("your age", "aap ki umar", "years"),
    "other_app_loans": (
        "how many loans you have from other apps",
        "doosri apps se aap ke kitne qarze chal rahe hain",
        "loans",
    ),
    "other_app_outstanding": (
        "what you owe other lending apps",
        "doosri apps ka aap par kitna qarz hai",
        "rupees",
    ),
    "tenor_days": (
        "how many days you have to repay",
        "wapsi ke liye kitne din hain",
        "days",
    ),
    "origination_month": (
        "which month you applied in",
        "aap ne kis maheene darkhwast di",
        "months into the window",
    ),
    "monthly_income": ("your monthly income", "aap ki maheena aamdani", "rupees"),
    "wallet_months": (
        "how long your wallet has been open",
        "aap ka wallet kitne maheene se chal raha hai",
        "months",
    ),
    "txn_velocity": (
        "the number of transactions through your wallet",
        "aap ke wallet mein mahine ke transactions",
        "transactions",
    ),
    "prior_defaults": ("loans you did not repay before", "pichle qarze jo wapas nahi huye", ""),
    "on_time_repayments": ("loans you repaid on time", "waqt par wapas kiye gaye qarze", ""),
    "dependants": (
        "how many people depend on your income",
        "aap ki aamdani par kitne log hain",
        "people",
    ),
    "avg_balance": ("the balance you usually keep", "aap ke wallet ka aam balance", "rupees"),
    "employment": ("how you earn", "aap ki aamdani ka zariya", ""),
    "region": ("where you live", "aap kahan rehte hain", ""),
    "has_smartphone": (
        "whether you use a smartphone",
        "aap smartphone istemal karte hain ya nahi",
        "",
    ),
    "score": ("your overall score", "aap ka total score", "points"),
}

_COMPARATORS = {
    "<": ("is below", "se kam hai"),
    "<=": ("is at or below", "se kam ya barabar hai"),
    ">": ("is above", "se zyada hai"),
    ">=": ("is at or above", "se zyada ya barabar hai"),
    "==": ("is", "hai"),
    "!=": ("is not", "nahi hai"),
}

RIGHTS_EN = (
    "You can apply again when the reason above has changed. "
    "If you believe this decision used the wrong information about you, ask for it to be "
    "reviewed by a person and we will tell you what we hold."
)
RIGHTS_UR = (
    "Jab ooper di gayi wajah badal jaye to aap dobara darkhwast de sakte hain. "
    "Agar aap ko lagta hai ke yeh faisla aap ki ghalat maloomat par hua hai, to insani "
    "jaiza talab karein - hum aap ko bata denge ke hamare paas kya darj hai."
)


def phrase(field_name: str, language: str) -> str:
    entry = PHRASES.get(field_name)
    if entry is None:
        return f"[{field_name}]"
    return entry[0] if language == "en" else entry[1]


def notice(decision: Decision, currency: str = "PKR", language: str = "en") -> dict[str, object]:
    """The decline or approval notice, as structured text the UI renders verbatim."""
    english = language == "en"
    if decision.outcome == "approve":
        headline = (
            f"Approved. Your limit is {currency} {decision.limit:,.0f}."
            if english
            else f"Manzoor. Aap ki limit {currency} {decision.limit:,.0f} hai."
        )
        body = [
            (line.reason or line.condition) if english else _translate_line(line.condition)
            for line in decision.score_lines
            if line.matched and line.points > 0
        ]
        charges = [
            (
                f"{charge['kind'].replace('_', ' ')}: {currency} {float(charge['amount']):,.0f}"
                f"{' per ' + str(charge['period']) if charge['period'] else ''}"
            )
            for charge in decision.charges
        ]
        return {
            "outcome": "approve",
            "headline": headline,
            "because": body,
            "charges": charges,
            "rights": RIGHTS_EN if english else RIGHTS_UR,
        }

    if decision.gate is not None:
        reason = str(decision.gate["reason"])
        headline = "Not approved this time." if english else "Is waqt manzoori nahi hui."
        detail = reason if english else _translate_line(str(decision.gate["condition"]))
        return {
            "outcome": decision.outcome,
            "headline": headline,
            "because": [detail],
            "charges": [],
            "rights": RIGHTS_EN if english else RIGHTS_UR,
        }

    shortfall = decision.threshold - decision.score
    headline = (
        f"Not approved this time. Your score was {decision.score:g} and this product needs "
        f"{decision.threshold:g}."
        if english
        else f"Is waqt manzoori nahi hui. Aap ka score {decision.score:g} tha, is product ke "
        f"liye {decision.threshold:g} chahiye."
    )
    # These are requirements the applicant did NOT meet, and they must be labelled as
    # such. Listing them under the same heading as an approval's reasons produces a
    # notice that reads "not approved because your income is above the floor", which is
    # the opposite of what happened. A decline notice is a regulated communication; it
    # does not get to be ambiguous about which direction a reason points.
    unmet = [
        {
            "requirement": (line.reason or line.condition)
            if english
            else _translate_line(line.condition),
            "met": False,
            "worth": line.points,
        }
        for line in decision.score_lines
        if not line.matched and line.points > 0
    ]
    unmet.sort(key=lambda item: -float(item["worth"]))
    return {
        "outcome": decision.outcome,
        "headline": headline,
        "shortfall": shortfall,
        "because": [
            f"your score fell {shortfall:g} points short"
            if english
            else f"aap ka score {shortfall:g} points kam raha"
        ],
        "requirements_not_met": unmet[:4],
        "not_met_label": "not met" if english else "yeh shart poori nahi hui",
        "charges": [],
        "rights": RIGHTS_EN if english else RIGHTS_UR,
    }


def _translate_line(rendered: str) -> str:
    """Translate a rendered condition such as ``monthly_income < 18000``.

    Conditions joined by and/or are translated part by part and rejoined, so a compound
    rule does not fall back to the field-name placeholder as a whole.
    """
    for joiner, urdu in ((" and ", " aur "), (" or ", " ya ")):
        if joiner in rendered:
            return urdu.join(_translate_line(part) for part in rendered.split(joiner))
    parts = rendered.strip("()").split()
    if len(parts) != 3:
        return rendered
    field_name, op, value = parts
    comparator = _COMPARATORS.get(op)
    if comparator is None:
        return rendered
    subject = phrase(field_name, "ur")
    unit = PHRASES.get(field_name, ("", "", ""))[2]
    tail = f"{value.strip(chr(34))}" + (f" {_unit_ur(unit)}" if unit else "")
    return f"{subject} {tail} {comparator[1]}"


def _unit_ur(unit: str) -> str:
    return {
        "years": "saal",
        "rupees": "rupay",
        "months": "maheene",
        "transactions": "transactions",
        "points": "points",
        "people": "log",
    }.get(unit, unit)


def untranslated_fields() -> list[str]:
    """Fields a policy can use that have no phrase entry. The UI shows this, not hides it."""
    from .cohort import Borrower

    known = set(PHRASES)
    # Outcome fields are not policy-readable, so they need no phrase: a decline notice
    # that mentioned when the borrower defaulted would be quoting the future at them.
    outcomes = {"id", "would_default", "months_to_default"}
    available = {name for name in Borrower.__dataclass_fields__ if name not in outcomes}
    return sorted(available - known)
