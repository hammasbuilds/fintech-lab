"""Discrimination, the cutoff sweep, and cohorts supplied as CSV."""

from __future__ import annotations

from pathlib import Path

import pytest

from projects.p02_policy_bench import backtest, cohort, dsl, sweep

POLICIES = Path(dsl.__file__).with_name("policies")


def policy(name: str = "nano_v2") -> dsl.Policy:
    return dsl.compile_policy((POLICIES / f"{name}.policy").read_text(encoding="utf-8"))


# ------------------------------------------------------------ discrimination


def test_discrimination_is_measured_only_on_those_who_were_scored() -> None:
    # Including gated applicants would be measuring the gates and calling it the
    # scorecard, so the exclusion is reported rather than hidden.
    people = cohort.generate(size=3000, seed=31)
    scored = sweep.score_all(policy(), people)
    result = sweep.discrimination(scored)
    assert result["scored"] + result["gated_out"] == len(people)
    assert result["gated_out"] > 0
    assert result["bads"] + result["goods"] == result["scored"]
    assert "excluded" in result["caveat"]


def test_gini_auc_and_ks_are_in_range_and_consistent() -> None:
    people = cohort.generate(size=3000, seed=32)
    result = sweep.discrimination(sweep.score_all(policy(), people))
    assert 0.0 <= result["auc"] <= 1.0
    # Both are rounded to 4 places from the unrounded AUC, so the identity holds to
    # within the rounding of each, not exactly.
    assert result["gini"] == pytest.approx(2 * result["auc"] - 1, abs=3e-4)
    assert 0.0 <= result["ks"] <= 1.0
    assert result["reading"]


def test_a_score_that_ranks_backwards_is_called_out() -> None:
    # A policy whose points reward the risky side should produce a negative Gini, and the
    # reading has to say so rather than printing a small positive-looking number.
    backwards = dsl.compile_policy(
        'policy "backwards"\n'
        'score +60 if prior_defaults >= 1 because "rewards the risky side"\n'
        'score +10 if dependants >= 3 because "and again"\n'
        "approve if score >= 60\n"
        "limit min(5000, 5000)\n"
    )
    people = cohort.generate(size=3000, seed=33)
    result = sweep.discrimination(sweep.score_all(backwards, people))
    assert result["gini"] < 0
    assert "wrong way round" in result["reading"]


def test_discrimination_is_withheld_when_there_is_nothing_to_rank() -> None:
    people = [b for b in cohort.generate(size=600, seed=34) if not b.would_default][:50]
    result = sweep.discrimination(sweep.score_all(policy(), people))
    assert "withheld" in result
    assert "auc" not in result


# -------------------------------------------------------------------- sweep


def test_the_sweep_covers_the_whole_score_range() -> None:
    people = cohort.generate(size=2000, seed=35)
    result = sweep.sweep(policy(), people, step=5)
    thresholds = [point["threshold"] for point in result["points"]]
    assert thresholds == sorted(thresholds)
    assert len(thresholds) > 5
    assert (
        result["points"][0]["approval_rate_of_scored"]
        >= result["points"][-1]["approval_rate_of_scored"]
    )


def test_approval_rate_is_reported_against_both_denominators() -> None:
    # The cutoff only controls the scored population; the rest of the project reports a
    # share of all applicants. One unlabelled "approval rate" across the two is how a
    # 63% and a 32% end up in the same sentence.
    people = cohort.generate(size=2000, seed=36)
    result = sweep.sweep(policy(), people, step=10)
    assert result["applications"] == len(people)
    for point in result["points"]:
        assert point["approval_rate_of_all"] <= point["approval_rate_of_scored"]
    scored_share = result["scored_population"] / result["applications"]
    top = result["points"][0]
    assert top["approval_rate_of_all"] == pytest.approx(
        top["approval_rate_of_scored"] * scored_share, abs=0.01
    )


def test_the_sweep_agrees_with_the_backtest_at_the_policy_cutoff() -> None:
    people = cohort.generate(size=2000, seed=37)
    chosen = policy()
    report, _ = backtest.run(chosen, people)
    result = sweep.sweep(chosen, people, step=5)
    assert result["current"]["threshold"] == chosen.approve_at
    assert result["current"]["approvals"] == report.approvals
    assert result["current"]["approval_rate_of_all"] == pytest.approx(
        report.approval_rate, abs=1e-3
    )


def test_the_net_maximising_cutoff_carries_its_in_sample_warning() -> None:
    people = cohort.generate(size=2000, seed=38)
    result = sweep.sweep(policy(), people, step=5)
    best = max(result["points"], key=lambda point: point["net"])
    assert result["net_maximising"]["threshold"] == best["threshold"]
    assert "in-sample" in result["in_sample_warning"] or "optimistic" in result["in_sample_warning"]
    assert result["net_gain_available"] >= 0


def test_declines_are_split_by_what_they_would_have_done() -> None:
    people = cohort.generate(size=1500, seed=39)
    result = sweep.sweep(policy(), people, step=10)
    for point in result["points"]:
        assert (
            point["good_declined"] + point["bad_declined"] + point["approvals"]
            == (result["scored_population"])
        )


def test_a_policy_that_gates_everyone_withholds_the_sweep() -> None:
    gated = dsl.compile_policy(
        'policy "all gated"\ndecline if age >= 0 because "everyone"\n'
        'score +50 if age >= 18 because "unreachable"\napprove if score >= 50\n'
        "limit min(1, 1)\n"
    )
    result = sweep.sweep(gated, cohort.generate(size=200, seed=40))
    assert "withheld" in result


# ------------------------------------------------------- supplied cohorts


def test_a_cohort_round_trips_through_csv() -> None:
    people = cohort.generate(size=120, seed=41)
    text = cohort.to_csv(people)
    back, meta = cohort.from_csv(text)
    assert back == people
    assert meta["rows"] == 120
    assert meta["labelled"] is True
    assert meta["unknown_columns"] == []


def test_a_cohort_without_an_outcome_column_is_detected() -> None:
    people = cohort.generate(size=60, seed=42)
    back, meta = cohort.from_csv(cohort.to_csv(people, include_label=False))
    assert meta["labelled"] is False
    assert all(borrower.would_default == 0 for borrower in back)
    assert "withheld" in meta["note"]


def test_figures_needing_an_outcome_are_withheld_not_computed_against_zeros() -> None:
    # The whole risk of accepting a cohort without labels is reporting a 0% bad rate.
    people = cohort.generate(size=400, seed=43)
    report, _ = backtest.run(policy(), people, labelled=False)
    data = report.as_dict()
    assert data["labelled"] is False
    assert data["withheld"]
    for key in (
        "bad_rate",
        "loss_rate",
        "expected_loss",
        "net",
        "defaults_among_approved",
        "declined_who_would_repay",
        "declined_who_would_default",
    ):
        assert data[key] is None, key
    # The figures that do not need an outcome are still there.
    assert data["approvals"] > 0
    assert data["disbursed"] > 0
    assert data["approval_rate"] > 0


def test_a_comparison_on_an_unlabelled_cohort_withholds_its_precision() -> None:
    people = cohort.generate(size=600, seed=44)
    result = backtest.compare(
        policy("nano_v1"), policy("nano_v2"), people, labelled=False
    ).as_dict()
    assert result["newly_declined"] > 0
    assert result["precision_of_the_tightening"] is None
    assert result["newly_declined_who_would_repay"] is None
    assert result["delta"]["bad_rate_points"] is None
    assert result["delta"]["net"] is None
    # The part that needs no outcome survives.
    assert result["delta"]["approval_rate_points"] < 0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no header row"),
        ("a,b\n1,2", "required and missing"),
        ("id,age\n1,2", "required and missing"),
    ],
)
def test_a_bad_csv_says_what_is_wrong(text: str, message: str) -> None:
    with pytest.raises(cohort.CohortError, match=message):
        cohort.from_csv(text)


def test_a_header_with_no_rows_is_refused() -> None:
    header = cohort.to_csv(cohort.generate(size=1, seed=45)).splitlines()[0]
    with pytest.raises(cohort.CohortError, match="no rows"):
        cohort.from_csv(header + "\n")


def test_a_non_numeric_cell_names_its_row_and_column() -> None:
    rows = cohort.to_csv(cohort.generate(size=3, seed=46)).splitlines()
    header = rows[0].split(",")
    index = header.index("monthly_income")
    cells = rows[1].split(",")
    cells[index] = "lots"
    rows[1] = ",".join(cells)
    with pytest.raises(cohort.CohortError, match="row 2.*monthly_income.*not a number"):
        cohort.from_csv("\n".join(rows))


def test_an_empty_cell_is_refused_rather_than_defaulted() -> None:
    rows = cohort.to_csv(cohort.generate(size=3, seed=47)).splitlines()
    header = rows[0].split(",")
    index = header.index("wallet_months")
    cells = rows[1].split(",")
    cells[index] = ""
    rows[1] = ",".join(cells)
    with pytest.raises(cohort.CohortError, match="is empty"):
        cohort.from_csv("\n".join(rows))


def test_a_label_that_is_not_zero_or_one_is_refused() -> None:
    rows = cohort.to_csv(cohort.generate(size=3, seed=48)).splitlines()
    cells = rows[1].split(",")
    cells[-1] = "maybe"
    rows[1] = ",".join(cells)
    with pytest.raises(cohort.CohortError, match="must be 0 or 1"):
        cohort.from_csv("\n".join(rows))


def test_extra_columns_are_reported_not_rejected() -> None:
    rows = cohort.to_csv(cohort.generate(size=3, seed=49)).splitlines()
    rows[0] += ",branch"
    for index in range(1, len(rows)):
        rows[index] += ",Gulberg"
    _, meta = cohort.from_csv("\n".join(rows))
    assert meta["unknown_columns"] == ["branch"]


def test_a_byte_order_mark_does_not_break_the_header() -> None:
    # A CSV saved from a spreadsheet on Windows starts with one, and a header check that
    # does not strip it reports every column as missing.
    text = "﻿" + cohort.to_csv(cohort.generate(size=5, seed=50))
    people, meta = cohort.from_csv(text)
    assert meta["rows"] == 5
    assert len(people) == 5


def test_a_supplied_cohort_can_be_back_tested() -> None:
    people, meta = cohort.from_csv(cohort.to_csv(cohort.generate(size=300, seed=51)))
    report, _ = backtest.run(policy(), people, labelled=meta["labelled"])
    assert report.applications == 300
    assert report.labelled is True
    assert report.bad_rate >= 0
