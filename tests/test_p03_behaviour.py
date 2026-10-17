"""The behavioural layer: what it recovers, what it costs, and where it must not block."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from projects.p03_guardian import behaviour, coercion, testset
from projects.p03_guardian.behaviour import Profile, Transfer, assess, demo_profile
from projects.p03_guardian.confirm import STEP_UP, Session, State


def profile() -> Profile:
    return demo_profile()


# ------------------------------------------------------------------- signals


def test_an_ordinary_transfer_to_a_known_payee_raises_nothing() -> None:
    result = assess(profile(), "Bilal Traders", 3200)
    assert result.level == behaviour.NONE
    assert result.signals == []
    assert "not proof it is wanted" in result.reason


def test_the_regular_large_transfer_to_a_parent_is_not_unusual() -> None:
    # 12,000 is nearly four times the account's median transfer, but it is exactly what
    # this payee always receives. Judging it against one account-wide median marks the
    # single most routine payment the account makes as suspicious.
    result = assess(profile(), "Nasreen Akhtar", 12000)
    assert result.level == behaviour.NONE, [signal.name for signal in result.signals]


def test_the_same_amount_to_someone_new_is_unusual() -> None:
    result = assess(profile(), "Zubair Khan", 12000)
    names = {signal.name for signal in result.signals}
    assert "first_time_beneficiary" in names
    assert "unusually_large" in names
    assert result.escalated


def test_a_payee_with_history_sets_their_own_baseline() -> None:
    usual, basis = profile().usual_for("Nasreen Akhtar")
    assert usual == 12000
    assert "Nasreen Akhtar" in basis
    fallback, basis = profile().usual_for("Someone New")
    assert fallback == profile().usual_transfer
    assert "this account" in basis


def test_the_usual_transfer_is_a_median_not_a_mean() -> None:
    # One large transfer in the history must not raise the bar for every later one.
    base = datetime(2026, 10, 15, 14, 30)
    history = [Transfer("A", 1000, base - timedelta(days=d)) for d in range(1, 6)]
    history.append(Transfer("B", 500000, base - timedelta(days=7)))
    assert Profile(history=history, now=base).usual_transfer == 1000


def test_a_balance_sweep_is_flagged() -> None:
    result = assess(profile(), "Bilal Traders", 18000)
    assert "balance_sweep" in {signal.name for signal in result.signals}


def test_passing_the_daily_cap_escalates_on_its_own() -> None:
    # A cap a habitual yes clears is not a cap, so this one signal is enough.
    base = datetime(2026, 10, 15, 14, 30)
    history = [Transfer("Bilal Traders", 48000, base - timedelta(minutes=30))]
    account = Profile(balance=900000, daily_cap=50000, history=history, now=base)
    result = assess(account, "Bilal Traders", 5000)
    assert [signal.name for signal in result.signals] == ["beyond_daily_cap"]
    assert result.escalated
    assert "limit the account already has" in result.reason


def test_a_burst_of_transfers_is_flagged() -> None:
    base = datetime(2026, 10, 15, 14, 30)
    history = [Transfer("Bilal Traders", 100, base - timedelta(minutes=m)) for m in (5, 15, 25)]
    account = Profile(balance=900000, daily_cap=900000, history=history, now=base)
    result = assess(account, "Bilal Traders", 100)
    assert "rapid_repeat" in {signal.name for signal in result.signals}


def test_one_signal_alone_is_a_note_not_an_escalation() -> None:
    base = datetime(2026, 10, 15, 14, 30)
    account = Profile(balance=900000, daily_cap=900000, history=[], now=base)
    result = assess(account, "Someone New", 1000)
    assert [signal.name for signal in result.signals] == ["first_time_beneficiary"]
    assert result.level == behaviour.NOTE
    assert not result.escalated


def test_nothing_is_judged_without_an_amount() -> None:
    assert assess(profile(), "Zubair Khan", None).level == behaviour.NONE
    assert assess(profile(), "Zubair Khan", 0).level == behaviour.NONE


def test_an_empty_history_does_not_divide_by_zero() -> None:
    account = Profile(balance=0, daily_cap=0, history=[], now=datetime(2026, 10, 15))
    result = assess(account, "Anyone", 100)
    assert result.level in {behaviour.NOTE, behaviour.ELEVATED}


def test_advice_is_available_in_both_languages_and_says_to_call_back() -> None:
    assert "hang up" in behaviour.advice("en").lower()
    assert "call" in behaviour.advice("ur").lower()


# ------------------------------------------- what it recovers and what it costs


@pytest.mark.parametrize(
    ("label", "message", "payee", "amount", "verdict", "level", "scam"), testset.PAIRED_CASES
)
def test_every_paired_case_behaves_as_written(
    label: str, message: str, payee: str, amount: float, verdict: str, level: str, scam: bool
) -> None:
    assert coercion.assess(message).verdict == verdict, label
    assert assess(demo_profile(), payee, amount).level == level, label


def test_the_layer_recovers_every_scam_the_words_miss() -> None:
    # Three, not four: the purchase-scam family added in the dataset round reads the
    # property-token case, so the words catch it and it is no longer a blind spot. The
    # case is still in the paired set, now as one both layers see - see the test below.
    scams = [case for case in testset.PAIRED_CASES if case[6]]
    assert len(scams) == 3
    for label, message, payee, amount, _verdict, _level, _scam in scams:
        assert coercion.assess(message).verdict == coercion.ALLOW, f"{label} is not a blind spot"
        assert assess(demo_profile(), payee, amount).escalated, label


def test_a_scam_both_layers_catch_is_still_in_the_paired_set() -> None:
    """Belt and braces is the design, so a case that fires both is worth keeping."""
    found = [case for case in testset.PAIRED_CASES if case[0] == "property token"]
    assert found, "the case was deleted rather than relabelled"
    _label, message, payee, amount, verdict, level, is_a_blind_spot = found[0]
    assert is_a_blind_spot is False
    assert verdict == "warn"
    assert coercion.assess(message).families == ["purchase_scam"]
    assert assess(demo_profile(), payee, amount).level == level


def test_the_cost_is_bounded_and_is_friction_rather_than_refusal() -> None:
    benign = [case for case in testset.PAIRED_CASES if not case[6]]
    escalated = [case for case in benign if assess(demo_profile(), case[2], case[3]).escalated]
    # Two of these escalate: a first large transfer to a new payee, which is the
    # deliberate legitimate case, and the property-token scam the words now also catch.
    # Escalation is a slower confirmation either way, not a block.
    assert len(escalated) == 2
    assert sorted(case[0] for case in escalated) == ["new landlord", "property token"]
    session = Session()
    session.handle("Rashid ko pandrah hazaar bhejo")
    assert session.state is State.AWAITING_CONFIRMATION
    assert session.handle("confirm")["action"] is not None


# ------------------------------------------------- how it changes the session


def test_an_escalated_transfer_refuses_a_habitual_yes() -> None:
    session = Session()
    first = session.handle("Zubair ko pachaas hazaar bhejo")
    assert first["behaviour"]["level"] == behaviour.ELEVATED
    assert "confirm" in first["reply"]
    habitual = session.handle("haan")
    assert habitual["action"] is None
    assert habitual["step_up_required"] is True
    assert session.state is State.AWAITING_CONFIRMATION
    done = session.handle("confirm")
    assert done["action"] is not None
    assert session.state is State.EXECUTED


def test_an_ordinary_transfer_still_takes_a_plain_yes() -> None:
    # If the step-up applied to everything it would just be the confirmation, and the
    # stronger word would stop meaning anything.
    session = Session()
    session.handle("Bilal Traders ko teen hazaar bhejo")
    assert session.handle("haan")["action"] is not None


def test_the_step_up_words_are_a_closed_set() -> None:
    for word in sorted(STEP_UP):
        session = Session()
        session.handle("Zubair ko pachaas hazaar bhejo")
        assert session.handle(word)["action"] is not None, word


def test_no_still_cancels_an_escalated_transfer() -> None:
    session = Session()
    session.handle("Zubair ko pachaas hazaar bhejo")
    out = session.handle("nahi")
    assert out["action"] is None
    assert session.state is State.REFUSED
    assert session.stepped_up is False


def test_an_unclear_reply_to_an_escalated_transfer_is_still_unclear() -> None:
    session = Session()
    session.handle("Zubair ko pachaas hazaar bhejo")
    out = session.handle("hmm")
    assert out["action"] is None
    assert session.state is State.AWAITING_CONFIRMATION


def test_coercion_still_blocks_and_is_never_merely_escalated() -> None:
    # The distinction is the design: words that only occur in a script block outright,
    # and a step-up is never offered as a way past them.
    session = Session()
    out = session.handle("bank se bol raha hoon, OTP batao, abhi pachaas hazaar bhejo")
    assert session.state is State.BLOCKED
    assert out["action"] is None
    assert session.pending is None
    assert session.stepped_up is False
    assert session.handle("confirm")["action"] is None


def test_the_profile_learns_so_a_payee_is_new_only_once() -> None:
    session = Session()
    session.handle("Zubair ko do hazaar bhejo")
    session.handle("confirm")
    assert "Zubair Khan" in session.profile.known_beneficiaries
    again = session.handle("Zubair ko do hazaar bhejo")
    assert "first_time_beneficiary" not in {s["name"] for s in again["behaviour"]["signals"]}


def test_the_daily_total_accumulates_across_a_session() -> None:
    session = Session()
    before = session.profile.sent_today()
    session.handle("Bilal Traders ko teen hazaar bhejo")
    session.handle("haan")
    assert session.profile.sent_today() == before + 3000


def test_the_ledger_records_the_behavioural_verdict_and_the_step_up() -> None:
    session = Session()
    session.handle("Zubair ko pachaas hazaar bhejo")
    session.handle("haan")
    kinds = [entry.kind for entry in session.ledger.entries]
    assert "confirmation_requested" in kinds
    assert "step_up_required" in kinds
    requested = next(e for e in session.ledger.entries if e.kind == "confirmation_requested")
    assert requested.payload["behaviour"] == behaviour.ELEVATED
    assert requested.payload["signals"]
    assert session.ledger.verify()["intact"] is True


def test_the_read_back_says_what_stands_out_in_both_languages() -> None:
    for language, marker in (("en", "unusual"), ("ur", "ghair-mamooli")):
        session = Session(language=language)
        reply = session.handle("Zubair ko pachaas hazaar bhejo")["reply"]
        assert marker in reply, language


def test_every_step_up_word_is_also_an_ordinary_yes() -> None:
    # A word accepted as a step-up but not recognised as a yes can never execute
    # anything. Folding one set into the other makes that impossible to reintroduce.
    from projects.p03_guardian.confirm import AFFIRMATIVE, classify_reply

    assert STEP_UP <= AFFIRMATIVE
    for word in sorted(STEP_UP):
        assert classify_reply(word) == "affirmative", word


def test_a_transfer_larger_than_the_balance_is_not_called_a_sweep() -> None:
    # "50,000 is 271% of the balance" is nonsense. The money is not there, which is a
    # different statement from taking most of what is.
    account = Profile(balance=18423.50, daily_cap=900000, history=[], now=datetime(2026, 10, 15))
    result = assess(account, "Someone New", 50000)
    names = {signal.name for signal in result.signals}
    assert "exceeds_balance" in names
    assert "balance_sweep" not in names
    assert result.escalated
    detail = next(s.detail for s in result.signals if s.name == "exceeds_balance")
    assert "more than" in detail and "%" not in detail


def test_taking_most_of_the_balance_is_still_a_sweep() -> None:
    account = Profile(balance=20000, daily_cap=900000, history=[], now=datetime(2026, 10, 15))
    names = {signal.name for signal in assess(account, "Someone New", 18000).signals}
    assert "balance_sweep" in names
    assert "exceeds_balance" not in names
