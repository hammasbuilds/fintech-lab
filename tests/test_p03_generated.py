"""The generated utterance set, checked against arithmetic rather than against itself.

A generated test set is only worth its size if the gold labels are right, and a gold
label is only trustworthy if something other than the generator says so. So these tests
re-derive the amounts by hand, from the phrases the product's own documentation uses as
examples, and assert the labelling rules the generator claims to follow.
"""

from __future__ import annotations

import random

import pytest

from projects.p03_guardian import generator as gen

#: The five phrases numerals.py documents, with the values it documents for them. If the
#: generator and the product disagree about these, one of them is wrong and it matters
#: which - so they are written out here a third time, by hand.
CANONICAL = {
    "sawa lakh": 125_000,
    "dedh hazaar": 1_500,
    "dhai lakh": 250_000,
    "saadhe teen hazaar": 3_500,
    "paune do lakh": 175_000,
}


def rendered(count: int = 40_000, seed: int = 0) -> dict[str, float]:
    rng = random.Random(seed)
    found: dict[str, float] = {}
    for _ in range(count):
        item = gen.render_amount(rng)
        found.setdefault(item.text, item.value)
    return found


@pytest.fixture(scope="module")
def phrases() -> dict[str, float]:
    return rendered()


@pytest.mark.parametrize(("phrase", "value"), sorted(CANONICAL.items()))
def test_the_documented_phrases_render_to_their_documented_value(
    phrases: dict[str, float], phrase: str, value: int
) -> None:
    assert phrases.get(phrase) == value, (
        f"{phrase!r} was generated with gold {phrases.get(phrase)}, and it means {value}"
    )


def test_fractional_modifiers_add_rather_than_multiply(phrases: dict[str, float]) -> None:
    """``saadhe teen`` is three and a half, not three times one and a half."""
    for phrase, expected in (
        ("sawa do hazaar", 2_250),
        ("saadhe char hazaar", 4_500),
        ("paune teen lakh", 275_000),
    ):
        if phrase in phrases:
            assert phrases[phrase] == expected, phrase


def test_paune_never_attaches_to_one() -> None:
    """``paune aik lakh`` is grammatical and nobody says it; it would also be 75,000."""
    rng = random.Random(7)
    for _ in range(20_000):
        item = gen.render_amount(rng)
        assert not item.text.startswith("paune aik"), item.text
        assert not item.text.startswith("paune one"), item.text


def test_every_amount_has_a_positive_gold_value() -> None:
    rng = random.Random(3)
    for _ in range(5_000):
        item = gen.render_amount(rng)
        assert item.value > 0
        assert item.text.strip() == item.text
        assert item.text


def test_digit_styles_carry_the_digits_they_claim() -> None:
    rng = random.Random(11)
    checked = 0
    for _ in range(4_000):
        item = gen.render_amount(rng)
        if item.style in {"digits", "digits_slash"}:
            digits = "".join(char for char in item.text if char.isdigit())
            assert float(digits) == item.value
            checked += 1
    assert checked > 100


def test_urdu_digit_amounts_use_urdu_digits() -> None:
    rng = random.Random(5)
    found = 0
    for _ in range(4_000):
        item = gen.render_amount(rng)
        if item.style == "urdu_digits":
            assert all(char in "۰۱۲۳۴۵۶۷۸۹" for char in item.text)
            found += 1
    assert found > 50


# ------------------------------------------------------------------------- the labels


def plan(count: int = 300) -> list[gen.Case]:
    return list(gen.cases({family: count for family in gen.PLAN}, seed=99))


@pytest.fixture(scope="module")
def sample() -> list[gen.Case]:
    return plan()


def test_a_benign_script_that_names_no_contact_carries_no_contact_in_its_gold(
    sample: list[gen.Case],
) -> None:
    """ "ammi ko 1,730 bhejo" names a relation, and the gold used to name a stranger."""
    for case in sample:
        if case.family != "benign":
            continue
        if not case.recipe.get("names_a_payee"):
            assert case.gold_payee is None, case.text
            assert case.recipe["mention"] == ""


def test_benign_traffic_is_never_labelled_as_something_to_block(
    sample: list[gen.Case],
) -> None:
    for case in sample:
        if case.family == "benign":
            assert case.gold_should_block is False
            assert case.gold_coercion == ()


def test_every_coercion_case_names_at_least_one_family(sample: list[gen.Case]) -> None:
    for case in sample:
        if case.family == "coercion":
            assert case.gold_should_block is True
            assert len(set(case.gold_coercion)) == len(case.gold_coercion)
            if case.outside_the_vocabulary:
                # A scam type the product has no words for names no family on
                # purpose: that is what makes it a measurement of the blind spot
                # rather than of the lexicon testing itself.
                assert case.gold_coercion == ()
                assert case.recipe["scam_type"] in gen.OUTSIDE_THE_VOCABULARY
            else:
                assert len(case.gold_coercion) >= 1


def test_an_ambiguous_first_name_is_labelled_ask_and_a_unique_one_resolved(
    sample: list[gen.Case],
) -> None:
    asked = resolved = 0
    for case in sample:
        mention = str(case.recipe.get("mention", ""))
        if not mention or " " in mention:
            continue
        # In a conversation the mention belongs to the *request*; by the answering turn
        # the payee has been picked, so "resolved" is right there and this rule does not
        # apply to it.
        if case.recipe.get("role") not in (None, "request"):
            continue
        matches = gen.FIRST_NAMES.get(mention.lower(), [])
        if len(matches) > 1:
            assert case.gold_resolution == "ask", case.text
            asked += 1
        elif len(matches) == 1:
            assert case.gold_resolution == "resolved", case.text
            assert case.gold_payee == matches[0]
            resolved += 1
    assert asked > 0 and resolved > 0


def test_the_contact_book_is_a_world_mix_with_the_collisions_it_claims() -> None:
    assert len(gen.BOOK) == len(gen.CONTACT_NAMES) == 60
    assert len({contact.last4 for contact in gen.BOOK}) == 60
    shared = {first: names for first, names in gen.FIRST_NAMES.items() if len(names) > 1}
    assert {"imran", "ayesha", "rashid", "bilal", "chen"} <= set(shared)


def test_the_plan_adds_up_to_two_hundred_thousand() -> None:
    assert sum(gen.PLAN.values()) == 200_000
    assert min(gen.PLAN.values()) >= 1_000


def test_generation_is_reproducible_from_the_seed() -> None:
    first = [case.text for case in gen.cases({"amount": 200}, seed=4)]
    second = [case.text for case in gen.cases({"amount": 200}, seed=4)]
    assert first == second
    assert len(set(first)) > 150, "a test set of 200,000 identical sentences is one case"


def test_the_spellings_used_are_the_ones_the_corpora_attest() -> None:
    from projects.p03_guardian import variants

    assert variants.VARIANTS, "the mined variant table is empty"
    for meaning in ("send", "quickly", "thousand", "lakh"):
        assert gen.attested(meaning), meaning
        for spelling in gen.attested(meaning):
            assert variants.VARIANTS[meaning][spelling] >= 2


def test_unattested_banking_words_are_kept_separate() -> None:
    """Absence from two corpora of film talk is not evidence nobody writes 'otp'."""
    from projects.p03_guardian import variants

    for word in gen.CURATED_BANKING:
        assert not any(word in table for table in variants.VARIANTS.values()), word


# ------------------------------------------------- what the conversations found


def test_a_number_word_is_never_read_as_a_payee() -> None:
    """ "Okonkwo ko char sau send" paid Charlotte Hughes before this was fixed.

    "char sau" is four hundred; "char" prefixes "Charlotte", and the resolver took any
    word of three letters or more that prefixed a contact name. The read-back then named
    the wrong payee, and a user who says "theek hai" to that has paid a stranger.
    """
    from projects.p03_guardian import nlu
    from projects.p03_guardian.bigbench import CONTACTS

    for text in (
        "Okonkwo ko char sau send",
        "Berenice ko paune char hzar bhej",
        "Wei Lin ko char hazaar panch sau bhej",
    ):
        resolution = nlu.resolve_beneficiary(text, CONTACTS)
        assert resolution.contact is None, f"{text} resolved to {resolution.contact}"
        assert "unknown_beneficiary" in resolution.issues


def test_a_name_that_happens_to_look_like_a_number_still_resolves() -> None:
    """The fix withholds the amount's span, not the name."""
    from projects.p03_guardian import nlu
    from projects.p03_guardian.bigbench import CONTACTS

    for text, expected in (
        ("Charlotte Hughes ko char sau bhejo", "Charlotte Hughes"),
        ("Charlotte ko do hazaar bhejo", "Charlotte Hughes"),
        ("Chen Wei ko paanch sau bhej do", "Chen Wei"),
    ):
        resolution = nlu.resolve_beneficiary(text, CONTACTS)
        assert resolution.contact is not None, text
        assert resolution.contact.name == expected


def test_a_compound_yes_is_consent_and_a_hum_is_not() -> None:
    from projects.p03_guardian.confirm import classify_reply

    for yes in ("haan bhej do", "haan bhejo", "theek hai bhej do", "yes send it"):
        assert classify_reply(yes) == "affirmative", yes
    for unclear in ("hmm theek", "bhej", "acha", "theek hai na"):
        assert classify_reply(unclear) == "unclear", unclear


def test_which_one_can_be_answered_by_ordinal_bank_or_tail() -> None:
    from projects.p03_guardian import nlu
    from projects.p03_guardian.bigbench import CONTACTS
    from projects.p03_guardian.confirm import Session, State

    shared = [name for name, holders in gen.FIRST_NAMES.items() if len(holders) > 1]
    first = sorted(shared)[0]
    holders = [item for item in CONTACTS if item.name.split()[0].lower() == first]
    assert len(holders) > 1

    for answer, expected in (
        ("pehla wala", holders[0].name),
        ("doosra", holders[1].name),
        (holders[1].account_tail, holders[1].name),
    ):
        session = Session(contacts=CONTACTS)
        session.handle(f"{first.capitalize()} ko paanch hazaar bhejo")
        session.handle(answer)
        assert session.pending is not None, answer
        assert session.pending.beneficiary is not None, answer
        assert session.pending.beneficiary.name == expected, answer
        assert session.state is State.AWAITING_CONFIRMATION

    # An answer that is not an answer must not be read as one - and must not lose the
    # request either. It used to be parsed as a new instruction, found to contain none,
    # and the whole transfer was dropped.
    session = Session(contacts=CONTACTS)
    session.handle(f"{first.capitalize()} ko paanch hazaar bhejo")
    out = session.handle("koi bhi")
    assert session.state is State.NEEDS_INFORMATION
    assert out["asking"] == nlu.AMBIGUOUS_BENEFICIARY
    assert session.pending is not None
    assert session.pending.amount is not None and session.pending.amount.value == 5000
    # And the answer still works on the next turn.
    session.handle("doosra")
    assert session.state is State.AWAITING_CONFIRMATION


def test_a_real_instruction_still_replaces_a_pending_one() -> None:
    """Holding the pending request must not trap the user in it."""
    from projects.p03_guardian.bigbench import CONTACTS
    from projects.p03_guardian.confirm import Session, State

    session = Session(contacts=CONTACTS)
    session.handle("Imran ko paanch hazaar bhejo")
    out = session.handle("balance batao")
    assert session.state is State.EXECUTED
    assert out["action"]["intent"] == "balance"


def test_a_conversation_about_a_stranger_cannot_complete() -> None:
    """The gold says so, and the product has to agree."""
    from projects.p03_guardian.bigbench import CONTACTS
    from projects.p03_guardian.confirm import Session, State

    session = Session(contacts=CONTACTS)
    session.handle("Okonkwo ko char sau send")
    session.handle("theek hai")
    assert session.state is not State.EXECUTED


def test_a_name_inside_another_name_is_not_an_ambiguity() -> None:
    """ "Chen Wei Ltd" is the company, not a choice between it and a person."""
    from projects.p03_guardian import nlu
    from projects.p03_guardian.bigbench import CONTACTS

    for text, expected in (
        ("Chen Wei Ltd ko 1 sau bhej", "Chen Wei Ltd"),
        ("Chen Wei ko 1 sau bhej", "Chen Wei"),
        ("Bilal Traders ko teen hazaar bhejo", "Bilal Traders"),
        ("Bilal Khan ko teen hazaar bhejo", "Bilal Khan"),
    ):
        resolution = nlu.resolve_beneficiary(text, CONTACTS)
        assert resolution.contact is not None, text
        assert resolution.contact.name == expected, text


def test_two_unrelated_names_in_one_sentence_stay_ambiguous() -> None:
    """The longest-match rule must not turn a real ambiguity into a guess."""
    from projects.p03_guardian import nlu
    from projects.p03_guardian.bigbench import CONTACTS

    resolution = nlu.resolve_beneficiary("Rashid Ali aur Imran Shah ko bhejo", CONTACTS)
    assert resolution.contact is None
    assert nlu.AMBIGUOUS_BENEFICIARY in resolution.issues
    assert len(resolution.candidates) == 2
