"""The three model projects: their graders, validated against the deterministic baseline.

The order matters here. Each grader is first run against the thing it is grading *for* -
project 01's generator, project 02's hand-written policies and templates, project 03's
rules - and must score those perfectly. A grader that marks down the known-correct answer
is a broken grader, and every model number it then produces is noise.

That check has already earned its place: it caught a notice grader that treated an
approval's stated reasons as "invented", and a keyword list where the Roman Urdu word for
a repaid loan matched the pattern for a default.
"""

from __future__ import annotations

import json

import pytest

from projects.p01_switchboard import scenarios
from projects.p03_guardian import testset
from projects.p04_spec_to_wire import grade as g4
from projects.p04_spec_to_wire import tasks as t4
from projects.p05_policy_from_prose import grade as g5
from projects.p05_policy_from_prose import tasks as t5
from projects.p06_model_vs_lexicon import grade as g6
from projects.p06_model_vs_lexicon import tasks as t6

# ======================================================== 04 spec to wire


def case4(family: str, scenario: str = "atm_withdrawal_approved") -> t4.Case:
    return next(
        case
        for case in t4.cases()
        if case.family == family and (family == t4.NEGATIVE or case.scenario == scenario)
    )


def test_04_has_cases_across_every_family() -> None:
    families = {case.family for case in t4.cases()}
    assert families == {t4.DECODE, t4.FIELDS, t4.WIRE, t4.NEGATIVE}
    assert len(t4.cases()) == 3 * len(t4.CASE_SCENARIOS) + len(t4.NEGATIVE_RULES)


def test_04_the_spec_arm_differs_only_by_added_context() -> None:
    case = case4(t4.FIELDS)
    bare, spec = t4.prompt(case, t4.BARE), t4.prompt(case, t4.SPEC)
    assert len(spec) > len(bare) * 2
    assert bare.split("\n\n")[0] == spec.split("\n\n")[0], "the task must be identical"
    assert "DE 2" in spec and "DE 2" not in bare


def test_04_the_generator_scores_perfectly_on_every_case() -> None:
    for case in t4.cases():
        result = g4.baseline(case)
        assert result.score == 1.0, case.label


def test_04_a_perfect_fields_answer_scores_one() -> None:
    case = case4(t4.FIELDS)
    truth = scenarios.build(case.scenario, scenarios.Context()).steps[0].message
    reply = json.dumps({"mti": truth.mti, "fields": {str(de): v for de, v in truth.fields.items()}})
    result = g4.grade_fields(case, reply, "test", "none")
    assert result.parsed and result.packs and result.invariants_hold
    assert result.score == 1.0
    assert result.detail == "exact"


def test_04_a_fields_answer_missing_elements_gets_partial_credit_and_names_them() -> None:
    case = case4(t4.FIELDS)
    truth = scenarios.build(case.scenario, scenarios.Context()).steps[0].message
    fields = {str(de): v for de, v in truth.fields.items()}
    dropped = fields.pop("41")
    result = g4.grade_fields(case, json.dumps({"mti": truth.mti, "fields": fields}), "t", "n")
    assert 0 < result.score < 1
    assert 41 in result.columns["missing"]
    del dropped


def test_04_an_unparseable_answer_scores_zero_and_says_why() -> None:
    for family, grader in (
        (t4.FIELDS, g4.grade_fields),
        (t4.DECODE, g4.grade_decode),
        (t4.NEGATIVE, g4.grade_negative),
    ):
        result = grader(case4(family), "I cannot help with that", "t", "n")
        assert result.score == 0.0
        assert result.parsed is False
        assert result.detail


def test_04_a_wire_answer_the_parser_rejects_scores_zero() -> None:
    result = g4.grade_wire(case4(t4.WIRE), "0200NOTHEXATALL", "t", "n")
    assert result.packs is False
    assert result.score == 0.0
    assert "rejected" in result.detail


def test_04_the_exact_wire_message_scores_one() -> None:
    case = case4(t4.WIRE)
    raw = scenarios.build(case.scenario, scenarios.Context()).steps[0].raw
    result = g4.grade_wire(case, raw, "t", "n")
    assert result.packs and result.score == 1.0
    assert "byte-identical" in result.detail


def test_04_a_negative_case_needs_the_named_rule_and_only_that_one() -> None:
    case = next(case for case in t4.cases() if case.rule == "L03")
    flow = scenarios.build("atm_withdrawal_approved", scenarios.Context())
    request, response = flow.steps[0].message, flow.steps[1].message

    # Dropping DE 39 breaks L03 and nothing else: full marks.
    clean = json.dumps(
        {
            "request": {
                "mti": request.mti,
                "fields": {str(d): v for d, v in request.fields.items()},
            },
            "response": {
                "mti": response.mti,
                "fields": {str(d): v for d, v in response.fields.items() if d != 39},
            },
        }
    )
    good = g4.grade_negative(case, clean, "t", "n")
    assert good.score == 1.0
    assert "and nothing else" in good.detail

    # A flow that also changes the currency breaks L13 as well: half marks. The
    # collateral damage has to leave the pair *matched* - changing the trace instead
    # unpairs the two messages, so L03 is never evaluated and the score is zero rather
    # than half, which is the checker being consistent and not a grader bug.
    messy = json.loads(clean)
    messy["response"]["fields"]["49"] = "840"
    noisy = g4.grade_negative(case, json.dumps(messy), "t", "n")
    assert noisy.score == 0.5
    assert noisy.columns["collateral"] == ["L13"]

    unpaired = json.loads(clean)
    unpaired["response"]["fields"]["11"] = "999999"
    lost = g4.grade_negative(case, json.dumps(unpaired), "t", "n")
    assert lost.score == 0.0
    assert "L03" not in lost.columns["rules_fired"]


def test_04_a_negative_case_that_breaks_nothing_scores_zero() -> None:
    case = next(case for case in t4.cases() if case.rule == "L03")
    flow = scenarios.build("atm_withdrawal_approved", scenarios.Context())
    valid = json.dumps(
        {
            "request": {
                "mti": flow.steps[0].message.mti,
                "fields": {str(d): v for d, v in flow.steps[0].message.fields.items()},
            },
            "response": {
                "mti": flow.steps[1].message.mti,
                "fields": {str(d): v for d, v in flow.steps[1].message.fields.items()},
            },
        }
    )
    result = g4.grade_negative(case, valid, "t", "n")
    assert result.score == 0.0
    assert result.invariants_hold is True
    assert "did not break" in result.detail


def test_04_decode_is_graded_field_by_field() -> None:
    case = case4(t4.DECODE)
    truth = scenarios.build(case.scenario, scenarios.Context()).steps[0].message
    perfect = json.dumps(
        {
            "mti": truth.mti,
            "transaction": "cash withdrawal",
            "amount_minor_units": int(
                truth.fields["4"] if "4" in truth.fields else truth.fields[4]
            ),
            "currency": truth.get(49),
            "trace": truth.get(11),
        }
    )
    result = g4.grade_decode(case, perfect, "t", "n")
    assert result.score == 1.0
    half = json.loads(perfect)
    half["amount_minor_units"] = 1
    half["currency"] = "840"
    worse = g4.grade_decode(case, json.dumps(half), "t", "n")
    assert worse.score < 1.0
    assert set(worse.columns["wrong"]) == {"amount_minor_units", "currency"}


# =================================================== 05 policy from prose


def test_05_every_reference_compiles_and_scores_perfectly() -> None:
    for case in t5.POLICY_CASES:
        result = g5.grade_compile(case, case.reference, "reference", "none")
        assert result.compiles, case.key
        assert result.identical, case.key
        assert result.agreement == 1.0, case.key
        assert result.score == 1.0


def test_05_every_template_notice_passes_every_check() -> None:
    # The grader must not mark down the known-correct answer. This caught two real bugs
    # in it: an approval's own reasons counted as invented, and a keyword collision
    # between the Urdu for a repaid loan and the pattern for a default.
    for case in t5.NOTICE_CASES:
        result = g5.baseline_notice(case)
        assert result.score == 1.0, f"{case.key}: {result.detail}"
        assert result.safe


def test_05_a_policy_that_does_not_compile_scores_zero_and_names_the_line() -> None:
    result = g5.grade_compile(t5.POLICY_CASES[0], 'policy "x"\nnonsense here\n', "t", "n")
    assert result.compiles is False
    assert result.score == 0.0
    assert "line" in result.error


def test_05_a_policy_that_compiles_but_decides_differently_is_caught() -> None:
    # The output this project exists for: it parses, it reads sensibly, and it approves a
    # different set of applicants.
    case = next(case for case in t5.POLICY_CASES if case.key == "starter")
    drifted = case.reference.replace("monthly_income < 25000", "monthly_income < 20000")
    result = g5.grade_compile(case, drifted, "t", "n")
    assert result.compiles is True
    assert result.identical is False
    assert 0 < result.agreement < 1
    assert result.columns["decisions_differing"] > 0
    assert result.columns["approvals_model"] != result.columns["approvals_reference"]


def test_05_a_notice_that_invents_a_reason_is_flagged() -> None:
    case = next(case for case in t5.NOTICE_CASES if case.key == "gate-default-ur")
    bad = "Aap ki maheena aamdani bohat kam hai is liye manzoori nahi hui."
    result = g5.grade_notice(case, bad, "t", "n")
    assert result.checks["no_invented_reason"] is False
    assert result.safe is False
    assert "monthly_income" in result.detail


def test_05_a_notice_that_promises_approval_is_flagged() -> None:
    case = next(case for case in t5.NOTICE_CASES if case.key == "gate-income-en")
    bad = (
        "Your application was not approved because your monthly income is below our floor. "
        "Apply again next month and you will be approved."
    )
    result = g5.grade_notice(case, bad, "t", "n")
    assert result.checks["no_promise"] is False
    assert result.safe is False


def test_05_an_urdu_case_answered_in_english_is_flagged() -> None:
    case = next(case for case in t5.NOTICE_CASES if case.language == "ur")
    result = g5.grade_notice(case, "Your application was declined due to a default.", "t", "n")
    assert result.checks["in_roman_urdu"] is False


def test_05_the_prose_describes_the_same_thresholds_as_the_reference() -> None:
    # If the prose and the reference disagree, the grader measures the discrepancy
    # rather than the model.
    starter = next(case for case in t5.POLICY_CASES if case.key == "starter")
    assert "25,000" in starter.prose or "25000" in starter.prose
    assert "monthly_income < 25000" in starter.reference


# ==================================================== 06 model vs lexicon


def test_06_uses_project_03s_own_cases() -> None:
    from projects.p03_guardian import testset

    cases = t6.cases(amount_limit=None)
    amounts = [case for case in cases if case.family == t6.AMOUNT]
    assert len(amounts) == len(testset.NUMERAL_CASES)
    assert len([c for c in cases if c.family == t6.ACTION]) == len(testset.ACTION_CASES)
    assert len([c for c in cases if c.family == t6.COERCION]) == len(testset.COERCION_CASES)


def test_06_the_rules_score_perfectly_and_miss_exactly_the_published_blind_spot() -> None:
    rows = [g6.rules(case) for case in t6.cases(amount_limit=None)]
    summary = g6.summarise(rows)
    assert summary[t6.AMOUNT]["accuracy"] == 1.0
    assert summary[t6.ACTION]["accuracy"] == 1.0
    assert summary[t6.COERCION]["accuracy"] == 1.0
    # The point of the comparison: the rules catch none of the ones the words cannot
    # see, by construction. The count was four and is three - the purchase-scam family
    # added in the dataset round reads the property-token case - and the number is taken
    # from the test set rather than written here, so the next one that moves is not a
    # test failure waiting to be edited.
    expected = sum(1 for case in testset.COERCION_CASES if case[3])
    assert expected == 3
    assert summary[t6.COERCION]["cases_the_lexicon_misses"] == expected
    assert summary[t6.COERCION]["caught_of_those"] == 0
    assert summary[t6.COERCION]["ordinary_traffic_blocked"] == 0


def test_06_the_taught_arm_adds_the_vocabulary_and_nothing_else() -> None:
    case = next(case for case in t6.cases() if case.family == t6.AMOUNT)
    bare, taught = t6.prompt(case, t6.BARE), t6.prompt(case, t6.TAUGHT)
    assert bare in taught, "taught must be the same prompt plus context"
    assert "saath = 60" in taught and "saath = 60" not in bare


def test_06_an_amount_off_by_a_factor_is_recorded_as_such() -> None:
    case = next(case for case in t6.cases() if case.text == "paanch hazaar")
    result = g6.grade_amount(case, json.dumps({"amount": 50000}), "t", "n")
    assert result.correct is False
    assert result.columns["factor_out"] == 10.0


def test_06_a_correct_amount_in_any_shape_is_accepted() -> None:
    case = next(case for case in t6.cases() if case.text == "paanch hazaar")
    for reply in ('{"amount": 5000}', '{"amount": "5,000"}', '{"amount": 5000.0}'):
        assert g6.grade_amount(case, reply, "t", "n").correct, reply


def test_06_an_unparseable_reply_is_wrong_not_skipped() -> None:
    # A reply that cannot be parsed cannot drive a transfer, and dropping those cases
    # would grade the subset that happened to be formatted correctly.
    case = next(case for case in t6.cases() if case.family == t6.ACTION)
    result = g6.grade_action(case, "Sure! I will send that for you.", "t", "n")
    assert result.parsed is False
    assert result.correct is False


def test_06_resolving_an_underspecified_utterance_is_wrong() -> None:
    case = next(
        case
        for case in t6.cases()
        if case.family == t6.ACTION and isinstance(case.expected, dict) and "ask" in case.expected
    )
    guessed = json.dumps(
        {"intent": "send_money", "amount": 5000, "beneficiary": "Imran Shah", "ask": None}
    )
    result = g6.grade_action(case, guessed, "t", "n")
    assert result.correct is False
    assert "instead of asking" in result.detail

    asked = json.dumps({"intent": None, "amount": None, "beneficiary": None, "ask": "how much?"})
    assert g6.grade_action(case, asked, "t", "n").correct is True


def test_06_asking_about_a_determined_utterance_is_also_wrong() -> None:
    case = next(
        case
        for case in t6.cases()
        if case.family == t6.ACTION
        and not (isinstance(case.expected, dict) and "ask" in case.expected)
    )
    result = g6.grade_action(case, json.dumps({"ask": "which one?"}), "t", "n")
    assert result.correct is False
    assert "asked when the action was determined" in result.detail


def test_06_a_coercion_verdict_is_compared_exactly() -> None:
    case = next(
        case for case in t6.cases() if case.family == t6.COERCION and case.expected == "block"
    )
    assert g6.grade_coercion(case, json.dumps({"verdict": "block", "why": "otp"}), "t", "n").correct
    wrong = g6.grade_coercion(case, json.dumps({"verdict": "allow", "why": "fine"}), "t", "n")
    assert wrong.correct is False
    assert "expected 'block'" in wrong.detail


def test_06_the_blind_spot_cases_are_marked_in_the_rows() -> None:
    blind = [case for case in t6.cases() if case.family == t6.COERCION and case.note]
    assert len(blind) == sum(1 for case in testset.COERCION_CASES if case[3])
    for case in blind:
        row = g6.rules(case)
        assert row.columns["lexicon_misses_this"] is True
        assert row.columns["blocked"] is False


# ========================================================= the benchmarks


@pytest.mark.parametrize(
    "module",
    [
        "projects.p04_spec_to_wire.benchmark",
        "projects.p05_policy_from_prose.benchmark",
        "projects.p06_model_vs_lexicon.benchmark",
    ],
)
def test_a_benchmark_without_a_model_reports_what_it_did_not_run(module: str) -> None:
    """Cache-only must never invent a score for a prompt it never sent."""
    import importlib

    benchmark = importlib.import_module(module)
    results = benchmark.run(models=("not-a-real-model",), live=False)
    assert results["incomplete"], "an unasked prompt must be reported, not scored"
    assert results["usage"]["calls"] == 0
    # And the deterministic baseline is still there, because it needs no model.
    baseline = results.get("baseline") or results.get("rules")
    assert baseline


def test_the_committed_results_files_exist_and_name_their_models() -> None:
    import importlib

    for module in (
        "projects.p04_spec_to_wire.benchmark",
        "projects.p05_policy_from_prose.benchmark",
        "projects.p06_model_vs_lexicon.benchmark",
    ):
        benchmark = importlib.import_module(module)
        assert benchmark.RESULTS.is_file(), module
        data = json.loads(benchmark.RESULTS.read_text(encoding="utf-8"))
        assert data["setup"]["models"], module
        assert "usage" in data


def test_06_a_matching_amount_is_not_reported_as_unexpected() -> None:
    """Lock the branch a lint fix once broke.

    Collapsing ``if "amount" in expected:`` plus its inner check into one condition
    re-binds the following ``elif "amount" in got:`` to the combined expression, so a
    *correct* amount falls through and is reported as "gave an amount where there is
    none". That turned 14 of 30 correct answers into failures.
    """
    case = next(
        case
        for case in t6.cases()
        if case.family == t6.ACTION
        and isinstance(case.expected, dict)
        and "amount" in case.expected
    )
    expected = case.expected
    assert isinstance(expected, dict)
    reply = json.dumps(
        {
            "intent": expected["intent"],
            "amount": expected["amount"],
            "beneficiary": expected.get("beneficiary"),
            "target_number": expected.get("target_number"),
            "biller": expected.get("biller"),
            "ask": None,
        }
    )
    result = g6.grade_action(case, reply, "t", "n")
    assert result.correct, result.detail
    assert "where there is none" not in result.detail


def test_06_an_amount_given_where_none_is_expected_is_still_caught() -> None:
    # The other direction of the same branch, so the fix cannot be undone silently.
    case = next(
        case
        for case in t6.cases()
        if case.family == t6.ACTION
        and isinstance(case.expected, dict)
        and case.expected.get("intent") == "balance"
    )
    reply = json.dumps({"intent": "balance", "amount": 500, "ask": None})
    result = g6.grade_action(case, reply, "t", "n")
    assert result.correct is False
    assert "where there is none" in result.detail
