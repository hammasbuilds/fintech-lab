"""Read an amount out of code-mixed Urdu, Roman Urdu and English text.

This is the part that has to be right. Everything else in the assistant can be re-asked;
an amount that is silently wrong by a factor of a hundred is a wrong transfer.

What it has to cope with, all of which are how people actually say amounts here:

* ``15000``, ``15,000``, ``Rs 15000``, ``15k``, ``۱۵۰۰۰`` (Urdu digits)
* ``pandrah hazaar``, ``pandra hazar``, ``15 hazaar`` - the same amount three ways
* ``sawa lakh`` (1.25 lakh), ``dedh hazaar`` (1,500), ``dhai lakh`` (250,000),
  ``saadhe teen hazaar`` (3,500), ``paune do lakh`` (175,000) - fractional modifiers
  that attach to the number *after* them, and change the value by a quarter or a half
* ``do hazaar paanch sau`` - two scales added together in one phrase

Every spelling variant is listed rather than guessed at by a fuzzy match. A fuzzy match
on numbers is how ``saat`` (seven) becomes ``saath`` (sixty).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Multipliers, largest first so the scanner prefers the longest match.
SCALES: dict[str, int] = {
    "crore": 10_000_000,
    "karor": 10_000_000,
    "karore": 10_000_000,
    "کروڑ": 10_000_000,
    "lakh": 100_000,
    "lac": 100_000,
    "lakhs": 100_000,
    "lakhon": 100_000,
    "لاکھ": 100_000,
    "thousand": 1_000,
    "hazaar": 1_000,
    "hazar": 1_000,
    "hazzar": 1_000,
    "hajar": 1_000,
    "ہزار": 1_000,
    "hundred": 100,
    "sau": 100,
    "so": 100,
    "saw": 100,
    "سو": 100,
}

#: Whole numbers. Variants are listed because the spelling is genuinely unsettled, and
#: because ``saat``/``saath`` and ``nau``/``nabbay`` are a digit apart in sound and an
#: order of magnitude apart in money.
UNITS: dict[str, float] = {
    "aik": 1, "ek": 1, "ik": 1, "one": 1, "ایک": 1,
    "do": 2, "two": 2, "دو": 2,
    "teen": 3, "tin": 3, "three": 3, "تین": 3,
    "char": 4, "chaar": 4, "four": 4, "چار": 4,
    "panch": 5, "paanch": 5, "panj": 5, "five": 5, "پانچ": 5,
    "che": 6, "chay": 6, "chhe": 6, "six": 6, "چھ": 6,
    "saat": 7, "sat": 7, "seven": 7, "سات": 7,
    "aath": 8, "ath": 8, "eight": 8, "آٹھ": 8,
    "nau": 9, "no": 9, "nine": 9, "نو": 9,
    "das": 10, "dus": 10, "ten": 10, "دس": 10,
    "gyarah": 11, "gyara": 11, "eleven": 11, "گیارہ": 11,
    "barah": 12, "bara": 12, "twelve": 12, "بارہ": 12,
    "terah": 13, "tera": 13, "thirteen": 13, "تیرہ": 13,
    "chaudah": 14, "chauda": 14, "fourteen": 14, "چودہ": 14,
    "pandrah": 15, "pandra": 15, "pandhra": 15, "fifteen": 15, "پندرہ": 15,
    "solah": 16, "sola": 16, "sixteen": 16, "سولہ": 16,
    "satrah": 17, "satra": 17, "seventeen": 17, "سترہ": 17,
    "atharah": 18, "athara": 18, "eighteen": 18, "اٹھارہ": 18,
    "unnees": 19, "unnis": 19, "nineteen": 19, "انیس": 19,
    "bees": 20, "bis": 20, "twenty": 20, "بیس": 20,
    "pachees": 25, "pachis": 25, "پچیس": 25,
    "tees": 30, "tis": 30, "thirty": 30, "تیس": 30,
    "chalees": 40, "chalis": 40, "forty": 40, "چالیس": 40,
    "pachaas": 50, "pachas": 50, "fifty": 50, "پچاس": 50,
    "saath": 60, "sath": 60, "sixty": 60, "ساٹھ": 60,
    "sattar": 70, "satar": 70, "seventy": 70, "ستر": 70,
    "assi": 80, "asi": 80, "eighty": 80, "اسی": 80,
    "nabbay": 90, "nabbe": 90, "ninety": 90, "نوے": 90,
}

#: Modifiers that attach to the number *after* them. ``base`` is used when the modifier
#: stands alone, as in ``dedh hazaar`` or ``sawa lakh``.
MODIFIERS: dict[str, tuple[float, float]] = {
    # word: (offset applied to the following number, value when it stands alone)
    "sawa": (0.25, 1.25),
    "sava": (0.25, 1.25),
    "سوا": (0.25, 1.25),
    "saadhe": (0.5, 0.5),
    "sadhe": (0.5, 0.5),
    "sarhe": (0.5, 0.5),
    "ساڑھے": (0.5, 0.5),
    "paune": (-0.25, 0.75),
    "pone": (-0.25, 0.75),
    "پونے": (-0.25, 0.75),
}

#: Standalone fractional amounts.
STANDALONE: dict[str, float] = {
    "dedh": 1.5, "derh": 1.5, "deed": 1.5, "ڈیڑھ": 1.5,
    "dhai": 2.5, "dhaai": 2.5, "ڈھائی": 2.5,
    "aadha": 0.5, "adha": 0.5, "half": 0.5, "آدھا": 0.5,
}

CURRENCY_WORDS = {
    "rupay", "rupaye", "rupees", "rupee", "rs", "pkr", "rupya", "روپے", "روپیہ",
}

#: Words that may appear inside an amount phrase without ending it.
FILLER = {"aur", "and", "ke", "ka", "ki"}

_URDU_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

#: Mobile and account numbers. These are digits that are never an amount, and they have
#: to be recognised *before* the amount scan rather than filtered by length afterwards:
#: "0321-4455667" splits on the hyphen into two short runs, and a length rule lets both
#: of them through as amounts.
_NOT_AN_AMOUNT = re.compile(
    r"""
      0\d{2,3}[\s-]?\d{7}        # 0300 1234567, 0321-4455667
    | \b\d{10,}\b                # a long bare run: account or reference
    | \b[A-Z]{2}\d{2}[A-Z0-9]{6,}\b  # IBAN-shaped
    """,
    re.VERBOSE,
)

_SHORTHAND = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([kK])(?![\w])")
_PLAIN = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w])")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class Amount:
    value: float
    text: str
    start: int
    end: int
    source: str  # "digits" | "shorthand" | "words"

    @property
    def minor_units(self) -> int:
        return int(round(self.value * 100))

    def as_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "source": self.source,
        }


def normalise(text: str) -> str:
    """Fold Urdu and Arabic-Indic digits to ASCII. Nothing else is changed."""
    return text.translate(_URDU_DIGITS)


def find_all(text: str) -> list[Amount]:
    """Every amount in ``text``, in the order they appear, non-overlapping."""
    folded = normalise(text)
    reserved = [(m.start(), m.end()) for m in _NOT_AN_AMOUNT.finditer(folded)]
    found: list[Amount] = []
    found += _digit_amounts(folded)
    found += _word_amounts(folded)
    found = [
        amount
        for amount in found
        if not any(amount.start < end and amount.end > start for start, end in reserved)
    ]
    found.sort(key=lambda amount: (amount.start, -(amount.end - amount.start)))

    # A word phrase such as "15 hazaar" contains a digit match; keep the longer span.
    kept: list[Amount] = []
    for amount in found:
        if any(amount.start >= other.start and amount.end <= other.end for other in kept):
            continue
        kept = [other for other in kept if not (other.start >= amount.start and other.end <= amount.end)]
        kept.append(amount)
    kept.sort(key=lambda amount: amount.start)
    return kept


def parse_one(text: str) -> Amount | None:
    """The single amount in ``text``, or ``None`` when there is not exactly one."""
    found = find_all(text)
    return found[0] if len(found) == 1 else None


def _digit_amounts(text: str) -> list[Amount]:
    out: list[Amount] = []
    taken: list[tuple[int, int]] = []
    for match in _SHORTHAND.finditer(text):
        value = float(match.group(1)) * 1000
        out.append(Amount(value, match.group(0), match.start(), match.end(), "shorthand"))
        taken.append((match.start(), match.end()))
    for match in _PLAIN.finditer(text):
        if any(match.start() >= start and match.end() <= end for start, end in taken):
            continue
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:  # pragma: no cover - the pattern cannot produce this
            continue
        # A long run of digits is an account or phone number, not an amount.
        if len(raw.split(".")[0]) > 9:
            continue
        out.append(Amount(value, match.group(0), match.start(), match.end(), "digits"))
    return out


def _word_amounts(text: str) -> list[Amount]:
    """Scan word runs, building a value whenever a scale or standalone word appears."""
    tokens: list[tuple[str, int, int]] = []
    for match in _WORD.finditer(text):
        tokens.append((match.group(0).lower(), match.start(), match.end()))
    # Digits can be the number in front of a scale word: "15 hazaar".
    for match in _PLAIN.finditer(text):
        tokens.append((match.group(1).replace(",", ""), match.start(), match.end()))
    tokens.sort(key=lambda item: item[1])

    out: list[Amount] = []
    index = 0
    while index < len(tokens):
        if not _starts_amount(tokens[index][0]):
            index += 1
            continue
        value, consumed, used_scale = _consume(tokens, index)
        if value is None or not used_scale:
            index += 1
            continue
        start = tokens[index][1]
        end = tokens[index + consumed - 1][2]
        out.append(Amount(value, text[start:end], start, end, "words"))
        index += consumed
    return out


def _starts_amount(token: str) -> bool:
    return token in UNITS or token in MODIFIERS or token in STANDALONE or token.isdigit()


def _consume(
    tokens: list[tuple[str, int, int]], index: int
) -> tuple[float | None, int, bool]:
    """Read one amount phrase starting at ``index``.

    Returns the value, how many tokens it used, and whether a scale word was involved -
    the caller needs the last part because a bare ``paanch`` is a quantity ("paanch
    transactions"), not an amount, while ``paanch hazaar`` is money.
    """
    total = 0.0
    current: float | None = None
    pending: float | None = None
    used_scale = False
    position = index
    last_useful = index - 1
    # A compound amount descends through the scales: "char lakh pachaas hazaar". A scale
    # that repeats or grows is a *second* amount, not a continuation - "5 hazaar aur 7
    # hazaar" is two transfers someone is weighing up, and reading it as 12,000 would be
    # a new number nobody said.
    last_scale: int | None = None
    #: Token index of the last scale word consumed. When a repeated scale ends the
    #: phrase, the partial number read after that point is given back to the scanner
    #: rather than folded into this amount.
    end_of_last_scale = index - 1

    while position < len(tokens):
        token = tokens[position][0]
        if token in FILLER and current is None and pending is None:
            position += 1
            continue
        if token in CURRENCY_WORDS:
            position += 1
            last_useful = position - 1
            continue
        if token in MODIFIERS:
            offset, alone = MODIFIERS[token]
            pending = offset
            current = alone if current is None else current
            position += 1
            last_useful = position - 1
            continue
        if token in STANDALONE:
            current = STANDALONE[token]
            pending = None
            position += 1
            last_useful = position - 1
            continue
        if token in UNITS or token.isdigit():
            number = UNITS[token] if token in UNITS else float(token)
            if pending is not None:
                number += pending
                pending = None
            current = number if current is None or current in (1.25, 0.5, 0.75) else current + number
            position += 1
            last_useful = position - 1
            continue
        if token in SCALES:
            scale = SCALES[token]
            if last_scale is not None and scale >= last_scale:
                # A second amount starts here. Hand back everything read since the last
                # scale word so the scanner sees "7 hazaar" as its own amount.
                return total, max(1, end_of_last_scale - index + 1), used_scale
            base = current if current is not None else 1.0
            total += base * scale
            current = None
            pending = None
            used_scale = True
            last_scale = scale
            position += 1
            last_useful = position - 1
            end_of_last_scale = last_useful
            continue
        break

    if current is not None:
        total += current
    if total == 0.0:
        return None, 1, False
    return total, max(1, last_useful - index + 1), used_scale


def say(value: float, language: str = "en") -> str:
    """Render an amount as words, for reading an action back to the user.

    The read-back is the safety mechanism, so it uses the local scale names a user
    actually checks against - lakh, not hundred thousand.
    """
    whole = int(round(value))
    if language == "ur":
        names = [(10_000_000, "crore"), (100_000, "lakh"), (1_000, "hazaar"), (100, "sau")]
        joiner = " "
        tail = "rupay"
    else:
        names = [(10_000_000, "crore"), (100_000, "lakh"), (1_000, "thousand"), (100, "hundred")]
        joiner = " "
        tail = "rupees"
    parts: list[str] = []
    remaining = whole
    for scale, name in names:
        if remaining >= scale:
            count = remaining // scale
            remaining -= count * scale
            parts.append(f"{count} {name}")
    if remaining or not parts:
        parts.append(str(remaining))
    return joiner.join(parts) + " " + tail
