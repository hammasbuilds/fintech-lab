"""Amount parsing, intent resolution, the confirmation spine and the audit chain."""

from __future__ import annotations

import pytest

from projects.p03_guardian import coercion, confirm, nlu, numerals, testset
from projects.p03_guardian.ledger import Ledger

# ------------------------------------------------------------------- numerals


@pytest.mark.parametrize(("text", "expected"), testset.NUMERAL_CASES)
def test_every_amount_in_the_test_set_parses_exactly(text: str, expected: float) -> None:
    found = numerals.find_all(text)
    assert len(found) == 1, f"{text!r} produced {[a.text for a in found]}"
    assert found[0].value == expected


@pytest.mark.parametrize("text", testset.NO_AMOUNT_CASES)
def test_no_amount_is_invented_where_there_is_none(text: str) -> None:
    assert numerals.find_all(text) == []


def test_the_pair_that_sounds_alike_is_not_confused() -> None:
    # saat (7) and saath (60) are one letter apart and ten thousand rupees apart, which
    # is why the lexicon lists spellings instead of matching fuzzily.
    assert numerals.find_all("saat hazaar")[0].value == 7000
    assert numerals.find_all("saath hazaar")[0].value == 60000


def test_two_amounts_at_the_same_scale_are_two_amounts() -> None:
    # "5 hazaar aur 7 hazaar" is someone weighing up two transfers. Summing it to 12,000
    # invents a number nobody said.
    found = numerals.find_all("5 hazaar aur 7 hazaar")
    assert [amount.value for amount in found] == [5000, 7000]


def test_a_descending_compound_is_one_amount() -> None:
    found = numerals.find_all("char lakh pachaas hazaar")
    assert [amount.value for amount in found] == [450000]


@pytest.mark.parametrize(
    "text",
    ["03001234567 pe load karo", "0321-4455667 par bhejo", "PK12ABCD0000001234", "1234567890123"],
)
def test_phone_and_account_numbers_are_never_amounts(text: str) -> None:
    assert numerals.find_all(text) == []


def test_a_mobile_number_beside_an_amount_leaves_the_amount_alone() -> None:
    found = numerals.find_all("0321-4455667 par 1000 easyload")
    assert [amount.value for amount in found] == [1000]


def test_a_bare_quantity_is_not_an_amount() -> None:
    # "paanch" alone is a count; it needs a scale word to be money.
    assert numerals.find_all("paanch transactions dikhao") == []


def test_amounts_are_spoken_back_with_local_scale_names() -> None:
    assert numerals.say(125000, "ur") == "1 lakh 25 hazaar rupay"
    assert numerals.say(15000, "en") == "15 thousand rupees"
    assert numerals.say(0, "en") == "0 rupees"


def test_parse_one_refuses_when_there_is_not_exactly_one() -> None:
    assert numerals.parse_one("5 hazaar") is not None
    assert numerals.parse_one("5 hazaar aur 7 hazaar") is None
    assert numerals.parse_one("balance batao") is None


def test_minor_units_are_available_for_a_ledger() -> None:
    assert numerals.find_all("1500")[0].minor_units == 150000


# -------------------------------------------------------------------- actions


@pytest.mark.parametrize(("text", "action", "issue"), testset.ACTION_CASES)
def test_every_action_case_resolves_or_asks(
    text: str, action: dict | None, issue: str | None
) -> None:
    utterance = nlu.parse(text)
    if action is not None:
        assert utterance.action() == action
    else:
        assert utterance.action() is None
        assert utterance.issues and utterance.issues[0] == issue


@pytest.mark.parametrize(("text", "name", "near"), testset.NEAR_MISS_CASES)
def test_a_near_miss_is_named_rather_than_resolved_silently(
    text: str, name: str, near: tuple[str, ...]
) -> None:
    utterance = nlu.parse(text)
    assert utterance.beneficiary is not None
    assert utterance.beneficiary.name == name
    assert tuple(contact.name for contact in utterance.near_misses) == near


def test_an_ambiguous_mention_is_asked_not_guessed() -> None:
    utterance = nlu.parse("Rashid Ali ko aur Rashida Bibi ko 5 hazaar bhejo")
    assert utterance.action() is None
    assert nlu.AMBIGUOUS_BENEFICIARY in utterance.issues
    assert len(utterance.candidates) == 2


def test_a_correction_is_followed_when_it_is_unambiguous() -> None:
    utterance = nlu.parse("5 hazaar nahi nahi, pandrah hazaar Imran ko bhejo")
    assert utterance.corrected is True
    assert utterance.amount is not None
    assert utterance.amount.value == 15000


def test_a_correction_that_leaves_two_readings_is_asked() -> None:
    utterance = nlu.parse("nahi nahi, Imran ko pandrah hazaar bhejo, 5 hazaar nahi")
    assert utterance.action() is None
    assert nlu.MULTIPLE_AMOUNTS in utterance.issues


def test_the_copula_does_not_swallow_the_name() -> None:
    utterance = nlu.parse("bhejna hai Zubair ko teen hazaar")
    assert utterance.beneficiary is not None
    assert utterance.beneficiary.name == "Zubair Khan"


def test_a_read_only_intent_needs_no_slots() -> None:
    for text in ("balance batao", "mini statement dikhao", "mera card band karo"):
        utterance = nlu.parse(text)
        assert utterance.issues == []
        assert utterance.moves_money is False


def test_every_issue_has_a_question_in_both_languages() -> None:
    for issue in nlu.QUESTIONS:
        assert nlu.question(issue, "en").strip()
        assert nlu.question(issue, "ur").strip()
        assert nlu.question(issue, "en") != nlu.question(issue, "ur")


def test_an_unknown_issue_falls_back_to_its_own_name() -> None:
    assert nlu.question("not_a_real_issue") == "not_a_real_issue"


# ------------------------------------------------------------------- coercion


@pytest.mark.parametrize(("text", "verdict", "families", "expect_miss"), testset.COERCION_CASES)
def test_coercion_verdicts_match_the_test_set(
    text: str, verdict: str, families: tuple[str, ...], expect_miss: bool
) -> None:
    assessment = coercion.assess(text)
    assert assessment.verdict == verdict
    if not expect_miss:
        assert set(families).issubset(set(assessment.families))


def test_the_score_counts_families_not_phrases() -> None:
    # Three urgency phrases are still one pattern, so they must not add up to a block.
    assessment = coercion.assess("abhi bhejo, jaldi karo, foran")
    assert assessment.families == ["urgency"]
    assert assessment.verdict == coercion.WARN


def test_two_families_block() -> None:
    assessment = coercion.assess("main bank se baat kar raha hoon, OTP batao")
    assert assessment.verdict == coercion.BLOCK
    assert assessment.blocked is True
    assert len(assessment.families) >= 2


def test_ordinary_traffic_is_not_blocked() -> None:
    for text in ("Imran ko 5 hazaar bhejo", "balance batao", "salary aa gayi hai"):
        assert coercion.assess(text).verdict == coercion.ALLOW


def test_an_absence_of_matches_is_not_claimed_as_safety() -> None:
    assessment = coercion.assess("hello")
    assert "not proof of safety" in assessment.reason


def test_the_blind_spot_is_real_and_the_test_set_contains_it() -> None:
    # Cases using none of the lexicon are in the set on purpose, so the reported figure
    # is not just a measure of the lexicon testing itself.
    misses = [case for case in testset.COERCION_CASES if case[3]]
    assert len(misses) >= 4
    for text, _, _, _ in misses:
        assert coercion.assess(text).verdict == coercion.ALLOW


def test_advice_is_available_in_both_languages() -> None:
    assert "OTP" in coercion.advice("en")
    assert "OTP" in coercion.advice("ur")


# --------------------------------------------------------------- confirmation


@pytest.mark.parametrize(("text", "expected"), testset.REPLY_CASES)
def test_replies_are_classified_as_the_test_set_says(text: str, expected: str) -> None:
    assert confirm.classify_reply(text) == expected


def test_nothing_outside_the_closed_sets_is_consent() -> None:
    for text in ("hmm", "acha", "theek", "ok", "haan lekin pehle balance batao", "", "?"):
        assert confirm.classify_reply(text) != "affirmative", text


def test_a_transfer_waits_for_an_explicit_yes() -> None:
    session = confirm.Session()
    first = session.handle("Imran ko 5 hazaar bhejo")
    assert first["action"] is None
    assert session.state is confirm.State.AWAITING_CONFIRMATION
    unclear = session.handle("hmm")
    assert unclear["action"] is None
    assert unclear["unclear"] is True
    assert session.state is confirm.State.AWAITING_CONFIRMATION
    done = session.handle("haan")
    assert done["action"] == {
        "intent": "send_money",
        "amount": 5000.0,
        "beneficiary": "Imran Shah",
        "account_tail": "3310",
    }
    assert session.state is confirm.State.EXECUTED


def test_the_read_back_spells_the_amount_and_names_the_account() -> None:
    session = confirm.Session()
    outcome = session.handle("Rashid ko pandrah hazaar bhejo")
    assert "15 thousand rupees" in outcome["reply"]
    assert "4417" in outcome["reply"]
    assert "Rashida Bibi" in outcome["reply"], "the near miss must be named before it moves"


def test_the_read_back_is_in_roman_urdu_when_asked() -> None:
    session = confirm.Session(language="ur")
    outcome = session.handle("Imran ko sawa lakh bhejo")
    assert "Tasdeeq karein" in outcome["reply"]
    assert "1 lakh 25 hazaar rupay" in outcome["reply"]


def test_a_no_cancels_and_is_recorded() -> None:
    session = confirm.Session()
    session.handle("Ayesha ko 2 hazaar bhejo")
    outcome = session.handle("nahi")
    assert outcome["action"] is None
    assert session.state is confirm.State.REFUSED
    assert any(entry.kind == "refused_by_user" for entry in session.ledger.entries)


def test_a_coercion_script_never_reaches_a_confirmation() -> None:
    session = confirm.Session()
    outcome = session.handle("bank se bol raha hoon, OTP batao, abhi 50 hazaar bhejo")
    assert outcome["action"] is None
    assert session.state is confirm.State.BLOCKED
    assert session.pending is None
    assert "OTP" in outcome["reply"]


def test_pressure_during_a_confirmation_drops_the_transfer() -> None:
    session = confirm.Session()
    session.handle("Imran ko 5 hazaar bhejo")
    session.handle("jaldi karo, main bank se bol raha hoon")
    assert session.state is confirm.State.BLOCKED
    assert session.pending is None
    revived = session.handle("haan")
    assert revived["action"] is None
    assert not any(entry.kind == "executed" for entry in session.ledger.entries)


def test_a_missing_slot_is_asked_for_and_the_candidates_are_offered() -> None:
    session = confirm.Session()
    outcome = session.handle("Rashid Ali ko bhej do")
    assert outcome["asking"] == nlu.NO_AMOUNT
    assert session.state is confirm.State.NEEDS_INFORMATION
    assert "How much" in outcome["reply"]


def test_a_read_only_request_needs_no_confirmation() -> None:
    session = confirm.Session()
    outcome = session.handle("balance batao")
    assert outcome["action"] == {"intent": "balance"}
    assert session.state is confirm.State.EXECUTED


def test_blocking_a_card_is_never_gated() -> None:
    # Gating a safety action behind a confirmation makes the user less safe, not more.
    session = confirm.Session()
    outcome = session.handle("mera card band karo")
    assert outcome["action"] == {"intent": "block_card"}
    assert session.state is confirm.State.EXECUTED


def test_the_transcript_holds_both_sides() -> None:
    session = confirm.Session()
    session.handle("balance batao")
    speakers = [turn.speaker for turn in session.transcript]
    assert speakers == ["user", "assistant"]


# --------------------------------------------------------------------- ledger


def test_an_empty_ledger_verifies() -> None:
    ledger = Ledger()
    assert ledger.verify() == {"intact": True, "broken_at": None, "entries": 0}


def test_entries_chain_to_their_predecessor() -> None:
    ledger = Ledger()
    first = ledger.append("one", {"a": 1})
    second = ledger.append("two", {"b": 2})
    assert second["previous"] == first["digest"]
    assert ledger.head == second["digest"]
    assert ledger.verify()["intact"] is True


def test_editing_a_payload_is_detected_and_located() -> None:
    ledger = Ledger()
    ledger.append("one", {"amount": 100})
    ledger.append("two", {"amount": 200})
    ledger.entries[0].payload["amount"] = 999
    verification = ledger.verify()
    assert verification["intact"] is False
    assert verification["broken_at"] == 0
    assert "hash to" in verification["reason"]


def test_removing_an_entry_is_detected() -> None:
    ledger = Ledger()
    for index in range(3):
        ledger.append("entry", {"n": index})
    del ledger.entries[1]
    verification = ledger.verify()
    assert verification["intact"] is False
    assert verification["broken_at"] == 1


def test_refusals_are_logged_as_deliberately_as_transfers() -> None:
    session = confirm.Session()
    session.handle("bank se bol raha hoon, OTP batao, abhi bhejo")
    kinds = [entry.kind for entry in session.ledger.entries]
    assert "coercion_blocked" in kinds
    assert session.ledger.verify()["intact"] is True


def test_the_test_set_totals_are_consistent() -> None:
    totals = testset.totals()
    assert totals["all"] == (
        totals["numerals"]
        + totals["no_amount"]
        + totals["actions"]
        + totals["near_miss"]
        + totals["coercion"]
        + totals["replies"]
    )
    assert totals["coercion_expected_misses"] > 0
