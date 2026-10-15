"""The ISO 8583 codec: framing, character classes, MTI discipline and round-tripping."""

from __future__ import annotations

import pytest

from projects.p01_switchboard import fields as F
from projects.p01_switchboard.codec import (
    CodecError,
    Message,
    build_bitmap,
    check_values,
    describe_mti,
    field_offsets,
    is_request,
    normalise,
    pack,
    parse_bitmap,
    permits,
    response_mti,
    round_trip,
    unpack,
)
from projects.p01_switchboard.scenarios import Context, build, build_all


def sample() -> Message:
    return Message(
        "0200",
        {
            2: "4111111111111111",
            3: "011000",
            4: "000000500000",
            7: "1015093012",
            11: "004711",
            41: "ATM00417",
            49: "586",
        },
    )


# ------------------------------------------------------------------ dictionary


def test_dictionary_entries_are_self_consistent() -> None:
    for de in F.known():
        field = F.get(de)
        assert 2 <= field.de <= 128
        assert field.cls in F.CLASSES
        assert field.length > 0
        assert field.var in {F.FIXED, F.LLVAR, F.LLLVAR}
        assert field.prefix_digits == {F.FIXED: 0, F.LLVAR: 2, F.LLLVAR: 3}[field.var]
        assert field.name.strip()


def test_unknown_element_names_itself_in_the_error() -> None:
    with pytest.raises(KeyError, match="data element 99 is not in the dictionary"):
        F.get(99)


# ----------------------------------------------------------------------- MTI


@pytest.mark.parametrize(
    ("mti", "expected_class"),
    [
        ("0200", "financial"),
        ("0100", "authorization"),
        ("0400", "reversal or chargeback"),
        ("0800", "network management"),
    ],
)
def test_mti_classes(mti: str, expected_class: str) -> None:
    assert describe_mti(mti)["class"] == expected_class


@pytest.mark.parametrize("mti", ["0900", "0250", "0270", "4200", "020", "02a0", ""])
def test_a_reserved_or_malformed_mti_is_refused(mti: str) -> None:
    # Tolerating a reserved position only moves the failure somewhere later.
    with pytest.raises(CodecError):
        describe_mti(mti)


def test_response_mti_increments_the_function_digit() -> None:
    assert response_mti("0200") == "0210"
    assert response_mti("0400") == "0410"
    assert response_mti("0800") == "0810"


def test_a_response_has_no_response() -> None:
    assert is_request("0200") is True
    assert is_request("0210") is False
    with pytest.raises(CodecError, match="not a request"):
        response_mti("0210")


# -------------------------------------------------------------------- bitmap


def test_bitmap_sets_bit_one_only_when_a_high_element_is_present() -> None:
    assert build_bitmap([2, 3, 4])[0] in "0123456789ABCDEF"
    assert parse_bitmap(build_bitmap([2, 3, 4]))[0] == [2, 3, 4]
    high = build_bitmap([2, 102])
    assert len(high) == 32
    assert parse_bitmap(high)[0] == [2, 102]


def test_callers_cannot_set_bit_one_themselves() -> None:
    with pytest.raises(CodecError, match="2..128"):
        build_bitmap([1, 2])


def test_a_secondary_bitmap_that_does_not_follow_is_reported() -> None:
    raw = "0200" + build_bitmap([2, 102])[:16]
    with pytest.raises(CodecError, match="secondary bitmap must follow"):
        parse_bitmap(raw, 4)


def test_a_non_hex_bitmap_is_reported() -> None:
    with pytest.raises(CodecError, match="not hexadecimal"):
        parse_bitmap("ZZZZZZZZZZZZZZZZ")


# ------------------------------------------------------------ pack and unpack


def test_round_trip_is_stable() -> None:
    message = sample()
    assert round_trip(message)
    assert unpack(pack(message)).fields == message.fields


def test_every_generated_message_round_trips() -> None:
    for flow in build_all():
        for message in flow.messages:
            assert round_trip(message), f"{flow.key} {message.mti}"


def test_variable_fields_carry_their_own_length() -> None:
    raw = pack(sample())
    # DE 2 is LLVAR: a two-digit length then the value.
    offset, length = field_offsets(raw)[2]
    assert raw[offset - 2 : offset] == "16"
    assert length == 16


def test_field_offsets_point_at_the_values() -> None:
    message = sample()
    raw = pack(message)
    spans = field_offsets(raw)
    assert set(spans) == set(message.present)
    for de, (offset, length) in spans.items():
        assert raw[offset : offset + length] == normalise(de, message.fields[de])


def test_trailing_characters_are_refused() -> None:
    with pytest.raises(CodecError, match="trailing characters"):
        unpack(pack(sample()) + "00")


def test_truncation_inside_a_field_is_refused() -> None:
    with pytest.raises(CodecError, match="remain"):
        unpack(pack(sample())[:-4])


def test_a_length_prefix_above_the_maximum_is_refused() -> None:
    raw = pack(sample())
    offset, _ = field_offsets(raw)[2]
    broken = raw[: offset - 2] + "99" + raw[offset:]
    with pytest.raises(CodecError):
        unpack(broken)


def test_letters_in_a_numeric_field_are_refused_on_the_way_in() -> None:
    # A parser that reads widths but not contents hands back an amount with letters in it.
    raw = pack(sample())
    offset, _ = field_offsets(raw)[4]
    broken = raw[:offset] + "X" + raw[offset + 1 :]
    with pytest.raises(CodecError, match="class n"):
        unpack(broken)


def test_a_message_ending_in_a_space_parses() -> None:
    # Text elements are space-filled, so when the highest present element is a text one
    # the message genuinely ends in spaces. Stripping them shortens it and makes the
    # final field report itself as truncated.
    message = Message(
        "0200",
        {
            2: "4111111111111111",
            3: "011000",
            4: "000000500000",
            7: "1015093012",
            11: "004711",
            41: "ATM1",
        },
    )
    raw = pack(message)
    assert raw.endswith(" "), "DE 41 is ans8, so this message must end in padding"
    assert unpack(raw).fields[41] == "ATM1    "
    assert round_trip(message)


def test_a_bare_newline_or_leading_space_is_tolerated() -> None:
    raw = pack(sample())
    assert unpack("  " + raw + "\n").fields == sample().fields
    assert unpack(raw[:20] + "\n" + raw[20:]).fields == sample().fields


# ------------------------------------------------------------- value checking


def test_check_values_names_the_element_and_the_rule() -> None:
    # A value longer than a fixed element cannot be padded into shape, so it is reported.
    # A *short* numeric one is not an error: callers pass plain values and the codec
    # zero-fills them, which is what test_padding_follows_the_character_class covers.
    problems = check_values({4: "1" * 13})
    assert len(problems) == 1
    assert "DE 4" in problems[0]
    assert "exactly 12" in problems[0]
    assert check_values({4: "123"}) == []


def test_an_overlong_variable_field_is_reported() -> None:
    problems = check_values({2: "4" * 25})
    assert problems and "at most 19" in problems[0]


def test_padding_follows_the_character_class() -> None:

    assert normalise(4, "500") == "000000000500"  # numeric: right justified, zero filled
    assert normalise(41, "ATM1") == "ATM1    "  # text: left justified, space filled
    assert normalise(2, "4111") == "4111"  # variable: never padded


@pytest.mark.parametrize(
    ("cls", "char", "allowed"),
    [
        ("n", "5", True),
        ("n", "X", False),
        ("an", " ", True),
        ("ans", ".", True),
        ("b", "F", True),
        ("b", "G", False),
        ("x+n", "C", True),
        ("x+n", "Z", False),
    ],
)
def test_permits_answers_per_character(cls: str, char: str, allowed: bool) -> None:
    assert permits(cls, char) is allowed


def test_pack_refuses_rather_than_emitting_a_bad_message() -> None:
    with pytest.raises(CodecError, match="DE 4"):
        pack(Message("0200", {4: "oops", 11: "000001"}))


# ------------------------------------------------------------------- messages


def test_replace_adds_and_removes_fields() -> None:
    message = sample()
    assert message.replace(de39="00").get(39) == "00"
    assert message.replace(de4=None).get(4) is None
    with pytest.raises(ValueError, match="expected de"):
        message.replace(amount="1")


def test_fields_are_kept_in_element_order() -> None:
    message = Message("0200", {49: "586", 2: "4111111111111111", 11: "000001"})
    assert message.present == [2, 11, 49]


def test_scenario_values_flow_through_to_the_message() -> None:
    flow = build("atm_withdrawal_approved", Context(pan="5105105105105100", amount=123456))
    request = flow.steps[0].message
    assert request.fields[2] == "5105105105105100"
    assert request.fields[4] == "000000123456"
