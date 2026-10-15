"""Detect the pressure patterns that precede a fraudulent transfer.

Seven families, each a lexicon of the things a scammer says in Roman Urdu, Urdu and
English. The score is **the number of distinct families present**, not the number of
matched phrases, because the signal is the combination: "abhi bhejo" (send now) is an
impatient relative, while "abhi bhejo" together with "main bank se baat kar raha hoon"
and "OTP batao" is one script.

The policy that follows from the score is deliberately crude and stated in the open:

* 0 families - proceed normally.
* 1 family - proceed, but say what was noticed in the confirmation.
* 2 or more - **do not execute.** Hand the user a warning and a human to call.

What this cannot do, said plainly: it reads the words in front of it. A caller coaching
a victim in the room is invisible to it, and a scammer who avoids these phrases scores
zero. It raises the floor; it is not a shield, and the project's own test set measures
exactly how often it misses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import numerals

OTP = "otp_solicitation"
IMPERSONATION = "staff_impersonation"
URGENCY = "urgency"
THREAT = "threat"
REMOTE = "remote_access"
LURE = "lure"
REDIRECT = "account_redirect"

FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    OTP: (
        "Asking for a one-time code, PIN or password",
        (
            "otp",
            "o.t.p",
            "one time password",
            "one-time code",
            "code batao",
            "code bhejo",
            "code share",
            "pin batao",
            "pin bata",
            "pin share",
            "password batao",
            "cvv",
            "verification code",
            "کوڈ بتاؤ",
            "او ٹی پی",
        ),
    ),
    IMPERSONATION: (
        "Claiming to be the bank, the regulator or an agent",
        (
            "bank se baat",
            "bank se bol",
            "bank ka numaindah",
            "bank officer",
            "helpline se",
            "customer care se",
            "state bank",
            "sbp se",
            "fbr se",
            "main manager",
            "agent bol",
            "franchise se",
            "head office se",
            "بینک سے بات",
            "اسٹیٹ بینک",
        ),
    ),
    URGENCY: (
        "Manufacturing time pressure",
        (
            "abhi bhejo",
            "abhi karo",
            "foran",
            "fauran",
            "jaldi karo",
            "jaldi bhejo",
            "do minute mein",
            "2 minute mein",
            "ek ghante mein",
            "aaj hi",
            "right now",
            "immediately",
            "urgent",
            "last warning",
            "ابھی بھیجو",
            "فوراً",
        ),
    ),
    THREAT: (
        "Threatening a consequence",
        (
            "account band ho",
            "account block ho",
            "account bند",
            "account suspend",
            "fir ho",
            "f.i.r",
            "police",
            "qanooni",
            "legal action",
            "court",
            "jurm",
            "giraftar",
            "penalty lag",
            "fine lag",
            "اکاؤنٹ بند",
            "قانونی",
        ),
    ),
    REMOTE: (
        "Asking for remote access or an app install",
        (
            "anydesk",
            "any desk",
            "teamviewer",
            "team viewer",
            "screen share",
            "screen dikha",
            "app install",
            "apk install",
            "link par click",
            "link kholo",
            "remote access",
            "quick support",
            "اسکرین",
        ),
    ),
    LURE: (
        "Offering a prize, refund or reward",
        (
            "inaam",
            "inam jeet",
            "prize",
            "lottery",
            "lucky draw",
            "scheme",
            "cashback jeet",
            "refund mil",
            "refund aya",
            "bonus mil",
            "free mein",
            "government scheme",
            "ehsaas program",
            "انعام",
            "لاٹری",
        ),
    ),
    REDIRECT: (
        "Redirecting money to an account the user does not name",
        (
            "mere account mein",
            "mere khate mein",
            "is account mein bhejo",
            "naye account",
            "new account number",
            "iban bhejta hoon",
            "jo number main dun",
            "اس اکاؤنٹ میں",
        ),
    ),
}

#: Some patterns are a pair of words with something in between: "abhi 50 hazaar mere
#: account mein bhejo" is the urgency pattern with an amount sitting in the middle, and a
#: list of adjacent phrases cannot see it. These carry their own bounded-gap regexes. The
#: gap is capped so the two halves have to belong to the same clause, rather than a bare
#: "abhi" anywhere in the message matching a "bhejo" at the end of a long one.
PROXIMITY: dict[str, tuple[str, ...]] = {
    URGENCY: (
        r"\babhi\b.{0,40}?\b(bhej|bhejo|bhejna|karo|kardo|transfer|send)\b",
        r"\b(jaldi|fauran|foran)\b.{0,40}?\b(bhej|bhejo|karo|transfer|send)\b",
        r"\baaj hi\b.{0,40}?\b(bhej|bhejo|karo|jama)\b",
    ),
    THREAT: (r"\bwarna\b.{0,60}?\b(band|block|suspend|fir|police|qanooni|legal)\b",),
}

ALLOW = "allow"
WARN = "warn"
BLOCK = "block"


@dataclass
class Hit:
    family: str
    description: str
    phrase: str
    start: int
    end: int

    def as_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "description": self.description,
            "phrase": self.phrase,
            "start": self.start,
            "end": self.end,
        }


@dataclass
class Assessment:
    verdict: str
    families: list[str]
    hits: list[Hit]
    reason: str

    @property
    def blocked(self) -> bool:
        return self.verdict == BLOCK

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "families": self.families,
            "score": len(self.families),
            "hits": [hit.as_dict() for hit in self.hits],
            "reason": self.reason,
        }


def assess(text: str) -> Assessment:
    """Score ``text`` and return the verdict with every phrase that produced it."""
    folded = numerals.normalise(text).lower()
    hits: list[Hit] = []
    for family, (description, phrases) in FAMILIES.items():
        for phrase in phrases:
            match = re.search(re.escape(phrase), folded)
            if match is not None:
                # One hit per phrase is enough; the score counts families, not phrases.
                hits.append(Hit(family, description, phrase, match.start(), match.end()))
        for pattern in PROXIMITY.get(family, ()):
            match = re.search(pattern, folded)
            if match is not None:
                hits.append(Hit(family, description, match.group(0), match.start(), match.end()))
    families = sorted({hit.family for hit in hits})

    if len(families) >= 2:
        verdict = BLOCK
        reason = (
            f"{len(families)} separate pressure patterns appear together "
            f"({', '.join(families)}). That combination is a script, not a coincidence, "
            f"so nothing is sent from this message."
        )
    elif len(families) == 1:
        verdict = WARN
        description = FAMILIES[families[0]][0]
        reason = (
            f"One pressure pattern appears: {description.lower()}. On its own this is "
            f"often innocent, so it is shown rather than acted on."
        )
    else:
        verdict = ALLOW
        reason = "No pressure pattern matched. That is not proof of safety, only an absence."
    return Assessment(verdict, families, hits, reason)


ADVICE = {
    "en": (
        "No bank, and no part of this app, will ever ask you for an OTP, a PIN or a "
        "password. Hang up and call the number printed on your card. Nothing has been sent."
    ),
    "ur": (
        "Koi bank, aur is app ka koi hissa, aap se OTP, PIN ya password kabhi nahi "
        "mangega. Call band karein aur apne card par likhe number par rabta karein. "
        "Kuch bhi nahi bheja gaya."
    ),
}


def advice(language: str = "en") -> str:
    return ADVICE.get(language, ADVICE["en"])
