"""Compile every bundled policy, back-test it, compare the versions, write ``results.json``.

The headline the report is built around is the one a dashboard will not show you by
itself: a tightening's **precision** - of the applicants a new policy version newly
declines, what share would actually have defaulted. A tightening that improves the bad
rate while declining mostly good borrowers has not improved the policy, it has shrunk
it, and those two look identical in an approval-rate chart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import backtest, cohort, compliance, dsl, explain, fairness, sweep

HERE = Path(__file__).parent
POLICIES = HERE / "policies"
RESULTS = HERE / "results.json"


def load_policies() -> dict[str, dsl.Policy]:
    out: dict[str, dsl.Policy] = {}
    for path in sorted(POLICIES.glob("*.policy")):
        out[path.stem] = dsl.compile_policy(path.read_text(encoding="utf-8"))
    return out


def measure() -> dict[str, Any]:
    borrowers = cohort.generate()
    policies = load_policies()

    reports: dict[str, Any] = {}
    outcomes: dict[str, list[backtest.Outcome]] = {}
    for key, policy in policies.items():
        report, rows = backtest.run(policy, borrowers)
        reports[key] = report.as_dict()
        outcomes[key] = rows

    round_trips = {
        key: dsl.compile_policy(policy.render()).render() == policy.render()
        for key, policy in policies.items()
    }

    lint = {key: compliance.verdict(compliance.review(policy)) for key, policy in policies.items()}

    comparison = backtest.compare(policies["nano_v1"], policies["nano_v2"], borrowers).as_dict()
    fee_only = backtest.compare(
        policies["nano_v2"], policies["nano_v3_timepriced"], borrowers
    ).as_dict()

    equity = {
        attribute: fairness.analyse(outcomes["nano_v2"], attribute)
        for attribute in ("region", "employment")
    }

    scored = sweep.score_all(policies["nano_v2"], borrowers)
    discrimination = sweep.discrimination(scored)
    cutoff = sweep.sweep(policies["nano_v2"], borrowers)

    # The CSV path is exercised here rather than only in the tests, because a feature
    # that no measurement touches is a feature nobody notices breaking.
    exported = cohort.to_csv(borrowers[:200])
    reloaded, csv_meta = cohort.from_csv(exported)
    unlabelled, unlabelled_meta = cohort.from_csv(
        cohort.to_csv(borrowers[:200], include_label=False)
    )
    unlabelled_report, _ = backtest.run(policies["nano_v2"], unlabelled, labelled=False)

    return {
        "cohort": cohort.summary(borrowers),
        "policies": {
            key: {
                "name": policy.name,
                "version": str(policy.version),
                "gates": len(policy.gates),
                "score_rules": len(policy.score_rules),
                "approve_at": policy.approve_at,
                "fields_used": policy.fields_used,
                "round_trips": round_trips[key],
            }
            for key, policy in policies.items()
        },
        "backtests": reports,
        "lint": lint,
        "comparison_v1_to_v2": comparison,
        "comparison_v2_to_v3": fee_only,
        "fairness": equity,
        "discrimination": discrimination,
        "cutoff_sweep": cutoff,
        "supplied_cohorts": {
            "csv_round_trip": reloaded == borrowers[:200],
            "rows": csv_meta["rows"],
            "labelled_detected": csv_meta["labelled"],
            "unlabelled_detected": unlabelled_meta["labelled"],
            "withheld_without_a_label": [
                key
                for key, value in unlabelled_report.as_dict().items()
                if value is None and key != "withheld"
            ],
        },
        "explanations": {
            "untranslated_fields": explain.untranslated_fields(),
            "languages": ["en", "ur"],
        },
    }


def report(results: dict[str, Any]) -> str:
    c = results["cohort"]
    lines = [
        "Policy Bench - lending-policy sandbox",
        "",
        f"  cohort                       {c['size']} borrowers, synthetic and documented",
        f"  median income                {c['median_income']:,} PKR",
        f"  base default rate            {c['base_default_rate']:.2%}",
        "",
        "  policy                       gates  rules  approve  approvals   bad rate   lint",
    ]
    for key, meta in results["policies"].items():
        b = results["backtests"][key]
        verdict = results["lint"][key]
        counts = verdict["counts"]
        flag = (
            f"{counts['critical']} critical"
            if counts["critical"]
            else (f"{counts['serious']} serious" if counts["serious"] else "clean")
        )
        lines.append(
            f"  {key:26s}   {meta['gates']:>3}   {meta['score_rules']:>4}"
            f"    {meta['approve_at']:>5.0f}  {b['approval_rate']:>8.1%}"
            f"  {b['bad_rate']:>8.2%}   {flag}"
        )

    lines += ["", "  policy text round-trips through the parser:"]
    for key, meta in results["policies"].items():
        lines.append(f"    {key:26s} {'yes' if meta['round_trips'] else 'NO'}")

    v12 = results["comparison_v1_to_v2"]
    lines += [
        "",
        "  v1 -> v2, the tightening",
        f"    newly declined             {v12['newly_declined']}",
        f"    of those, would default    {v12['newly_declined_who_would_default']}",
        f"    of those, would have repaid {v12['newly_declined_who_would_repay']}",
        f"    precision of the tightening {v12['precision_of_the_tightening']:.1%}",
        f"    approval rate              {v12['delta']['approval_rate_points']:+.2f} points",
        f"    bad rate                   {v12['delta']['bad_rate_points']:+.2f} points",
        f"    net                        {v12['delta']['net']:+,.0f} PKR",
        f"    disbursed                  {v12['delta']['disbursed']:+,.0f} PKR",
    ]

    v23 = results["comparison_v2_to_v3"]
    v3_critical = results["lint"]["nano_v3_timepriced"]["counts"]["critical"]
    lines += [
        "",
        "  v2 -> v3, the fee change only",
        f"    credit decisions changed   {v23['newly_approved'] + v23['newly_declined']}",
        f"    lint                       {v3_critical} critical",
    ]

    disc = results["discrimination"]
    cut = results["cutoff_sweep"]
    supplied = results["supplied_cohorts"]
    lines += [
        "",
        "  v2's scorecard, on the applicants who reached it",
        f"    Gini                       {disc['gini']}   (AUC {disc['auc']}, KS {disc['ks']})",
        f"    scored                     {disc['scored']:,}"
        f"   ({disc['gated_out']:,} stopped by a gate first)",
        f"    reading                    {disc['reading']}",
        "",
        "  where the cutoff should be",
        f"    policy's own cutoff        {cut['current']['threshold']:g}"
        f"   net {cut['current']['net']:>12,.0f} PKR",
        f"    net-maximising cutoff      {cut['net_maximising']['threshold']:g}"
        f"   net {cut['net_maximising']['net']:>12,.0f} PKR",
        f"    available by moving it     {cut['net_gain_available']:+,.0f} PKR"
        "   (in-sample; see results.json)",
        "",
        "  a supplied cohort",
        f"    CSV round trip             {'yes' if supplied['csv_round_trip'] else 'NO'}",
        f"    outcome column detected    {supplied['labelled_detected']}"
        f" / {supplied['unlabelled_detected']} when absent",
        f"    figures withheld with no label  {len(supplied['withheld_without_a_label'])}",
    ]

    equity = results["fairness"]["region"]
    lines += [
        "",
        "  fairness, v2 by region",
        f"    raw approval-rate gap      {equity['raw_gap']:.1%}"
        f"  ({equity['best']['key']} vs {equity['worst']['key']})",
        f"    gap within income bands    {equity['within_band_gap_mean']:.1%}",
        f"    thin cells skipped         {equity['thin_cells_skipped']}",
    ]
    return "\n".join(lines)


def main() -> None:
    results = measure()
    # See the note in p01's benchmark: LF explicitly, because CI compares this file.
    RESULTS.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(report(results))
    print(f"\nwritten to {RESULTS}")


if __name__ == "__main__":
    main()
