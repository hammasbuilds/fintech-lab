"""The policy language, the evaluator, the backtest arithmetic and the lint."""

from __future__ import annotations

from pathlib import Path

import pytest

from projects.p02_policy_bench import backtest, cohort, compliance, dsl, engine, explain, fairness

POLICIES = Path(dsl.__file__).with_name("policies")

MINIMAL = """
policy "Test"
version 1
currency PKR

decline if age < 18 because "below the legal age"
score +50 if monthly_income >= 20000 because "income above the floor"
approve if score >= 50
limit min(0.25 * monthly_income, 20000)
fee 2% of limit
"""


def compile_minimal() -> dsl.Policy:
    return dsl.compile_policy(MINIMAL)


# -------------------------------------------------------------------- parsing


def test_every_bundled_policy_compiles() -> None:
    assert sorted(path.stem for path in POLICIES.glob("*.policy")) == [
        "nano_v1",
        "nano_v2",
        "nano_v3_timepriced",
    ]
    for path in POLICIES.glob("*.policy"):
        policy = dsl.compile_policy(path.read_text(encoding="utf-8"))
        assert policy.name
        assert policy.approve_at is not None
        assert policy.limit is not None


def test_a_policy_round_trips_through_its_own_printer() -> None:
    # If the text and the tree diverge, the document the credit team argues over is no
    # longer the thing that runs, which is the one guarantee this language is for.
    for path in POLICIES.glob("*.policy"):
        policy = dsl.compile_policy(path.read_text(encoding="utf-8"))
        rendered = policy.render()
        assert dsl.compile_policy(rendered).render() == rendered


def test_comments_and_blank_lines_are_ignored() -> None:
    policy = dsl.compile_policy("# a note\n\n" + MINIMAL)
    assert policy.name == "Test"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ('policy "x"\nversion 1\ncurrency PKR\nlimit 100\n', "never says when to approve"),
        ('policy "x"\napprove if score >= 10\n', "never sets a limit"),
        (
            'policy "x"\nscore +5 if age >= 18\napprove if score >= 50\nlimit 100\n',
            "at most 5 can be awarded",
        ),
        (
            'policy "x"\napprove if score >= 10\nlimit 100\n'
            "score +20 if age >= 18\napprove if score >= 5\n",
            "already set",
        ),
        ('policy "x"\ndecline if age < 18\n', "expected because"),
        ('policy "x"\ndecline if age 18 because "no"\n', "expected one of"),
        ('policy "x"\ndecline if age <> 18 because "no"\n', "expected a number or a quoted value"),
        (
            'policy "x"\nscore 5 if age >= 18 because "x"\napprove if score <= 5\nlimit 10\n',
            "uses >= or >",
        ),
        ('policy "x"\napprove if age >= 5\nlimit 10\n', "compares the score"),
        (
            'policy "x"\nscore +5 if age >= "old" because "x"\napprove if score >= 5\nlimit 1\n',
            "cannot compare text",
        ),
        ('policy "x"\nfee of limit\n', "takes a percentage"),
        ('policy "x"\nlimit min(1)\n', "expected ,"),
    ],
)
def test_a_broken_policy_says_what_is_wrong_and_where(source: str, message: str) -> None:
    with pytest.raises(dsl.PolicyError, match=message) as caught:
        dsl.compile_policy(source)
    assert caught.value.line >= 1


def test_a_policy_that_can_never_approve_is_refused_at_compile_time() -> None:
    with pytest.raises(dsl.PolicyError, match="nothing can ever be approved"):
        dsl.compile_policy(
            'policy "x"\nscore +10 if age >= 18 because "a"\napprove if score >= 90\nlimit 100\n'
        )


def test_conditions_combine_with_and_or() -> None:
    policy = dsl.compile_policy(
        'policy "x"\n'
        'score +50 if monthly_income >= 100 and wallet_months >= 2 because "both"\n'
        'score +10 if region == "central-urban" or region == "north-urban" because "either"\n'
        "approve if score >= 50\nlimit 100\n"
    )
    both = policy.score_rules[0].condition
    assert isinstance(both, dsl.Combination) and both.op == "and"
    either = policy.score_rules[1].condition
    assert isinstance(either, dsl.Combination) and either.op == "or"
    record = {"monthly_income": 100, "wallet_months": 2, "region": "north-urban"}
    decision = engine.evaluate(policy, record)
    assert decision.score == 60


def test_fields_used_lists_what_the_policy_reads() -> None:
    assert compile_minimal().fields_used == ["age", "monthly_income"]


# ------------------------------------------------------------------ evaluation


def test_a_gate_short_circuits_before_scoring() -> None:
    decision = engine.evaluate(compile_minimal(), {"age": 16, "monthly_income": 90000})
    assert decision.outcome == "decline"
    assert decision.gate is not None
    assert decision.score == 0
    assert decision.score_lines == []  # no score was computed, so none is reported
    assert decision.reasons == ["below the legal age"]


def test_an_approval_carries_its_limit_and_fee() -> None:
    decision = engine.evaluate(compile_minimal(), {"age": 30, "monthly_income": 40000})
    assert decision.approved
    assert decision.limit == 10000
    assert decision.charges == [
        {
            "kind": "fee",
            "amount": 200.0,
            "basis": "fee 2% of limit",
            "period": None,
            "periods_charged": None,
        }
    ]


def test_the_limit_is_capped_by_min() -> None:
    decision = engine.evaluate(compile_minimal(), {"age": 30, "monthly_income": 900000})
    assert decision.limit == 20000


def test_a_decline_reports_the_shortfall_and_what_was_missed() -> None:
    decision = engine.evaluate(compile_minimal(), {"age": 30, "monthly_income": 1000})
    assert not decision.approved
    assert "50 this policy requires" in decision.reasons[0]
    assert any(reason.startswith("missed:") for reason in decision.reasons)


def test_a_missing_field_is_an_error_not_a_silent_false() -> None:
    # Treating an absent field as "condition not met" would make a typo in a policy look
    # like a working rule that never fires.
    with pytest.raises(engine.EvaluationError, match="no field 'monthly_income'"):
        engine.evaluate(compile_minimal(), {"age": 30})


def test_a_text_field_cannot_be_compared_with_a_number() -> None:
    policy = dsl.compile_policy(
        'policy "x"\nscore +50 if employment >= 3 because "x"\napprove if score >= 50\nlimit 10\n'
    )
    with pytest.raises(engine.EvaluationError, match="cannot be compared"):
        engine.evaluate(policy, {"employment": "salaried"})


def test_refer_is_distinct_from_decline() -> None:
    policy = dsl.compile_policy(
        'policy "x"\nrefer if dependants >= 6 because "large household"\n'
        'score +50 if age >= 18 because "adult"\napprove if score >= 50\nlimit 100\n'
    )
    decision = engine.evaluate(policy, {"dependants": 7, "age": 40})
    assert decision.outcome == "refer"
    assert not decision.approved


# --------------------------------------------------------------------- cohort


def test_the_cohort_is_reproducible_and_withholds_its_label() -> None:
    first = cohort.generate(size=300, seed=5)
    second = cohort.generate(size=300, seed=5)
    assert [person.id for person in first] == [person.id for person in second]
    assert first[7] == second[7]
    record = first[0].record()
    assert "would_default" not in record
    assert "id" not in record


def test_the_cohort_has_the_structure_the_sandbox_needs() -> None:
    summary = cohort.summary(cohort.generate(size=3000, seed=9))
    assert 0.02 < summary["base_default_rate"] < 0.20
    regions = summary["by_region"]
    assert len(regions) == 4
    urban = regions["central-urban"]["median_income"]
    rural = regions["south-rural"]["median_income"]
    assert rural < urban, (
        "region must correlate with income for the fairness panel to mean anything"
    )


def test_an_empty_cohort_summarises_without_dividing_by_zero() -> None:
    assert cohort.summary([]) == {"size": 0}


# ------------------------------------------------------------------- backtest


def test_backtest_totals_add_up() -> None:
    people = cohort.generate(size=1000, seed=4)
    policy = dsl.compile_policy((POLICIES / "nano_v2.policy").read_text(encoding="utf-8"))
    report, outcomes = backtest.run(policy, people)
    assert report.applications == len(people) == len(outcomes)
    assert report.approvals + report.refers + report.declines == report.applications
    assert report.declined_who_would_repay + report.declined_who_would_default == (
        report.refers + report.declines
    )
    assert 0 <= report.approval_rate <= 1
    assert report.disbursed >= 0


def test_the_score_histogram_excludes_gated_applicants() -> None:
    # A gated applicant never reached the scorecard, so putting them in a zero bucket
    # would invent a score nobody computed.
    people = cohort.generate(size=800, seed=6)
    policy = dsl.compile_policy((POLICIES / "nano_v2.policy").read_text(encoding="utf-8"))
    report, outcomes = backtest.run(policy, people)
    scored = sum(1 for outcome in outcomes if outcome.decision.gate is None)
    assert sum(int(row["count"]) for row in report.score_histogram) == scored


def test_a_bucket_split_by_the_threshold_says_so() -> None:
    people = cohort.generate(size=1500, seed=6)
    policy = dsl.compile_policy((POLICIES / "nano_v2.policy").read_text(encoding="utf-8"))
    report, _ = backtest.run(policy, people)
    standings = {row["standing"] for row in report.score_histogram}
    assert "split by the threshold" in standings
    for row in report.score_histogram:
        assert 0 <= int(row["approved"]) <= int(row["count"])


def test_comparing_a_policy_with_itself_changes_nothing() -> None:
    people = cohort.generate(size=500, seed=7)
    policy = dsl.compile_policy((POLICIES / "nano_v1.policy").read_text(encoding="utf-8"))
    result = backtest.compare(policy, policy, people).as_dict()
    assert result["newly_approved"] == 0
    assert result["newly_declined"] == 0
    assert result["unchanged"] == len(people)
    assert result["precision_of_the_tightening"] is None


def test_a_tightening_reports_who_it_removed_and_whether_they_were_bad() -> None:
    people = cohort.generate(size=2000, seed=8)
    v1 = dsl.compile_policy((POLICIES / "nano_v1.policy").read_text(encoding="utf-8"))
    v2 = dsl.compile_policy((POLICIES / "nano_v2.policy").read_text(encoding="utf-8"))
    result = backtest.compare(v1, v2, people).as_dict()
    assert result["newly_declined"] > 0
    assert (
        result["newly_declined_who_would_repay"] + result["newly_declined_who_would_default"]
        == result["newly_declined"]
    )
    assert 0 <= result["precision_of_the_tightening"] <= 1
    assert result["delta"]["approval_rate_points"] < 0


def test_a_fee_only_change_moves_no_credit_decision() -> None:
    people = cohort.generate(size=800, seed=11)
    v2 = dsl.compile_policy((POLICIES / "nano_v2.policy").read_text(encoding="utf-8"))
    v3 = dsl.compile_policy((POLICIES / "nano_v3_timepriced.policy").read_text(encoding="utf-8"))
    result = backtest.compare(v2, v3, people).as_dict()
    assert result["newly_approved"] == 0
    assert result["newly_declined"] == 0


def test_time_priced_revenue_is_counted_apart_from_fees() -> None:
    people = cohort.generate(size=400, seed=12)
    v3 = dsl.compile_policy((POLICIES / "nano_v3_timepriced.policy").read_text(encoding="utf-8"))
    report, _ = backtest.run(v3, people)
    assert report.time_priced_revenue > 0
    assert report.net == report.fee_revenue - report.expected_loss


# ----------------------------------------------------------------------- lint


def test_a_time_priced_charge_is_flagged_as_blocking() -> None:
    policy = dsl.compile_policy(
        (POLICIES / "nano_v3_timepriced.policy").read_text(encoding="utf-8")
    )
    verdict = compliance.verdict(compliance.review(policy))
    assert verdict["blocking"] is True
    assert any(note["check"] == "C1" for note in verdict["notes"])


def test_the_bundled_policies_without_a_time_charge_are_clean() -> None:
    for name in ("nano_v1", "nano_v2"):
        policy = dsl.compile_policy((POLICIES / f"{name}.policy").read_text(encoding="utf-8"))
        assert compliance.review(policy) == []


def test_a_protected_attribute_in_a_rule_is_flagged() -> None:
    policy = dsl.compile_policy(
        'policy "x"\ndecline if region == "south-rural" because "no"\n'
        'score +50 if age >= 18 because "adult"\napprove if score >= 50\nlimit min(1, 2)\n'
    )
    notes = compliance.review(policy)
    assert any(note.check == "C2" and note.severity == "critical" for note in notes)


def test_an_uncapped_limit_is_flagged() -> None:
    policy = dsl.compile_policy(
        'policy "x"\nscore +50 if age >= 18 because "adult"\napprove if score >= 50\n'
        "limit 0.5 * monthly_income\n"
    )
    assert any(note.check == "C5" for note in compliance.review(policy))


def test_a_positive_rule_with_no_reason_is_flagged() -> None:
    policy = dsl.compile_policy(
        'policy "x"\nscore +50 if age >= 18\napprove if score >= 50\nlimit min(1, 2)\n'
    )
    assert any(note.check == "C4" for note in compliance.review(policy))


def test_a_threshold_needing_nearly_every_point_is_flagged() -> None:
    policy = dsl.compile_policy(
        'policy "x"\nscore +50 if age >= 18 because "a"\nscore +10 if dependants >= 1 because "b"\n'
        "approve if score >= 55\nlimit min(1, 2)\n"
    )
    assert any(note.check == "C8" for note in compliance.review(policy))


def test_the_lint_states_its_own_scope() -> None:
    # Passing eight structural checks is not compliance, and the output must not imply it.
    verdict = compliance.verdict([])
    assert verdict["checks_run"] == len(compliance.CHECKS)
    assert "not that the policy is compliant" in verdict["scope"]


# ------------------------------------------------------------------- fairness


def test_fairness_reports_the_gap_and_the_gap_within_income_bands() -> None:
    people = cohort.generate(size=4000, seed=21)
    policy = dsl.compile_policy((POLICIES / "nano_v2.policy").read_text(encoding="utf-8"))
    _, outcomes = backtest.run(policy, people)
    result = fairness.analyse(outcomes, "region")
    assert result["raw_gap"] > 0
    assert result["within_band_gap_mean"] is not None
    assert result["within_band_gap_mean"] < result["raw_gap"]
    assert "income" in result["reading"]
    assert "not a compliance opinion" in result["not_a_verdict"]


def test_fairness_withholds_a_figure_rather_than_computing_it_on_a_handful() -> None:
    people = cohort.generate(size=60, seed=22)
    policy = dsl.compile_policy((POLICIES / "nano_v2.policy").read_text(encoding="utf-8"))
    _, outcomes = backtest.run(policy, people)
    result = fairness.analyse(outcomes, "region", min_cell=200)
    assert "withheld" in result
    assert "raw_gap" not in result


def test_a_policy_with_no_disparity_is_reported_as_such() -> None:
    people = cohort.generate(size=2000, seed=23)
    policy = dsl.compile_policy(
        'policy "x"\nscore +50 if age >= 0 because "everyone"\napprove if score >= 50\n'
        "limit min(1000, 1000)\n"
    )
    _, outcomes = backtest.run(policy, people)
    result = fairness.analyse(outcomes, "region")
    assert result["raw_gap"] == 0
    assert "little disparity" in result["reading"]


# --------------------------------------------------------------- explanations


def test_a_decline_notice_marks_unmet_requirements_as_unmet() -> None:
    # Listing them beside an approval's reasons produces "not approved because your
    # income is above the floor", which is the opposite of what happened.
    decision = engine.evaluate(compile_minimal(), {"age": 30, "monthly_income": 1000})
    for language in ("en", "ur"):
        notice = explain.notice(decision, "PKR", language)
        assert notice["outcome"] == "decline"
        assert notice["requirements_not_met"]
        assert all(item["met"] is False for item in notice["requirements_not_met"])
        assert notice["not_met_label"]
        assert notice["rights"]


def test_a_gate_notice_quotes_the_gate_reason() -> None:
    decision = engine.evaluate(compile_minimal(), {"age": 15, "monthly_income": 90000})
    assert explain.notice(decision, "PKR", "en")["because"] == ["below the legal age"]


def test_an_approval_notice_states_the_limit_in_both_languages() -> None:
    decision = engine.evaluate(compile_minimal(), {"age": 30, "monthly_income": 40000})
    assert "10,000" in explain.notice(decision, "PKR", "en")["headline"]
    assert "10,000" in explain.notice(decision, "PKR", "ur")["headline"]


def test_every_cohort_field_has_an_urdu_phrase() -> None:
    # A missing translation must look missing rather than fluent, and right now none are.
    assert explain.untranslated_fields() == []


def test_a_compound_condition_is_translated_part_by_part() -> None:
    translated = explain._translate_line("monthly_income >= 18000 and wallet_months >= 6")
    assert " aur " in translated
    assert "aamdani" in translated
    assert "wallet" in translated


PRICED = """
policy "Priced per month"
version 1
currency XXX

decline if age < 18 because "below the legal age"

score +50 if months_employed >= 6 because "half a year in work"

approve if score >= 50
limit min(0.25 * monthly_income, 20000)
term loan_term_months months
interest 1.5% of limit per month
"""

TERMLESS = """
policy "Per day, no term"
version 1
currency PKR

decline if age < 18 because "below the legal age"

score +50 if months_employed >= 6 because "half a year in work"

approve if score >= 50
limit min(0.25 * monthly_income, 20000)
late_fee 1% of limit per day
"""


def test_interest_is_charged_once_per_month_of_the_stated_term() -> None:
    policy = dsl.compile_policy(PRICED)
    applicant = {"age": 30, "monthly_income": 40000, "months_employed": 24, "loan_term_months": 12}
    decision = engine.evaluate(policy, applicant)
    assert decision.limit == 10000
    charge = decision.charges[0]
    # 1.5% of 10,000 is 150 a month, and the term says twelve of them.
    assert charge["amount"] == 1800.0
    assert charge["periods_charged"] == 12
    assert charge["period"] == "month"


def test_the_term_can_differ_per_applicant_because_it_is_a_field() -> None:
    policy = dsl.compile_policy(PRICED)
    base = {"age": 30, "monthly_income": 40000, "months_employed": 24}
    short = engine.evaluate(policy, base | {"loan_term_months": 6})
    long = engine.evaluate(policy, base | {"loan_term_months": 60})
    assert short.charges[0]["amount"] == 900.0
    assert long.charges[0]["amount"] == 9000.0


def test_a_per_period_charge_without_a_term_is_still_counted_once() -> None:
    """The policies written before ``term`` existed must earn exactly what they did."""
    policy = dsl.compile_policy(TERMLESS)
    decision = engine.evaluate(policy, {"age": 30, "monthly_income": 40000, "months_employed": 24})
    charge = decision.charges[0]
    assert charge["amount"] == 100.0
    assert charge["periods_charged"] is None


def test_interest_without_a_term_is_refused_at_compile_time() -> None:
    source = PRICED.replace("term loan_term_months months", "")
    with pytest.raises(dsl.PolicyError) as caught:
        dsl.compile_policy(source)
    assert "never says for how many" in str(caught.value)


def test_interest_must_be_priced_per_period() -> None:
    source = PRICED.replace("interest 1.5% of limit per month", "interest 1.5% of limit")
    with pytest.raises(dsl.PolicyError) as caught:
        dsl.compile_policy(source)
    assert "per unit of time" in str(caught.value)


def test_a_term_is_stated_in_months() -> None:
    source = PRICED.replace("term loan_term_months months", "term loan_term_months weeks")
    with pytest.raises(dsl.PolicyError) as caught:
        dsl.compile_policy(source)
    assert "stated in months" in str(caught.value)


def test_a_priced_policy_round_trips_through_its_own_text() -> None:
    policy = dsl.compile_policy(PRICED)
    assert dsl.compile_policy(policy.render()).render() == policy.render()
    assert "term loan_term_months months" in policy.render()
    assert "loan_term_months" in policy.fields_used
