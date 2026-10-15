"""The ISO 8583 data element dictionary.

Only the elements a card switch actually populates are here - 50 of the 128 - because a
dictionary padded out with "reserved for ISO use" entries invites the codec to accept
fields no acquirer will ever send. Each entry carries the character class and the length
discipline, which is what the codec and the value checker both read.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Character classes. ``n`` numeric, ``an`` alphanumeric, ``ans`` alphanumeric plus
#: punctuation and space, ``b`` binary carried as hex, ``z`` magnetic-stripe track data,
#: ``x+n`` a credit/debit sign followed by digits.
CLASSES = ("n", "an", "ans", "b", "z", "x+n")

FIXED = "FIXED"
LLVAR = "LLVAR"
LLLVAR = "LLLVAR"


@dataclass(frozen=True)
class Field:
    de: int
    name: str
    cls: str
    length: int
    var: str = FIXED

    @property
    def spec(self) -> str:
        """The shorthand a payments spec would print, e.g. ``n..19`` or ``ans8``."""
        return f"{self.cls}{'..' if self.var != FIXED else ''}{self.length}"

    @property
    def prefix_digits(self) -> int:
        return {FIXED: 0, LLVAR: 2, LLLVAR: 3}[self.var]


_FIELDS: tuple[Field, ...] = (
    Field(2, "Primary account number", "n", 19, LLVAR),
    Field(3, "Processing code", "n", 6),
    Field(4, "Amount, transaction", "n", 12),
    Field(5, "Amount, settlement", "n", 12),
    Field(6, "Amount, cardholder billing", "n", 12),
    Field(7, "Transmission date and time", "n", 10),
    Field(9, "Conversion rate, settlement", "n", 8),
    Field(10, "Conversion rate, cardholder billing", "n", 8),
    Field(11, "System trace audit number", "n", 6),
    Field(12, "Local transaction time", "n", 6),
    Field(13, "Local transaction date", "n", 4),
    Field(14, "Expiration date", "n", 4),
    Field(15, "Settlement date", "n", 4),
    Field(16, "Conversion date", "n", 4),
    Field(18, "Merchant category code", "n", 4),
    Field(19, "Acquiring institution country code", "n", 3),
    Field(22, "Point of service entry mode", "n", 3),
    Field(23, "Card sequence number", "n", 3),
    Field(25, "Point of service condition code", "n", 2),
    Field(26, "Point of service PIN capture code", "n", 2),
    Field(28, "Amount, transaction fee", "x+n", 8),
    Field(32, "Acquiring institution identification code", "n", 11, LLVAR),
    Field(33, "Forwarding institution identification code", "n", 11, LLVAR),
    Field(35, "Track 2 data", "z", 37, LLVAR),
    Field(37, "Retrieval reference number", "an", 12),
    Field(38, "Authorization identification response", "an", 6),
    Field(39, "Response code", "an", 2),
    Field(41, "Card acceptor terminal identification", "ans", 8),
    Field(42, "Card acceptor identification code", "ans", 15),
    Field(43, "Card acceptor name and location", "ans", 40),
    Field(48, "Additional data, private", "ans", 999, LLLVAR),
    Field(49, "Currency code, transaction", "n", 3),
    Field(50, "Currency code, settlement", "n", 3),
    Field(52, "Personal identification number data", "b", 16),
    Field(53, "Security related control information", "n", 16),
    Field(54, "Additional amounts", "an", 120, LLLVAR),
    Field(55, "Integrated circuit card data", "b", 999, LLLVAR),
    Field(60, "Reserved, national", "ans", 999, LLLVAR),
    Field(61, "Reserved, national", "ans", 999, LLLVAR),
    Field(62, "Reserved, private", "ans", 999, LLLVAR),
    Field(63, "Reserved, private", "ans", 999, LLLVAR),
    Field(70, "Network management information code", "n", 3),
    Field(90, "Original data elements", "n", 42),
    Field(95, "Replacement amounts", "an", 42),
    Field(100, "Receiving institution identification code", "n", 11, LLVAR),
    Field(102, "Account identification 1", "ans", 28, LLVAR),
    Field(103, "Account identification 2", "ans", 28, LLVAR),
    Field(120, "Reserved, private", "ans", 999, LLLVAR),
    Field(123, "Reserved, private", "ans", 999, LLLVAR),
    Field(128, "Message authentication code", "b", 16),
)

DICTIONARY: dict[int, Field] = {f.de: f for f in _FIELDS}


def get(de: int) -> Field:
    try:
        return DICTIONARY[de]
    except KeyError:
        raise KeyError(f"data element {de} is not in the dictionary") from None


def known() -> list[int]:
    return sorted(DICTIONARY)
