"""Parse DE 55, the chip data, as BER-TLV - and check it against the rest of the message.

DE 55 is the one field a decoder usually gives up on and prints as a hex blob. It is
BER-TLV: a tag, a length, a value, repeated, with multi-byte tags and multi-byte lengths.
Inside it sit values that are supposed to agree with the top-level message, and **that is
where a real class of defect lives**: tag 9F02 is the authorised amount as the chip saw
it, and it must equal DE 4. When a terminal or a switch rewrites one and not the other,
the message still parses, every field is individually well-formed, and the cardholder is
debited an amount the chip never approved.

So this module does two things: it reads the TLV, and it exports the cross-field checks
that turn the contents into invariants :mod:`lifecycle` can enforce.

The tag dictionary covers the tags that appear in an authorisation. An unknown tag is
reported as unknown with its bytes intact, never skipped - a parser that silently drops
what it does not recognise is how a mandatory tag goes missing unnoticed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TAGS: dict[str, tuple[str, str]] = {
    "4F": ("Application identifier (AID)", "b"),
    "50": ("Application label", "ans"),
    "5F2A": ("Transaction currency code", "n"),
    "5F34": ("Application PAN sequence number", "n"),
    "82": ("Application interchange profile", "b"),
    "84": ("Dedicated file name", "b"),
    "95": ("Terminal verification results", "b"),
    "9A": ("Transaction date", "n"),
    "9C": ("Transaction type", "n"),
    "9F02": ("Amount, authorised", "n"),
    "9F03": ("Amount, other", "n"),
    "9F06": ("Application identifier, terminal", "b"),
    "9F09": ("Application version number", "b"),
    "9F10": ("Issuer application data", "b"),
    "9F1A": ("Terminal country code", "n"),
    "9F1E": ("Interface device serial number", "ans"),
    "9F26": ("Application cryptogram", "b"),
    "9F27": ("Cryptogram information data", "b"),
    "9F33": ("Terminal capabilities", "b"),
    "9F34": ("Cardholder verification method results", "b"),
    "9F35": ("Terminal type", "n"),
    "9F36": ("Application transaction counter", "b"),
    "9F37": ("Unpredictable number", "b"),
    "9F41": ("Transaction sequence counter", "n"),
    "9F53": ("Transaction category code", "ans"),
}

#: Tags an online authorisation cannot be assessed without.
REQUIRED_FOR_AUTHORISATION = ("9F26", "9F27", "9F36", "82", "9F10")

CRYPTOGRAM_TYPES = {
    "00": "AAC - declined offline by the card",
    "40": "TC - approved offline by the card",
    "80": "ARQC - the card asked to go online",
}


class EmvError(Exception):
    """DE 55 does not decode as BER-TLV. The message says where it stopped."""


@dataclass
class Tag:
    tag: str
    name: str
    length: int
    value: str
    offset: int
    known: bool = True
    children: list[Tag] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "name": self.name,
            "length": self.length,
            "value": self.value,
            "offset": self.offset,
            "known": self.known,
            "meaning": meaning(self.tag, self.value),
            "children": [child.as_dict() for child in self.children],
        }


def parse(hex_value: str) -> list[Tag]:
    """Parse a DE 55 hex string into its tags, in order."""
    text = "".join(hex_value.split()).upper()
    if not text:
        return []
    if len(text) % 2:
        raise EmvError(f"DE 55 is {len(text)} hex characters, which is not whole bytes")
    try:
        data = bytes.fromhex(text)
    except ValueError as exc:
        raise EmvError(f"DE 55 is not hexadecimal: {exc}") from exc
    tags, cursor = _parse_run(data, 0, len(data))
    if cursor != len(data):  # pragma: no cover - _parse_run consumes or raises
        raise EmvError(f"{len(data) - cursor} bytes left over after the last tag")
    return tags


def _parse_run(data: bytes, start: int, end: int) -> tuple[list[Tag], int]:
    out: list[Tag] = []
    cursor = start
    while cursor < end:
        tag_start = cursor
        if cursor >= len(data):
            raise EmvError(f"a tag was expected at byte {cursor} but the data ends there")
        first = data[cursor]
        tag_bytes = [first]
        cursor += 1
        # A low nibble of all ones means the tag continues into the next byte.
        if first & 0x1F == 0x1F:
            while True:
                if cursor >= end:
                    raise EmvError(
                        f"the multi-byte tag starting at byte {tag_start} is unterminated"
                    )
                nxt = data[cursor]
                tag_bytes.append(nxt)
                cursor += 1
                if not nxt & 0x80:
                    break
        tag = bytes(tag_bytes).hex().upper()

        if cursor >= end:
            raise EmvError(f"tag {tag} at byte {tag_start} has no length byte")
        length_byte = data[cursor]
        cursor += 1
        if length_byte & 0x80:
            count = length_byte & 0x7F
            if count == 0:
                raise EmvError(
                    f"tag {tag} uses the indefinite length form, which BER-TLV in DE 55 "
                    f"does not allow"
                )
            if cursor + count > end:
                raise EmvError(f"tag {tag} has a {count}-byte length that runs off the end")
            length = int.from_bytes(data[cursor : cursor + count], "big")
            cursor += count
        else:
            length = length_byte

        if cursor + length > end:
            raise EmvError(
                f"tag {tag} at byte {tag_start} declares {length} bytes but only "
                f"{end - cursor} remain"
            )
        value = data[cursor : cursor + length].hex().upper()
        name, _ = TAGS.get(tag, (f"unknown tag {tag}", "b"))
        node = Tag(
            tag=tag,
            name=name,
            length=length,
            value=value,
            offset=tag_start,
            known=tag in TAGS,
        )
        # A constructed tag (bit 6 of the first byte) holds further tags.
        if first & 0x20 and length:
            node.children, _ = _parse_run(data, cursor, cursor + length)
        cursor += length
        out.append(node)
    return out, cursor


def flatten(tags: list[Tag]) -> dict[str, str]:
    """``{tag: value}`` including nested tags, for the cross-field checks."""
    out: dict[str, str] = {}
    for node in tags:
        out[node.tag] = node.value
        out.update(flatten(node.children))
    return out


def meaning(tag: str, value: str) -> str:
    if tag == "9F27":
        return CRYPTOGRAM_TYPES.get(value[:2], f"unknown cryptogram information {value}")
    if tag == "9F02":
        return f"authorised amount {int(value):,} in minor units" if value.isdigit() else ""
    if tag == "9F36":
        return f"transaction counter {int(value, 16)}" if value else ""
    if tag == "5F2A":
        return f"currency {value}"
    if tag == "9C":
        return {
            "00": "purchase",
            "01": "cash advance",
            "09": "purchase with cashback",
            "20": "refund",
        }.get(value, f"transaction type {value}")
    if tag == "9A" and len(value) == 6:
        return f"dated 20{value[0:2]}-{value[2:4]}-{value[4:6]}"
    if tag == "95" and value:
        return (
            "terminal verification results, all zero"
            if set(value) == {"0"}
            else ("terminal verification results, at least one bit set")
        )
    return ""


# ------------------------------------------------------------- cross-field checks


def amount_mismatch(de55: str, de4: str | None) -> str | None:
    """The defect this module exists for: chip tag 9F02 disagreeing with DE 4."""
    if de4 is None:
        return None
    try:
        tags = flatten(parse(de55))
    except EmvError:
        return None  # the TLV is broken; that is reported separately
    chip = tags.get("9F02")
    if chip is None or not chip.isdigit() or not de4.isdigit():
        return None
    if int(chip) == int(de4):
        return None
    return (
        f"chip tag 9F02 authorised {int(chip):,} minor units but DE 4 asks for "
        f"{int(de4):,} - the cardholder would be debited an amount the chip never approved"
    )


def currency_mismatch(de55: str, de49: str | None) -> str | None:
    if de49 is None:
        return None
    try:
        tags = flatten(parse(de55))
    except EmvError:
        return None
    chip = tags.get("5F2A")
    if chip is None:
        return None
    if chip.lstrip("0") == de49.lstrip("0"):
        return None
    return f"chip tag 5F2A says currency {chip} but DE 49 says {de49}"


def missing_tags(de55: str) -> list[str]:
    try:
        present = flatten(parse(de55))
    except EmvError:
        return []
    return [tag for tag in REQUIRED_FOR_AUTHORISATION if tag not in present]


def build(values: dict[str, str]) -> str:
    """Build a DE 55 hex string from ``{tag: hex value}``. Used by the scenarios."""
    out = []
    for tag, value in values.items():
        body = value.upper()
        if len(body) % 2:
            raise EmvError(f"tag {tag} value {value!r} is not whole bytes")
        length = len(body) // 2
        if length > 0x7F:
            raise EmvError(f"tag {tag} is {length} bytes; this builder keeps lengths short")
        out.append(f"{tag}{length:02X}{body}")
    return "".join(out)
