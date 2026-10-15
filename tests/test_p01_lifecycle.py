"""The invariant checker, the scenarios it runs on, and the fault injector that grades it."""

from __future__ import annotations

import pytest

from projects.p01_switchboard import lifecycle, mutations, narrate, scenarios
from projects.p01_switchboard.codec import Message, pack, unpack
from projects.p01_switchboard.scenarios import ACQ_TO_ISS, ISS_TO_ACQ, Context, Flow, Step

# ------------------------------------------------------------------ scenarios


def test_every_scenario_builds_and_is_clean() -> None:
    # A finding on valid traffic is a false positive, and a checker with false positives
    # gets switched off.
    for flow in scenarios.build_all():
        findings = lifecycle.check(flow)
        assert findings == [], f"{flow.key}: {[f.detail for f in findings]}"


def test_scenarios_cover_both_directions_and_several_classes() -> None:
    flows = scenarios.build_all()
    assert len(flows) == 14
    classes = {step.message.mti[1] for flow in flows for step in flow.steps}
    assert {"2", "4", "8"} <= classes
    directions = {step.direction for flow in flows for step in flow.steps}
    assert directions == {ACQ_TO_ISS, ISS_TO_ACQ}


def test_an_unknown_scenario_lists_the_known_ones() -> None:
    with pytest.raises(KeyError, match="atm_withdrawal_approved"):
        scenarios.build("not_a_scenario")


def test_each_scenario_gets_its_own_trace_numbers() -> None:
    for flow in scenarios.build_all():
        requests = [s.message for s in flow.steps if s.direction == ACQ_TO_ISS]
        traces = [m.get(11) for m in requests]
        assert len(traces) == len(set(traces)), flow.key


def test_a_negative_amount_is_refused_at_the_source() -> None:
    with pytest.raises(ValueError, match="unsigned"):
        scenarios.amount_field(-1)


def test_the_reversal_names_the_request_it_reverses() -> None:
    flow = scenarios.build("atm_reversal_after_timeout")
    request, reversal = flow.steps[0].message, flow.steps[1].message
    parts = narrate.decode_original_data_elements(reversal.fields[90])
    assert parts["original_mti"] == request.mti
    assert parts["original_stan"] == request.fields[11]


def test_partial_approval_is_the_only_changed_amount() -> None:
    flow = scenarios.build("atm_partial_approval")
    request, response = flow.steps[0].message, flow.steps[1].message
    assert response.fields[39] == "10"
    assert int(response.fields[4]) < int(request.fields[4])
    assert int(response.fields[4]) > 0


# ----------------------------------------------------------------- invariants


def paired(request: Message, response: Message) -> Flow:
    return Flow(
        "t",
        "t",
        "t",
        [
            Step(ACQ_TO_ISS, "request", request, raw=pack(request)),
            Step(ISS_TO_ACQ, "response", response, raw=pack(response)),
        ],
    )


def base_pair() -> tuple[Message, Message]:
    flow = scenarios.build("atm_withdrawal_approved")
    return flow.steps[0].message, flow.steps[1].message


def test_rules_are_uniquely_identified_and_described() -> None:
    ids = [rule.rule for rule in lifecycle.RULES]
    assert len(ids) == len(set(ids)) == 22
    for rule in lifecycle.RULES:
        assert rule.severity in {"critical", "serious", "warning"}
        assert rule.why.strip().endswith(".")


def test_an_unanswered_request_is_reported() -> None:
    request, _ = base_pair()
    flow = Flow("t", "t", "t", [Step(ACQ_TO_ISS, "r", request, raw=pack(request))])
    assert {f.rule for f in lifecycle.check(flow)} == {"L01"}


def test_an_unanswered_request_is_fine_when_it_was_reversed() -> None:
    # The rule is "answered or reversed". A timeout followed by a reversal is correct,
    # and a checker that insists on a response would flag every timeout.
    flow = scenarios.build("atm_reversal_after_timeout")
    assert lifecycle.check(flow) == []
    assert not any(step.message.mti == "0210" for step in flow.steps)


def test_a_changed_response_amount_is_reported_but_a_partial_approval_is_not() -> None:
    request, response = base_pair()
    assert {
        f.rule for f in lifecycle.check(paired(request, response.replace(de4="000000000001")))
    } == {"L04"}
    partial = response.replace(de4="000000000001", de39="10")
    assert "L04" not in {f.rule for f in lifecycle.check(paired(request, partial))}


def test_a_partial_approval_for_the_full_amount_is_reported() -> None:
    request, response = base_pair()
    full = response.replace(de39="10")
    assert "L05" in {f.rule for f in lifecycle.check(paired(request, full))}


def test_declines_must_not_carry_an_authorisation_id() -> None:
    request, response = base_pair()
    declined = response.replace(de39="51", de38="A00001")
    assert "L12" in {f.rule for f in lifecycle.check(paired(request, declined))}


def test_a_reversal_or_network_approval_needs_no_authorisation_id() -> None:
    # DE 38 is an authorisation handle, so only classes 1 and 2 are expected to carry
    # one. The first run of this checker over its own valid flows found that out.
    for key in ("atm_reversal_after_timeout", "network_echo_test", "network_sign_on"):
        flow = scenarios.build(key)
        assert "L12" not in {f.rule for f in lifecycle.check(flow)}, key


def test_an_inquiry_must_request_zero() -> None:
    flow = scenarios.build("balance_inquiry")
    flow.steps[0].message = flow.steps[0].message.replace(de4="000000010000")
    assert "L17" in {f.rule for f in lifecycle.check(flow)}


def test_a_refund_must_not_carry_a_pin_block() -> None:
    flow = scenarios.build("pos_refund")
    flow.steps[0].message = flow.steps[0].message.replace(de52="A1B2C3D4E5F60718")
    assert "L16" in {f.rule for f in lifecycle.check(flow)}


def test_findings_are_sorted_worst_first() -> None:
    request, response = base_pair()
    broken = response.replace(de39=None, de41="ATM99999")
    findings = lifecycle.check(paired(request, broken))
    severities = [f.severity for f in findings]
    order = {"critical": 0, "serious": 1, "warning": 2}
    assert severities == sorted(severities, key=lambda level: order[level])


def test_findings_are_deduplicated() -> None:
    request, response = base_pair()
    flow = paired(request, response)
    flow.steps.append(Step(ISS_TO_ACQ, "again", response, raw=pack(response)))
    findings = lifecycle.check(flow)
    assert len({(f.rule, f.detail) for f in findings}) == len(findings)


def test_is_clean_agrees_with_check() -> None:
    flow = scenarios.build("pos_purchase_approved")
    assert lifecycle.is_clean(flow) is True


# ------------------------------------------------------------------- mutation


def test_every_mutant_is_killed_by_the_rule_written_for_it() -> None:
    report = mutations.run()
    assert report["survived"] == []
    assert report["killed"] == report["total"]
    assert report["killed_by_intended_rule"] == report["total"]


def test_the_mutant_catalogue_exercises_every_rule() -> None:
    # A rule nothing exercises is a rule nobody has shown to work.
    report = mutations.run()
    assert report["rules_never_exercised"] == []


def test_mutants_are_actually_different_from_the_valid_flow() -> None:
    for mutant in mutations.generate():
        if mutant.layer == mutations.CODEC:
            original = scenarios.build(mutant.scenario, Context()).steps[0].raw
            assert mutant.raw != original, mutant.mutator
        else:
            assert mutant.flow is not None
            valid = scenarios.build(mutant.scenario, Context())
            mutated = [(s.message.mti, s.message.fields) for s in mutant.flow.steps]
            baseline = [(s.message.mti, s.message.fields) for s in valid.steps]
            assert mutated != baseline, mutant.mutator


def test_the_detector_reports_what_caught_each_fault() -> None:
    for mutant in mutations.generate():
        detection = mutations.detect(mutant)
        assert detection.killed
        assert detection.by
        assert detection.message.strip()


def test_random_corruption_never_lets_an_illegal_character_through() -> None:
    result = mutations.fuzz(trials=600, seed=11)
    violating = result["groups"]["class_violating"]
    assert violating["trials"] > 50
    assert violating["rejected"] == violating["trials"]
    assert result["illegal_characters_accepted"] == []


def test_random_corruption_accepts_characters_the_field_allows() -> None:
    # A digit swapped for another digit is a different but valid message. A parser that
    # rejected those would be wrong, so this rate is expected to be near zero.
    result = mutations.fuzz(trials=600, seed=11)
    legal = result["groups"]["class_legal"]
    assert legal["trials"] > 50
    assert legal["rejected"] / legal["trials"] < 0.02


def test_fuzz_is_deterministic_for_a_seed() -> None:
    assert mutations.fuzz(trials=200, seed=3) == mutations.fuzz(trials=200, seed=3)


# ------------------------------------------------------------------ narration


def test_narration_names_the_transaction_and_the_outcome() -> None:
    flow = scenarios.build("atm_insufficient_funds")
    text = narrate.narrate(flow.steps[1].message)
    assert "cash withdrawal" in text
    assert "insufficient funds" in text
    assert "5,000.00 PKR" in text


def test_amounts_use_the_currency_exponent() -> None:
    assert narrate.format_amount("000000500000", "586") == "5,000.00 PKR"
    assert narrate.format_amount("000000500000", "392") == "500,000 JPY"
    assert "not a numeric amount" in narrate.format_amount("12X4", "586")


def test_the_account_number_is_masked_wherever_it_is_shown() -> None:
    flow = scenarios.build("atm_withdrawal_approved")
    rows = narrate.field_rows(flow.steps[0].message)
    pan = next(row for row in rows if row["de"] == "2")
    assert pan["value"] == "411111******1111"
    assert "4111111111111111" not in pan["value"]


def test_an_unknown_code_is_reported_as_unknown_not_guessed() -> None:
    assert "unknown" in narrate._meaning(39, "ZZ", "586")
    assert "unknown" in narrate.decode_processing_code("990000")["transaction"]


def test_severity_follows_the_response_code() -> None:
    flow = scenarios.build("atm_withdrawal_approved")
    assert narrate.severity(flow.steps[1].message) == "good"
    assert narrate.severity(flow.steps[0].message) == "neutral"
    declined = scenarios.build("pos_chip_fallback_declined")
    assert narrate.severity(declined.steps[1].message) == "serious"


def test_original_data_elements_split_into_their_five_parts() -> None:
    parts = narrate.decode_original_data_elements(
        "0200" + "004712" + "1015093012" + "0" * 11 + "0" * 11
    )
    assert parts["original_mti"] == "0200"
    assert parts["original_stan"] == "004712"
    assert "error" in narrate.decode_original_data_elements("short")


def test_pasted_traffic_can_be_validated_as_a_flow() -> None:
    flow = scenarios.build("atm_incorrect_pin_then_approved")
    raws = [step.raw for step in flow.steps]
    rebuilt = Flow(
        "pasted",
        "pasted",
        "",
        [
            Step(
                ACQ_TO_ISS if index % 2 == 0 else ISS_TO_ACQ,
                f"line {index}",
                unpack(raw),
                raw=raw,
            )
            for index, raw in enumerate(raws)
        ],
    )
    assert lifecycle.check(rebuilt) == []
