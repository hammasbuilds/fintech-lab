"""Read and write the binary-framed ISO 8583 that real switches actually put on the wire.

The ASCII form in :mod:`codec` is what test tooling and specifications use. A log pulled
off a live switch is almost never that. It is:

* a **binary bitmap** - 8 raw bytes, not 16 hex characters;
* **BCD-packed numeric fields** - two digits per byte, so a 12-digit amount is 6 bytes;
* **BCD length prefixes** on variable fields - one byte for LLVAR, two for LLLVAR;
* usually behind a **2-byte big-endian length header**, because the transport is a TCP
  stream and the reader has to know where each message ends.

So a workbench that only reads the ASCII form cannot read the thing an engineer has in
front of them. This module converts in both directions and shares one ``Message`` type
with the ASCII codec, so every invariant, narration and fault check already written
applies to binary traffic unchanged.

Odd-length BCD is the part that bites. A 19-digit PAN does not fit a whole number of
bytes, so it is padded with an F nibble - and a reader that drops the pad silently turns
a 19-digit card number into an 18-digit one. The padding is tracked per field from the
dictionary rather than guessed from the data.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import fields as F
from .codec import CodecError, Message, check_values, describe_mti, normalise

LENGTH_HEADER_BYTES = 2
MAX_MESSAGE_BYTES = 8192

#: Character classes carried as packed BCD on the wire. Text classes stay as ASCII bytes
#: even in a binary-framed message, which is why the two are distinguished here.
BCD_CLASSES = {"n", "z"}


@dataclass(frozen=True)
class Framing:
    """How a binary message is wrapped. ``header`` is the 2-byte length prefix."""

    header: bool = True
    name: str = "binary"


def _bcd_encode(digits: str, field_name: str) -> bytes:
    if not digits.isdigit():
        raise CodecError(f"{field_name}: {digits!r} is not numeric, so it cannot be BCD-packed")
    padded = digits if len(digits) % 2 == 0 else digits + "F"
    try:
        return bytes.fromhex(padded)
    except ValueError as exc:  # pragma: no cover - the isdigit guard precedes this
        raise CodecError(f"{field_name}: cannot pack {digits!r} - {exc}") from exc


def _bcd_decode(data: bytes, digit_count: int, field_name: str) -> str:
    text = data.hex().upper()
    if len(text) < digit_count:
        raise CodecError(
            f"{field_name}: needed {digit_count} BCD digits but only {len(text)} are present"
        )
    taken = text[:digit_count]
    pad = text[digit_count:]
    if pad and set(pad) != {"F"}:
        raise CodecError(
            f"{field_name}: {digit_count} digits were read but the padding nibble is "
            f"{pad!r}, which is not an F pad - the length is wrong"
        )
    if not taken.isdigit():
        raise CodecError(f"{field_name}: BCD digits {taken!r} contain a non-decimal nibble")
    return taken


def _bcd_length(digit_count: int) -> int:
    return (digit_count + 1) // 2


def build_bitmap_bytes(present: list[int]) -> bytes:
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
    return int("".join(bits), 2).to_bytes(width // 8, "big")


def parse_bitmap_bytes(data: bytes, offset: int = 0) -> tuple[list[int], int]:
    primary = data[offset : offset + 8]
    if len(primary) < 8:
        raise CodecError(
            f"message ends inside the primary bitmap at byte {offset}: needed 8 bytes, "
            f"found {len(primary)}"
        )
    bits = "".join(format(byte, "08b") for byte in primary)
    cursor = offset + 8
    if bits[0] == "1":
        extra = data[cursor : cursor + 8]
        if len(extra) < 8:
            raise CodecError(
                f"bitmap bit 1 is set so a secondary bitmap must follow at byte {cursor}, "
                f"but only {len(extra)} bytes remain"
            )
        bits += "".join(format(byte, "08b") for byte in extra)
        cursor += 8
    present = [index + 1 for index, bit in enumerate(bits) if bit == "1" and index != 0]
    return present, cursor


def pack_binary(message: Message, framing: Framing | None = None) -> bytes:
    """Serialise ``message`` in binary framing, with the length header by default."""
    wrap = framing or Framing()
    describe_mti(message.mti)
    problems = check_values(message.fields)
    if problems:
        raise CodecError("; ".join(problems))

    body = bytearray()
    body += _bcd_encode(message.mti, "MTI")
    body += build_bitmap_bytes(message.present)
    for de in message.present:
        spec = F.get(de)
        value = normalise(de, message.fields[de])
        name = f"DE {de} ({spec.name})"
        if spec.var == F.FIXED:
            body += _encode_value(spec, value, name)
            continue
        if spec.var == F.LLVAR:
            body += _bcd_encode(str(len(value)).zfill(2), f"{name} length")
        else:
            body += _bcd_encode(str(len(value)).zfill(4), f"{name} length")
        body += _encode_value(spec, value, name)

    if len(body) > MAX_MESSAGE_BYTES:
        raise CodecError(f"message is {len(body)} bytes, above the {MAX_MESSAGE_BYTES} limit")
    if not wrap.header:
        return bytes(body)
    return len(body).to_bytes(LENGTH_HEADER_BYTES, "big") + bytes(body)


def _encode_value(spec: F.Field, value: str, name: str) -> bytes:
    if spec.cls in BCD_CLASSES:
        return _bcd_encode(value.replace("=", "D") if spec.cls == "z" else value, name)
    if spec.cls == "b":
        try:
            return bytes.fromhex(value)
        except ValueError as exc:
            raise CodecError(f"{name}: {value!r} is not hexadecimal - {exc}") from exc
    if spec.cls == "x+n":
        return value[:1].encode("ascii") + _bcd_encode(value[1:], name)
    return value.encode("ascii", errors="strict")


def unpack_binary(data: bytes, framing: Framing | None = None) -> Message:
    """Parse binary-framed ``data``. The length header is consumed when present."""
    wrap = framing or Framing()
    body = data
    if wrap.header:
        if len(data) < LENGTH_HEADER_BYTES:
            raise CodecError(
                f"a length header needs {LENGTH_HEADER_BYTES} bytes, found {len(data)}"
            )
        declared = int.from_bytes(data[:LENGTH_HEADER_BYTES], "big")
        body = data[LENGTH_HEADER_BYTES:]
        if declared != len(body):
            raise CodecError(
                f"the length header says {declared} bytes but {len(body)} follow it - "
                f"the message is {'truncated' if declared > len(body) else 'over-long'}"
            )
    if len(body) < 2 + 8:
        raise CodecError(f"message is {len(body)} bytes; the MTI and primary bitmap alone need 10")

    mti = _bcd_decode(body[:2], 4, "MTI")
    describe_mti(mti)
    present, cursor = parse_bitmap_bytes(body, 2)

    values: dict[int, str] = {}
    for de in present:
        try:
            spec = F.get(de)
        except KeyError:
            raise CodecError(
                f"bitmap sets DE {de} but it is not in the dictionary, so the remaining "
                f"{len(body) - cursor} bytes cannot be read"
            ) from None
        name = f"DE {de} ({spec.name})"
        if spec.var == F.FIXED:
            digit_count = spec.length
            width = _byte_width(spec, digit_count)
            chunk = body[cursor : cursor + width]
            if len(chunk) < width:
                raise CodecError(
                    f"{name} needs {width} bytes at byte {cursor}, but only {len(chunk)} remain"
                )
            values[de] = _decode_value(spec, chunk, digit_count, name)
            cursor += width
            continue

        prefix_digits = 2 if spec.var == F.LLVAR else 4
        prefix_width = _bcd_length(prefix_digits)
        prefix = body[cursor : cursor + prefix_width]
        if len(prefix) < prefix_width:
            raise CodecError(
                f"{name} needs a {prefix_width}-byte BCD length at byte {cursor}, found "
                f"{len(prefix)}"
            )
        declared = int(_bcd_decode(prefix, prefix_digits, f"{name} length"))
        if declared > spec.length:
            raise CodecError(
                f"{name} declares length {declared} at byte {cursor}, above its maximum "
                f"of {spec.length}"
            )
        cursor += prefix_width
        width = _byte_width(spec, declared)
        chunk = body[cursor : cursor + width]
        if len(chunk) < width:
            raise CodecError(
                f"{name} declares {declared} characters ({width} bytes) at byte {cursor}, "
                f"but only {len(chunk)} remain"
            )
        values[de] = _decode_value(spec, chunk, declared, name)
        cursor += width

    if cursor != len(body):
        last = present[-1] if present else "none"
        raise CodecError(
            f"{len(body) - cursor} trailing bytes after the last data element (DE {last}) "
            f"at byte {cursor}: {body[cursor : cursor + 12].hex().upper()}"
        )
    problems = check_values(values)
    if problems:
        raise CodecError("; ".join(problems))
    return Message(mti=mti, fields=values)


def _byte_width(spec: F.Field, character_count: int) -> int:
    if spec.cls in BCD_CLASSES:
        return _bcd_length(character_count)
    if spec.cls == "b":
        return _bcd_length(character_count)
    if spec.cls == "x+n":
        return 1 + _bcd_length(character_count - 1)
    return character_count


def _decode_value(spec: F.Field, chunk: bytes, character_count: int, name: str) -> str:
    if spec.cls in BCD_CLASSES:
        digits = _bcd_decode(chunk, character_count, name)
        return digits.replace("D", "=") if spec.cls == "z" else digits
    if spec.cls == "b":
        return chunk.hex().upper()[:character_count]
    if spec.cls == "x+n":
        return chunk[:1].decode("ascii") + _bcd_decode(chunk[1:], character_count - 1, name)
    try:
        return chunk.decode("ascii")
    except UnicodeDecodeError as exc:
        raise CodecError(
            f"{name}: bytes {chunk.hex().upper()} are not ASCII text, so this field is "
            f"not where the bitmap says it is - {exc}"
        ) from None


# ------------------------------------------------------------------- detection

ASCII = "ascii"
BINARY = "binary"
BINARY_NO_HEADER = "binary-no-header"


def detect(raw: str) -> dict[str, object]:
    """Guess the framing of a pasted dump, and say why rather than just asserting it.

    A workbench that silently picks a framing and then reports a parse error has blamed
    the message for its own guess, so the reasoning is returned alongside the answer and
    shown in the UI.
    """
    # Two different cleanings, and conflating them is a bug this function had: a hex dump
    # can have every space removed, but an ASCII-framed message cannot. Its text fields
    # are space-padded, so stripping internal spaces shortens real field values and the
    # message then fails to parse for a reason the pasted text did not contain.
    hex_text = "".join(raw.split())
    ascii_text = raw.replace("\r", "").replace("\n", "").replace("\t", "").lstrip()
    reasons: list[str] = []
    if not hex_text:
        return {"framing": None, "reasons": ["nothing to look at"], "candidates": []}

    candidates: list[str] = []
    text = hex_text
    if len(ascii_text) >= 4 and ascii_text[:4].isdigit():
        head = ascii_text[:4]
        if head[0] in "012" and head[1] in "12345678":
            reasons.append(f"starts with {head}, which is a plausible ASCII MTI")
            candidates.append(ASCII)

    hex_only = all(ch in "0123456789abcdefABCDEF" for ch in text)
    if hex_only and len(text) % 2:
        reasons.append(
            f"the dump is {len(text)} hex characters, an odd number, so it cannot be "
            f"whole bytes - a character is missing or one too many was pasted"
        )
    if hex_only and len(text) % 2 == 0:
        data = bytes.fromhex(text)
        if len(data) > LENGTH_HEADER_BYTES:
            declared = int.from_bytes(data[:LENGTH_HEADER_BYTES], "big")
            if declared == len(data) - LENGTH_HEADER_BYTES:
                reasons.append(
                    f"the first two bytes read {declared}, which is exactly the number "
                    f"of bytes that follow - that is a length header"
                )
                candidates.insert(0, BINARY)
            else:
                following = len(data) - LENGTH_HEADER_BYTES
                shape = "truncated" if declared > following else "longer than one message"
                reasons.append(
                    f"read as a length header the first two bytes say {declared} bytes, "
                    f"but {following} follow - so either this is not a length header, or "
                    f"the dump is {shape}"
                )
        if len(data) >= 2:
            mti = data[:2].hex().upper()
            if mti.isdigit() and mti[0] in "012":
                reasons.append(f"bytes 1-2 are BCD {mti}, a plausible packed MTI")
                if BINARY not in candidates:
                    candidates.append(BINARY_NO_HEADER)

    # Try each candidate in order and keep the first that actually parses. A guess that
    # is never tested against the parser is just a guess.
    if not candidates:
        reasons.append(
            "no framing matched: an ASCII message starts with four digits forming a "
            "message type indicator, and a binary dump is an even number of hex "
            "characters whose first two bytes are either a length or a packed MTI"
        )
    for candidate in candidates:
        try:
            if candidate == ASCII:
                from .codec import unpack

                unpack(ascii_text)
            else:
                unpack_binary(bytes.fromhex(text), Framing(header=candidate == BINARY))
        except CodecError as exc:
            reasons.append(f"{candidate} was tried and rejected: {exc}")
            continue
        reasons.append(f"{candidate} parses cleanly")
        return {"framing": candidate, "reasons": reasons, "candidates": candidates}
    return {"framing": None, "reasons": reasons or ["no framing matched"], "candidates": candidates}


def parse_any(raw: str) -> tuple[Message, str]:
    """Parse a pasted dump in whatever framing it turns out to be."""
    verdict = detect(raw)
    framing = verdict["framing"]
    if framing == ASCII:
        from .codec import unpack

        return unpack(raw), ASCII
    if framing in {BINARY, BINARY_NO_HEADER}:
        return (
            unpack_binary(bytes.fromhex("".join(raw.split())), Framing(header=framing == BINARY)),
            str(framing),
        )
    raise CodecError(
        "the framing could not be determined. "
        + " ".join(str(reason) for reason in verdict["reasons"])
    )


def round_trip_binary(message: Message, framing: Framing | None = None) -> bool:
    wrap = framing or Framing()
    once = pack_binary(message, wrap)
    return pack_binary(unpack_binary(once, wrap), wrap) == once
