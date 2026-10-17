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

#: The security words a scammer obfuscates once blocked: 0tp, o.t.p, ot p, O-T-P. The
#: product matched them exactly, which is the one place in this project where an exact
#: match is the wrong call - these are not numbers whose spelling carries meaning, they
#: are three letters somebody is trying to smuggle past a filter.
OBFUSCATED = ("otp", "pin", "cvv", "code", "password")

OTP = "otp_solicitation"
IMPERSONATION = "staff_impersonation"
URGENCY = "urgency"
THREAT = "threat"
REMOTE = "remote_access"
LURE = "lure"
REDIRECT = "account_redirect"
#: The five families the published statistics put first and this product had no opinion
#: about. A purchase scam is the largest single category of authorised push payment fraud
#: in the UK regulator's breakdown - one summary puts it at 68% of cases - and it carries
#: none of the markers the seven families above look for: nobody asks for a code, nobody
#: impersonates the bank, nobody threatens. The victim is buying something that does not
#: exist and is entirely willing.
PURCHASE = "purchase_scam"
INVESTMENT = "investment_scam"
ROMANCE = "romance_scam"
ADVANCE_FEE = "advance_fee"
INVOICE = "invoice_redirect"

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
            # The attested spellings of "quickly" that were missing: 29% of generated
            # urgency scripts went uncaught because they used "fori", "jald" or "abhee"
            # rather than the three spellings listed above.
            #
            # They are listed *with a verb*, not on their own. Adding the bare words took
            # the catch rate from 0.706 to 0.969 and the false-block rate on ordinary
            # traffic from 0.021 to 0.123 - one message in eight, because "ammi ko 5000
            # bhejo, jaldi nahi koi baat nahi" says "no rush" with the word "jaldi" in
            # it. A hurry is only a signal when it is a hurry to *do* something.
            "fori bhej",
            "fori karo",
            "fori transfer",
            "jald bhej",
            "jald karo",
            "jaldi bhej",
            "abhee bhej",
            "abhee karo",
            "abhi bhej",
            "abhi transfer",
            "waqt nahi hai",
            "der ho jaye gi",
            "der ho jaye ga",
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
            "fir case",
            "case ho jaye",
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
    PURCHASE: (
        "Paying in advance for goods from someone you have not met",
        (
            "advance payment",
            "advance bhej",
            "pehle payment",
            "pehle paisay",
            "booking amount",
            "token amount",
            "parcel chhurwa",
            "delivery charges bhej",
            "courier charges",
            "olx pe",
            "facebook page se",
            "stock last",
            "last piece",
            "\u0627\u06cc\u062f\u0648\u0627\u0646\u0633 \u067e\u06cc\u0645\u0646\u0679",
        ),
    ),
    INVESTMENT: (
        "A return that is promised rather than earned",
        (
            "double karwa",
            "double ho jaye",
            "profit guaranteed",
            "guaranteed return",
            "monthly profit",
            "forex trading",
            "crypto invest",
            "bitcoin invest",
            "trading account khol",
            "50 percent profit",
            "paisa barha",
            "\u0645\u0646\u0627\u0641\u0639 \u06af\u0627\u0631\u0646\u0679\u06cc",
        ),
    ),
    ROMANCE: (
        "Money asked for by someone met online",
        (
            "shadi karna chahta",
            "shadi karni hai",
            "visa ke paisay",
            "ticket ke paisay",
            "hospital me hoon",
            "mera phone band ho raha",
            "tumse pyar",
            "mujhse shadi",
            "gift bheja hai customs",
            "customs clearance",
        ),
    ),
    ADVANCE_FEE: (
        "A fee demanded before money that does not exist arrives",
        (
            "processing fee bhej",
            "clearance fee",
            "registration fee bhej",
            "tax jama karwa",
            "loan approve ho gaya fee",
            "inheritance",
            "warasat",
            "ticket jeet",
            "lottery fee",
        ),
    ),
    INVOICE: (
        "A supplier's account details changed just before payment",
        (
            "invoice ka account badal",
            "naya invoice",
            "updated bank details",
            "account details update ho gay",
            "supplier ka naya account",
            "is bar is account me",
            "iban change ho gaya",
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
            # Added after scoring 6,950 generated redirect scripts, of which this family
            # named only 30% - and named *none* of them as a redirect. The three
            # commonest ways of saying it were all missing, and two of them were being
            # caught as a threat instead, because "purana account band hai" contains
            # "account band". A scam that is caught under the wrong name is caught by
            # luck.
            "account badal",
            "account change",
            "doosre account",
            "dusre account",
            "doosray account",
            "purana account band",
            "naye number pe",
            "naye number par",
            "new number pe",
            # The same three phrasings in Urdu script. The family had one Urdu entry and
            # the Roman additions above do not match a message written in the script -
            # which the generated Urdu-script scripts measured at 0.88 caught overall.
            "اکاؤنٹ بدل",
            "نئے نمبر پہ",
            "دوسرے اکاؤنٹ",
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


#: Characters a scammer pushes between the letters of a security word, and the digits
#: they swap in for letters. Folding these is the one place in this project where fuzzy
#: matching is right: ``0tp`` is not a word whose spelling carries meaning, it is three
#: letters being smuggled past a filter.
_OBFUSCATION_FILL = re.compile(r"[\s.\-_*|/]+")
_LEETSPEAK = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})


def deobfuscate(text: str) -> str:
    """A second reading of the text with the smuggling removed.

    Returned alongside the original rather than instead of it: collapsing separators
    would join words that were never one word - "pin" appears inside "pindi" the moment
    a space is dropped - so the two readings are searched separately and only the
    security vocabulary is looked for in this one.
    """
    return _OBFUSCATION_FILL.sub("", text.translate(_LEETSPEAK))


def assess(text: str) -> Assessment:
    """Score ``text`` and return the verdict with every phrase that produced it."""
    folded = numerals.normalise(text).lower()
    smuggled = deobfuscate(folded)
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
    # The obfuscated reading, for the security words only. Anything matched here is
    # recorded with the family it belongs to and the fact that it was obfuscated, so the
    # report can separate "asked for an OTP" from "asked for an 0.t.p".
    if not any(hit.family == OTP for hit in hits):
        for word in OBFUSCATED:
            if word in smuggled and word not in folded:
                hits.append(
                    Hit(
                        OTP,
                        FAMILIES[OTP][0],
                        f"{word} (obfuscated)",
                        0,
                        0,
                    )
                )
                break

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
