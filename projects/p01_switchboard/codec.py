"""Pack and unpack ISO 8583 messages in the ASCII-on-the-wire form test tooling uses.

Layout: a 4-character message type indicator, a 16-hex-character primary bitmap, a
second 16 characters when bit 1 is set, then every present data element in ascending
order - fixed ones at their declared width, variable ones behind a 2- or 3-digit length.

The parser is deliberately unforgiving. A switch that guesses at a malformed message is
worse than one that rejects it, and this codec is the oracle the rest of the project is
graded against, so it may not be generous about what it accepts.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from dataclasses import field as dc_field

from . import fields as F

MTI_LENGTH = 4
BITMAP_HEX = 16
HEXDIGITS = set(string.hexdigits.upper())

_ANS_EXTRA = set(" .,-/'&()*+:;=?@#$%!_<>[]{}|^~")


class CodecError(Exception):
    """A message that cannot be read. The text names the offset wherever it is known."""


@dataclass
class Message:
    """A parsed message. ``fields`` is ordered by data element number."""

    mti: str
    fields: dict[int, str] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.fields = {de: self.fields[de] for de in sorted(self.fields)}

    def get(self, de: int, default: str | None = None) -> str | None:
        return self.fields.get(de, default)

    @property
    def present(self) -> list[int]:
        return sorted(self.fields)

    def replace(self, mti: str | None = None, **changes: str | None) -> Message:
        """Derive a copy. ``de4="000000001000"`` sets DE 4; ``de4=None`` removes it."""
        new = dict(self.fields)
        for key, value in changes.items():
            if not key.startswith("de"):
                raise ValueError(f"unexpected keyword {key!r}; expected de<number>")
            de = int(key[2:])
            if value is None:
                new.pop(de, None)
            else:
                new[de] = value
        return Message(mti=mti or self.mti, fields=new)


# --------------------------------------------------------------------------- MTI

_CLASSES = {
    "1": "authorization",
    "2": "financial",
    "3": "file action",
    "4": "reversal or chargeback",
    "5": "reconciliation",
    "6": "administrative",
    "7": "fee collection",
    "8": "network management",
}
_FUNCTIONS = {
    "0": "request",
    "1": "request response",
    "2": "advice",
    "3": "advice response",
    "4": "notification",
    "8": "response acknowledgement",
}
_ORIGINS = {
    "0": "acquirer",
    "1": "acquirer repeat",
    "2": "issuer",
    "3": "issuer repeat",
    "4": "other",
    "5": "other repeat",
}
_VERSIONS = {"0": "ISO 8583:1987", "1": "ISO 8583:1993", "2": "ISO 8583:2003"}


def describe_mti(mti: str) -> dict[str, str]:
    """Break an MTI into its four positions. Raises ``CodecError`` on a bad one.

    A reserved value in *any* of the four positions is rejected rather than labelled
    "reserved". An MTI whose class or function the switch does not recognise cannot be
    routed or answered, so accepting it only moves the failure somewhere later and less
    obvious. Random single-character corruption of a valid MTI found this: tolerating
    reserved positions quietly turned a 0200 into an accepted 0900.
    """
    if len(mti) != MTI_LENGTH or not mti.isdigit():
        raise CodecError(f"message type indicator must be 4 digits, got {mti!r}")
    positions = (
        ("version", _VERSIONS, mti[0]),
        ("class", _CLASSES, mti[1]),
        ("function", _FUNCTIONS, mti[2]),
        ("origin", _ORIGINS, mti[3]),
    )
    out: dict[str, str] = {}
    for index, (name, table, digit) in enumerate(positions, start=1):
        if digit not in table:
            raise CodecError(
                f"message type indicator {mti!r} has {digit!r} in position {index} "
                f"({name}), which is reserved; known values are "
                f"{', '.join(sorted(table))}"
            )
        out[name] = table[digit]
    return out


def is_request(mti: str) -> bool:
    return describe_mti(mti)["function"] in {"request", "advice"}


def response_mti(mti: str) -> str:
    """The MTI a switch must answer ``mti`` with: the function digit gains one."""
    parts = describe_mti(mti)
    if parts["function"] not in {"request", "advice"}:
        raise CodecError(f"{mti} is not a request, so it has no response MTI")
    return mti[:2] + str(int(mti[2]) + 1) + mti[3]


# ------------------------------------------------------------------------ bitmap


def build_bitmap(present: list[int]) -> str:
    """Hex bitmap covering ``present``. Bit 1 is set by this function, never by callers."""
    des = set(present)
    if any(de < 2 or de > 128 for de in des):
        raise CodecError(f"data elements must be 2..128, got {sorted(des)}")
    secondary = any(de > 64 for de in des)
    if secondary:
        des.add(1)
    width = 128 if secondary else 64
    bits = ["0"] * width
    for de in des:
        bits[de - 1] = "1"
    nibbles = ("".join(bits[i : i + 4]) for i in range(0, width, 4))
    return "".join(format(int(nibble, 2), "X") for nibble in nibbles)


def parse_bitmap(raw: str, offset: int = 0) -> tuple[list[int], int]:
    """Read the bitmap at ``offset``. Returns the present elements and the new offset."""
    primary = raw[offset : offset + BITMAP_HEX]
    if len(primary) < BITMAP_HEX:
        raise CodecError(
            f"message ends inside the primary bitmap at offset {offset}: needed "
            f"{BITMAP_HEX} hex characters, found {len(primary)}"
        )
    if set(primary.upper()) - HEXDIGITS:
        raise CodecError(f"primary bitmap {primary!r} at offset {offset} is not hexadecimal")
    cursor = offset + BITMAP_HEX
    bits = _hex_to_bits(primary)
    if bits[0] == "1":
        extra = raw[cursor : cursor + BITMAP_HEX]
        if len(extra) < BITMAP_HEX:
            raise CodecError(
                f"bitmap bit 1 is set so a secondary bitmap must follow at offset "
                f"{cursor}, but only {len(extra)} characters remain"
            )
        if set(extra.upper()) - HEXDIGITS:
            raise CodecError(f"secondary bitmap {extra!r} at offset {cursor} is not hexadecimal")
        bits += _hex_to_bits(extra)
        cursor += BITMAP_HEX
    present = [i + 1 for i, bit in enumerate(bits) if bit == "1" and i != 0]
    return present, cursor


def _hex_to_bits(raw: str) -> str:
    return "".join(format(int(ch, 16), "04b") for ch in raw)


# -------------------------------------------------------------------------- pack


def normalise(de: int, value: str) -> str:
    """Pad ``value`` the way its class requires, so callers can pass the plain value.

    Numeric elements are right-justified and zero-filled; text elements are
    left-justified and space-filled. Variable-length elements are never padded - their
    length prefix carries the real length.
    """
    spec = F.get(de)
    if spec.var != F.FIXED or len(value) >= spec.length:
        return value
    if spec.cls in {"n", "x+n", "b"}:
        return value.rjust(spec.length, "0")
    return value.ljust(spec.length, " ")


def pack(message: Message) -> str:
    """Serialise ``message``. Every value is checked before a single character is emitted."""
    describe_mti(message.mti)
    problems = check_values(message.fields)
    if problems:
        raise CodecError("; ".join(problems))
    out = [message.mti, build_bitmap(message.present)]
    for de in message.present:
        spec = F.get(de)
        value = normalise(de, message.fields[de])
        if spec.var == F.FIXED:
            out.append(value)
        else:
            out.append(str(len(value)).zfill(spec.prefix_digits))
            out.append(value)
    return "".join(out)


def check_values(values: dict[int, str]) -> list[str]:
    """Every way a set of field values breaks its own dictionary entry, as plain text."""
    problems: list[str] = []
    for de in sorted(values):
        try:
            spec = F.get(de)
        except KeyError as exc:
            problems.append(str(exc))
            continue
        value = values[de]
        if not isinstance(value, str):
            problems.append(f"DE {de} ({spec.name}) must be a string, got {type(value).__name__}")
            continue
        padded = normalise(de, value)
        if spec.var == F.FIXED and len(padded) != spec.length:
            problems.append(
                f"DE {de} ({spec.name}) is {spec.spec}: expected exactly {spec.length} "
                f"characters, got {len(padded)}"
            )
        if spec.var != F.FIXED and len(padded) > spec.length:
            problems.append(
                f"DE {de} ({spec.name}) is {spec.spec}: at most {spec.length} characters, "
                f"got {len(padded)}"
            )
        bad = _class_violation(spec.cls, padded)
        if bad is not None:
            problems.append(
                f"DE {de} ({spec.name}) is class {spec.cls}: {bad} is not allowed in that class"
            )
    return problems


def luhn_ok(pan: str) -> bool:
    """Whether ``pan`` passes the Luhn check digit.

    Every real card number does. A PAN that fails it was mistyped, truncated or invented,
    and catching that at the switch is cheaper than catching it at the issuer.
    """
    digits = [int(ch) for ch in pan if ch.isdigit()]
    if len(digits) < 12 or len(digits) != len(pan.strip()):
        return False
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += digit
    return total % 10 == 0


def permits(cls: str, char: str) -> bool:
    """Whether ``char`` is legal anywhere in a field of class ``cls``.

    Positional rules are ignored on purpose - ``x+n`` allows C and D only in its first
    position, and this answers the weaker per-character question, which is what a
    single-character corruption needs.
    """
    if cls == "x+n":
        return char.isdigit() or char in {"C", "D"}
    return _class_violation(cls, char) is None


def _class_violation(cls: str, value: str) -> str | None:
    if cls == "n":
        bad = [ch for ch in value if not ch.isdigit()]
    elif cls == "x+n":
        if value and value[0] not in {"C", "D"}:
            return f"a leading sign of {value[0]!r} (expected C or D)"
        bad = [ch for ch in value[1:] if not ch.isdigit()]
    elif cls == "an":
        bad = [ch for ch in value if not (ch.isdigit() or ch.isalpha() or ch == " ")]
    elif cls == "ans":
        bad = [ch for ch in value if not (ch.isdigit() or ch.isalpha() or ch in _ANS_EXTRA)]
    elif cls == "b":
        bad = [ch for ch in value if ch.upper() not in HEXDIGITS]
    elif cls == "z":
        bad = [ch for ch in value if not (ch.isdigit() or ch in {"=", "D"})]
    else:  # pragma: no cover - CLASSES is closed
        raise CodecError(f"unknown character class {cls!r}")
    if not bad:
        return None
    return repr("".join(sorted(set(bad))))


# ------------------------------------------------------------------------ unpack


def _clean(raw: str) -> str:
    """Strip only what a paste adds: line breaks, tabs and leading space.

    A **trailing** space is left alone. Text data elements are left-justified and
    space-filled, so the last character of a message can legitimately be a space, and
    ``str.strip()`` deletes it - which shortens the message and makes the final field
    report itself as truncated. A fuzz run found this by replacing the last digit of
    DE 102 with a space: a legal character for that class, rejected by the parser for a
    reason that had nothing to do with the message.
    """
    return raw.replace("\r", "").replace("\n", "").replace("\t", "").lstrip()


def field_offsets(raw: str) -> dict[int, tuple[int, int]]:
    """Where each data element's value sits in ``raw``, as ``{de: (offset, length)}``.

    The UI uses it to highlight a field inside the raw message, and the fault injector
    uses it to corrupt one named field rather than a position that happens to be near
    the end. Both want the same walk the parser does, so it is done once here.
    """
    message, spans = _walk(_clean(raw))
    del message
    return spans


def unpack(raw: str) -> Message:
    """Parse ``raw``. Line breaks and surrounding space are tolerated, nothing else is.

    Character classes are enforced here, not only on the way out. A parser that reads
    field *widths* but never field *contents* will happily hand back an amount with
    letters in it, which is how a malformed message gets as far as the ledger.
    """
    message, _ = _walk(_clean(raw))
    problems = check_values(message.fields)
    if problems:
        raise CodecError("; ".join(problems))
    return message


def _walk(text: str) -> tuple[Message, dict[int, tuple[int, int]]]:
    """Parse the framing only. Returns the message and each field's span in ``text``."""
    if len(text) < MTI_LENGTH + BITMAP_HEX:
        raise CodecError(
            f"message is {len(text)} characters; the MTI and primary bitmap alone need "
            f"{MTI_LENGTH + BITMAP_HEX}"
        )
    mti = text[:MTI_LENGTH]
    describe_mti(mti)
    present, cursor = parse_bitmap(text, MTI_LENGTH)
    values: dict[int, str] = {}
    spans: dict[int, tuple[int, int]] = {}
    for de in present:
        try:
            spec = F.get(de)
        except KeyError:
            raise CodecError(
                f"bitmap sets DE {de} but it is not in the dictionary, so the remaining "
                f"{len(text) - cursor} characters cannot be read"
            ) from None
        if spec.var == F.FIXED:
            chunk = text[cursor : cursor + spec.length]
            if len(chunk) < spec.length:
                raise CodecError(
                    f"DE {de} ({spec.name}) needs {spec.length} characters at offset "
                    f"{cursor}, but only {len(chunk)} remain"
                )
            spans[de] = (cursor, spec.length)
            cursor += spec.length
        else:
            prefix = text[cursor : cursor + spec.prefix_digits]
            if len(prefix) < spec.prefix_digits or not prefix.isdigit():
                raise CodecError(
                    f"DE {de} ({spec.name}) needs a {spec.prefix_digits}-digit length at "
                    f"offset {cursor}, found {prefix!r}"
                )
            declared = int(prefix)
            if declared > spec.length:
                raise CodecError(
                    f"DE {de} ({spec.name}) declares length {declared} at offset {cursor}, "
                    f"above its maximum of {spec.length}"
                )
            cursor += spec.prefix_digits
            chunk = text[cursor : cursor + declared]
            if len(chunk) < declared:
                raise CodecError(
                    f"DE {de} ({spec.name}) declares {declared} characters at offset "
                    f"{cursor}, but only {len(chunk)} remain"
                )
            spans[de] = (cursor, declared)
            cursor += declared
        values[de] = chunk
    if cursor != len(text):
        last = present[-1] if present else "none"
        raise CodecError(
            f"{len(text) - cursor} trailing characters after the last data element "
            f"(DE {last}) at offset {cursor}: {text[cursor : cursor + 24]!r}"
        )
    return Message(mti=mti, fields=values), spans


def round_trip(message: Message) -> bool:
    """Pack, parse and pack again. Used as a property check on generated traffic."""
    once = pack(message)
    return pack(unpack(once)) == once
