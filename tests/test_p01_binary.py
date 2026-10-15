"""Binary framing, framing detection, the EMV TLV parser and the chip cross-field rules."""

from __future__ import annotations

import pytest

from projects.p01_switchboard import emv, lifecycle, mutations, scenarios
from projects.p01_switchboard.binary import (
    ASCII,
    BINARY,
    BINARY_NO_HEADER,
    Framing,
    build_bitmap_bytes,
    detect,
    pack_binary,
    parse_any,
    parse_bitmap_bytes,
    round_trip_binary,
    unpack_binary,
)
from projects.p01_switchboard.codec import CodecError, Message, luhn_ok, pack


def sample() -> Message:
    return scenarios.build("atm_withdrawal_approved").steps[0].message


# ------------------------------------------------------------- binary framing


def test_every_generated_message_survives_binary_framing() -> None:
    for flow in scenarios.build_all():
        for message in flow.messages:
            data = pack_binary(message)
            back = unpack_binary(data)
            assert back.mti == message.mti, flow.key
            assert back.fields == message.fields, f"{flow.key} {message.mti}"
            assert round_trip_binary(message)


def test_binary_is_shorter_than_ascii_because_numbers_are_packed() -> None:
    message = sample()
    assert len(pack_binary(message, Framing(header=False))) < len(pack(message))


def test_the_length_header_is_the_byte_count_that_follows() -> None:
    data = pack_binary(sample())
    assert int.from_bytes(data[:2], "big") == len(data) - 2


def test_a_wrong_length_header_is_refused_and_says_which_way() -> None:
    data = pack_binary(sample())
    short = (len(data) - 2 + 5).to_bytes(2, "big") + data[2:]
    with pytest.raises(CodecError, match="truncated"):
        unpack_binary(short)
    long = (len(data) - 2 - 5).to_bytes(2, "big") + data[2:]
    with pytest.raises(CodecError, match="over-long"):
        unpack_binary(long)


def test_a_message_without_a_header_parses_when_told_so() -> None:
    message = sample()
    bare = pack_binary(message, Framing(header=False))
    assert unpack_binary(bare, Framing(header=False)).fields == message.fields
    with pytest.raises(CodecError):
        unpack_binary(bare, Framing(header=True))


def test_odd_length_bcd_keeps_its_pad_nibble() -> None:
    # A 19-digit PAN does not fit whole bytes. Dropping the F pad would hand back an
    # 18-digit card number that still looks plausible.
    message = Message(
        "0200",
        {2: "4" * 18 + "1", 3: "011000", 4: "000000500000", 7: "1015093012", 11: "004711"},
    )
    data = pack_binary(message)
    assert unpack_binary(data).fields[2] == "4" * 18 + "1"


def test_a_corrupted_pad_nibble_is_refused() -> None:
    message = Message("0200", {2: "4" * 19, 3: "011000", 4: "1", 7: "1015093012", 11: "004711"})
    data = bytearray(pack_binary(message))
    # The PAN's last byte holds one digit plus the F pad; break the pad.
    position = data.index(bytes.fromhex("4F"))
    data[position] = 0x41
    with pytest.raises(CodecError, match="padding nibble"):
        unpack_binary(bytes(data))


def test_a_non_decimal_nibble_in_a_packed_field_is_refused() -> None:
    data = bytearray(pack_binary(sample()))
    data[2] = 0xAB  # the MTI is BCD; AB is not two decimal digits
    with pytest.raises(CodecError):
        unpack_binary(bytes(data))


def test_binary_bitmaps_round_trip() -> None:
    for present in ([2, 3, 4], [2, 102], list(range(2, 64)), [2, 128]):
        data = build_bitmap_bytes(present)
        assert parse_bitmap_bytes(data)[0] == present
        assert len(data) == (16 if max(present) > 64 else 8)


def test_a_missing_secondary_binary_bitmap_is_reported() -> None:
    data = build_bitmap_bytes([2, 102])[:8]
    with pytest.raises(CodecError, match="secondary bitmap must follow"):
        parse_bitmap_bytes(data)


def test_trailing_bytes_are_refused() -> None:
    with pytest.raises(CodecError, match="trailing bytes"):
        unpack_binary(pack_binary(sample(), Framing(header=False)) + b"\x00", Framing(header=False))


# ---------------------------------------------------------------- detection


def test_each_framing_is_told_apart() -> None:
    message = sample()
    cases = [
        (pack(message), ASCII),
        (pack_binary(message).hex(), BINARY),
        (pack_binary(message, Framing(header=False)).hex(), BINARY_NO_HEADER),
    ]
    for raw, expected in cases:
        assert detect(raw)["framing"] == expected, expected
        parsed, framing = parse_any(raw)
        assert framing == expected
        assert parsed.fields == message.fields


def test_detection_does_not_strip_the_spaces_inside_an_ascii_message() -> None:
    # Text elements are space-padded. Removing all whitespace to normalise a hex dump
    # also shortens those fields, and the message then fails to parse for a reason the
    # pasted text did not contain.
    message = sample()
    raw = pack(message)
    assert "  " in raw, "this message must contain padding for the test to mean anything"
    assert detect(raw)["framing"] == ASCII
    assert parse_any(raw)[0].fields == message.fields


def test_a_spaced_hex_dump_is_read() -> None:
    data = pack_binary(sample())
    spaced = " ".join(data.hex().upper()[i : i + 2] for i in range(0, len(data.hex()), 2))
    assert parse_any(spaced)[0].fields == sample().fields


def test_detection_explains_itself_when_nothing_matches() -> None:
    verdict = detect("zzzz")
    assert verdict["framing"] is None
    assert any("no framing matched" in str(reason) for reason in verdict["reasons"])


def test_detection_names_a_length_mismatch() -> None:
    truncated = pack_binary(sample())[:40].hex()
    reasons = " ".join(str(reason) for reason in detect(truncated)["reasons"])
    assert "length header" in reasons


def test_an_odd_number_of_hex_characters_is_called_out() -> None:
    reasons = " ".join(str(reason) for reason in detect("00AA0200F")["reasons"])
    assert "odd number" in reasons


def test_parse_any_refuses_rather_than_guessing() -> None:
    with pytest.raises(CodecError, match="framing could not be determined"):
        parse_any("not a message at all")


# ---------------------------------------------------------------------- Luhn


@pytest.mark.parametrize(
    "pan", ["4111111111111111", "4000000000000002", "5105105105105100", "378282246310005"]
)
def test_real_test_card_numbers_pass_luhn(pan: str) -> None:
    assert luhn_ok(pan)


@pytest.mark.parametrize("pan", ["4111111111111112", "5555444433332222", "1234", "", "411111a1111"])
def test_invalid_card_numbers_fail_luhn(pan: str) -> None:
    assert not luhn_ok(pan)


def test_every_scenario_uses_a_card_number_that_could_exist() -> None:
    for flow in scenarios.build_all():
        for message in flow.messages:
            pan = message.get(2)
            if pan is not None:
                assert luhn_ok(pan), f"{flow.key} carries an impossible PAN"


# ----------------------------------------------------------------- EMV TLV


def chip() -> str:
    return sample().fields[55]


def test_the_chip_data_in_the_scenarios_decodes() -> None:
    tags = emv.parse(chip())
    assert len(tags) >= 10
    assert all(tag.known for tag in tags)
    assert emv.missing_tags(chip()) == []


def test_tags_agree_with_the_message_they_travel_in() -> None:
    message = sample()
    assert emv.amount_mismatch(message.fields[55], message.fields[4]) is None
    assert emv.currency_mismatch(message.fields[55], message.fields[49]) is None


def test_a_chip_amount_that_disagrees_with_de4_is_named() -> None:
    message = sample()
    problem = emv.amount_mismatch(message.fields[55], "000000999999")
    assert problem is not None
    assert "never approved" in problem


def test_a_multi_byte_tag_is_read_whole() -> None:
    tags = emv.parse("9F10" + "02" + "ABCD")
    assert [tag.tag for tag in tags] == ["9F10"]
    assert tags[0].value == "ABCD"


def test_a_multi_byte_length_is_read() -> None:
    tags = emv.parse("9F10" + "81" + "20" + "00" * 32)
    assert tags[0].length == 32


def test_an_unknown_tag_is_kept_and_flagged_rather_than_skipped() -> None:
    # Silently dropping what is not recognised is how a mandatory tag goes missing.
    tags = emv.parse("DF8101" + "02" + "ABCD")
    assert len(tags) == 1
    assert tags[0].known is False
    assert tags[0].value == "ABCD"


def test_a_constructed_tag_exposes_its_children() -> None:
    inner = emv.build({"9F02": "000000000100"})
    outer = "E1" + f"{len(inner) // 2:02X}" + inner
    tags = emv.parse(outer)
    assert tags[0].tag == "E1"
    assert [child.tag for child in tags[0].children] == ["9F02"]
    assert emv.flatten(tags)["9F02"] == "000000000100"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("9F02", "no length byte"),
        ("9F0205" + "0000", "only"),
        # 0x9F has the low nibble set, so the tag continues into a byte that is not there.
        ("9F", "unterminated"),
        ("9F10" + "80", "indefinite length"),
        ("ABC", "whole bytes"),
        ("ZZZZ", "not hexadecimal"),
    ],
)
def test_broken_tlv_says_where_it_stopped(value: str, message: str) -> None:
    with pytest.raises(emv.EmvError, match=message):
        emv.parse(value)


def test_an_empty_de55_is_not_an_error() -> None:
    assert emv.parse("") == []


def test_tag_meanings_are_given_where_known_and_withheld_where_not() -> None:
    assert "ARQC" in emv.meaning("9F27", "80")
    assert emv.meaning("9F27", "FF").startswith("unknown")
    assert emv.meaning("9A", "261015") == "dated 2026-10-15"
    assert emv.meaning("DF01", "00") == ""


# ------------------------------------------------- the chip and PAN invariants


def test_the_new_rules_are_declared_and_exercised() -> None:
    declared = {rule.rule for rule in lifecycle.RULES}
    assert {"L19", "L20", "L21", "L22"} <= declared
    assert len(declared) == 22
    report = mutations.run()
    assert report["rules_never_exercised"] == []


def test_a_bad_check_digit_is_caught_on_a_response_too() -> None:
    flow = scenarios.build("atm_withdrawal_approved")
    flow.steps[1].message = flow.steps[1].message.replace(de2="4111111111111112")
    rules = {finding.rule for finding in lifecycle.check(flow)}
    assert "L19" in rules


def test_a_rewritten_amount_is_caught_only_by_the_cross_field_rule() -> None:
    # Every field is individually well-formed; the message packs and parses cleanly.
    flow = scenarios.build("atm_withdrawal_approved")
    original = flow.steps[0].message
    flow.steps[0].message = original.replace(de4="000001000000")
    from projects.p01_switchboard.codec import check_values

    assert check_values(flow.steps[0].message.fields) == []
    rules = {finding.rule for finding in lifecycle.check(flow)}
    assert "L20" in rules


def test_broken_chip_data_is_reported_without_crashing_the_check() -> None:
    flow = scenarios.build("atm_withdrawal_approved")
    flow.steps[0].message = flow.steps[0].message.replace(de55="9F0205")
    findings = lifecycle.check(flow)
    assert "L22" in {finding.rule for finding in findings}


def test_a_magstripe_fallback_carries_no_chip_data_and_is_not_faulted_for_it() -> None:
    flow = scenarios.build("pos_chip_fallback_declined")
    assert flow.steps[0].message.get(55) is None
    assert lifecycle.check(flow) == []


# --------------------------------------------------------- binary fuzzing


def test_binary_framing_rejects_most_single_byte_damage() -> None:
    result = mutations.fuzz_binary(trials=400, seed=5)
    assert result["trials"] == 400
    assert result["rejection_rate"] > 0.5
    assert result["caught_by_the_length_header"] > 0


def test_binary_fuzz_is_deterministic_for_a_seed() -> None:
    assert mutations.fuzz_binary(trials=150, seed=2) == mutations.fuzz_binary(trials=150, seed=2)


def test_the_character_fuzz_covers_several_scenarios() -> None:
    result = mutations.fuzz(trials=300, seed=4)
    assert result["messages_in_pool"] > 1
    assert len(result["scenarios"]) >= 3
    assert result["illegal_characters_accepted"] == []
