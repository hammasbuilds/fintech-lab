"""The generated corpus, and the harness bug that only a corpus could show.

These tests run on a handful of lifecycles rather than the full 50,960: the point is the
properties, which hold per flow, and a test suite that takes three minutes is a test
suite nobody runs.
"""

from __future__ import annotations

import random

import pytest

from projects.p01_switchboard import binary, corpus, lifecycle, mutations, scenarios
from projects.p01_switchboard.codec import check_values, luhn_ok, pack, unpack


def contexts(count: int, seed: int = 11) -> list[scenarios.Context]:
    rng = random.Random(seed)
    return [corpus.random_context(rng) for _ in range(count)]


def test_a_generated_context_carries_a_pan_that_passes_its_own_check_digit() -> None:
    for context in contexts(40):
        assert len(context.pan) == 16
        assert luhn_ok(context.pan), context.pan


def test_generated_amounts_span_orders_of_magnitude() -> None:
    """A corpus of one typical amount cannot show that a rule depends on the amount."""
    amounts = sorted(context.amount for context in contexts(200))
    assert amounts[0] < 1_000
    assert amounts[-1] > 100_000


def test_generated_currencies_are_not_all_the_shipped_one() -> None:
    found = {context.currency for context in contexts(60)}
    assert len(found) >= 4
    assert found - {"586"}, "every context is in rupees, so a currency bug cannot surface"


def test_every_message_in_a_generated_flow_round_trips_in_both_framings() -> None:
    for item in list(corpus.flows(3)):
        for row in corpus.message_rows(item):
            message = corpus.message_from_row(row)
            text = pack(message)
            assert text == row["ascii"]
            assert pack(unpack(text)) == text
            assert binary.unpack_binary(binary.pack_binary(message)).fields == message.fields
            assert check_values(message.fields) == []


def test_generated_flows_are_valid_by_construction() -> None:
    """Every finding on these is a false positive, which is what makes them useful."""
    for item in list(corpus.flows(3)):
        findings = lifecycle.check(item.flow)
        assert findings == [], f"{item.flow.key}: {[f.rule_id for f in findings]}"


def test_a_corpus_run_covers_every_scenario() -> None:
    keys = {item.flow.key for item in corpus.flows(1)}
    assert keys == {scenario.key for scenario in scenarios.SCENARIOS}


# --------------------------------------------------------------------------- mutants


def test_every_mutant_actually_changes_the_flow() -> None:
    """A mutant that mutates nothing is scored as a survivor and is not one.

    Two currency mutators wrote a literal 840 into the response and into chip tag 5F2A.
    Against the shipped scenarios, which are all in 586, that is a real fault every time.
    Against a corpus with five currencies in it, one in five flows was already in dollars
    and the 'fault' was byte-identical to the original - so the detector was recorded as
    having missed a fault that did not exist, and the kill rate of one of them read 0.18.
    """
    rng = random.Random(4242)
    for _ in range(12):
        context = corpus.random_context(rng)
        clean = {
            scenario.key: scenarios.build(scenario.key, scenarios.Context(**vars(context)))
            for scenario in scenarios.SCENARIOS
        }
        for mutant in mutations.generate(context):
            before = [pack(step.message) for step in clean[mutant.scenario].steps]
            after = [pack(step.message) for step in mutant.flow.steps] if mutant.flow else None
            if after is None:
                continue  # a codec-layer mutant works on raw text, checked below
            assert before != after, (
                f"{mutant.mutator} on {mutant.scenario} in currency {context.currency} "
                f"produced an identical flow, so nothing was injected to detect"
            )


def test_no_mutant_survives_at_any_currency_or_amount() -> None:
    rng = random.Random(4242)
    survivors = []
    for _ in range(8):
        context = corpus.random_context(rng)
        for mutant in mutations.generate(context):
            if not mutations.detect(mutant).killed:
                survivors.append((mutant.mutator, context.currency, context.amount))
    assert survivors == []


def test_the_currency_swap_helper_never_returns_what_it_was_given() -> None:
    for current in ("586", "840", "784", "826", None):
        assert mutations._other_currency(current) != current


# ------------------------------------------------------------------------ fuzz pools


def test_a_wider_fuzz_pool_holds_more_messages_than_the_default_one() -> None:
    narrow = mutations.fuzz(trials=120)
    wide = mutations.fuzz(trials=120, contexts=8)
    assert narrow["contexts_in_pool"] == 1
    assert wide["contexts_in_pool"] == 8
    assert wide["messages_in_pool"] > narrow["messages_in_pool"]


def test_the_default_fuzz_pool_is_unchanged_by_the_context_option() -> None:
    """The committed benchmark numbers must not move because an option was added."""
    assert mutations.fuzz(trials=200)["messages_in_pool"] == 10
    assert mutations.fuzz_binary(trials=200)["messages_in_pool"] == 20


def test_an_illegal_character_is_never_accepted_in_a_widened_pool() -> None:
    result = mutations.fuzz(trials=1500, contexts=6)
    assert result["groups"]["class_violating"]["rejection_rate"] == 1.0
    assert result["illegal_characters_accepted"] == []


@pytest.mark.parametrize("count", [1, 2])
def test_building_a_corpus_is_reproducible_from_its_seed(count: int) -> None:
    first = [
        row["ascii"] for item in corpus.flows(count, seed=5) for row in corpus.message_rows(item)
    ]
    second = [
        row["ascii"] for item in corpus.flows(count, seed=5) for row in corpus.message_rows(item)
    ]
    assert first == second
    assert len(first) > 20
