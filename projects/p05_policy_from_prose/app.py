"""The project 05 dashboard: what each translated policy actually decided."""

from __future__ import annotations

import argparse
import json

from shared.iopage import Page, Row, Section, attach_io
from shared.modelpage import Column, Spec, Table, attach
from shared.theme import attach_theme
from shared.webkit import App, attach_metrics, run

from .benchmark import RESULTS
from .benchmark import run as measure

app = App("policy-from-prose", static_dir=None)
attach_theme(app)
attach_metrics(app)

SOURCE = "https://github.com/hammasbuilds/fintech-lab/tree/main/projects/p05_policy_from_prose"


def _data() -> dict:
    if RESULTS.is_file():
        try:
            return json.loads(RESULTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return measure(live=False)


def spec() -> Spec:
    data = _data()
    setup = data.get("setup", {})
    usage = data.get("usage", {})

    compile_rows = []
    base = data.get("baseline", {}).get("compile", {})
    if base:
        compile_rows.append(
            {
                "arm": "project 02's own policies",
                "compiles": f"{base['compiled']}/{base['cases']}",
                "identical": f"{base['behaved_identically']}/{base['cases']}",
                "agreement": f"{base['mean_agreement']:.3f}",
                "different": base["compiled_but_different"],
            }
        )
    for name, block in data.get("by_model_and_arm", {}).items():
        inner = block.get("compile", {})
        if inner:
            compile_rows.append(
                {
                    "arm": name,
                    "compiles": f"{inner['compiled']}/{inner['cases']}",
                    "identical": f"{inner['behaved_identically']}/{inner['cases']}",
                    "agreement": f"{inner['mean_agreement']:.3f}",
                    "different": inner["compiled_but_different"],
                }
            )

    notice_rows = []
    base_notice = data.get("baseline", {}).get("notice", {})
    if base_notice:
        notice_rows.append(
            {
                "arm": "project 02's templates",
                "checks": f"{base_notice['mean_score']:.2f}",
                "all_pass": f"{base_notice['all_checks_passed']}/{base_notice['cases']}",
                "safe": f"{base_notice['safe']}/{base_notice['cases']}",
                "invented": base_notice["invented_a_reason"],
                "promised": base_notice["promised_something"],
            }
        )
    for name, block in data.get("by_model_and_arm", {}).items():
        inner = block.get("notice", {})
        if inner:
            notice_rows.append(
                {
                    "arm": name,
                    "checks": f"{inner['mean_score']:.2f}",
                    "all_pass": f"{inner['all_checks_passed']}/{inner['cases']}",
                    "safe": f"{inner['safe']}/{inner['cases']}",
                    "invented": inner["invented_a_reason"],
                    "promised": inner["promised_something"],
                }
            )

    case_rows = [
        {
            "case": row.get("case"),
            "arm": f"{row.get('model', '')} / {row.get('arm', '')}",
            "compiles": row.get("compiles"),
            "agreement": f"{row.get('agreement', 0):.3f}",
            "differing": row.get("decisions_differing", "-"),
            "gap": row.get("approval_gap_points", "-"),
            "detail": row.get("error") or "",
        }
        for row in data.get("compile_rows", [])
    ]

    return Spec(
        title="05 Policy from prose",
        subtitle="Can a model turn a written credit policy into one that runs the same way?",
        tiles=[
            ("Policies in prose", str(setup.get("policy_cases", "-")), "each with a reference"),
            ("Notice cases", str(setup.get("notice_cases", "-")), "declines and an approval"),
            ("Borrowers each policy decides", f"{setup.get('borrowers', 0):,}", "the whole cohort"),
            (
                "Replies generated",
                str(usage.get("generated", 0)),
                f"{usage.get('served_from_cache', 0)} from cache",
            ),
            ("Hit the token cap", str(usage.get("hit_the_token_cap", 0)), "truncated answers"),
        ],
        notes=[
            "A compiled policy can be run, so the grader runs it. The score is agreement "
            "with the hand-written reference over all 5,000 borrowers - decision for "
            "decision and limit for limit - not similarity of the text.",
            "The column to read first is 'different': policies that compiled, ran, and "
            "decided a different set of applicants than the document they came from. That "
            "is the dangerous output here and it is invisible to any check that stops at "
            "the text.",
            "The notice checks are keyword and pattern checks and nothing more. They can "
            "tell that a notice mentions income when the rule was about defaults, and that "
            "it contains a promise; they cannot tell whether the sentence is any good. "
            "Project 02's templates pass all of them by construction, which is what makes "
            "the model column readable.",
        ],
        tables=[
            Table(
                title="Turning prose into a policy that runs",
                note="Agreement is over the whole cohort. A policy that does not compile "
                "scores zero rather than partial credit: it has no behaviour to agree with.",
                columns=[
                    Column("arm", "Arm", "text"),
                    Column("compiles", "Compiles", "num"),
                    Column("identical", "Identical behaviour", "num"),
                    Column("agreement", "Mean agreement", "num"),
                    Column("different", "Compiled but different", "num"),
                ],
                rows=compile_rows,
                baseline_key="arm",
                baseline_value="project 02's own policies",
            ),
            Table(
                title="The decline and approval notices",
                note="'invented' counts notices that gave the applicant a reason the policy "
                "did not. 'promised' counts notices that implied a future approval.",
                columns=[
                    Column("arm", "Arm", "text"),
                    Column("checks", "Mean checks passed", "num"),
                    Column("all_pass", "All checks", "num"),
                    Column("safe", "Safe", "num"),
                    Column("invented", "Invented a reason", "num"),
                    Column("promised", "Promised something", "num"),
                ],
                rows=notice_rows,
                baseline_key="arm",
                baseline_value="project 02's templates",
            ),
            Table(
                title="Every case, policy by policy",
                note="'differing' is how many of the 5,000 borrowers got a different "
                "decision; 'gap' is the change in approval rate in percentage points.",
                columns=[
                    Column("case", "Policy", "mono"),
                    Column("arm", "Arm", "text"),
                    Column("compiles", "Compiles", "verdict"),
                    Column("agreement", "Agreement", "num"),
                    Column("differing", "Decisions differing", "num"),
                    Column("gap", "Approval gap, points", "num"),
                    Column("detail", "Compile error", "reply"),
                ],
                rows=case_rows,
            ),
        ],
        links=[
            ("Input and output", "/io"),
            ("Project 02, the reference", "http://127.0.0.1:8102/"),
            ("Source", SOURCE),
        ],
        incomplete=data.get("incomplete", []),
        setup=setup | {"usage": usage, "cache": data.get("cache", {})},
    )


attach(app, spec, RESULTS)


def io_page() -> Page:
    data = _data()
    rows = data.get("compile_rows", [])
    sections = [
        Section(
            title=f"{row['case']}  ({row['model']} / {row['arm']})",
            given="the policy, described in prose by a product owner",
            note=row.get("error") or "",
            rows=[
                Row("compiles", str(row.get("compiles"))),
                Row("agreement over the cohort", f"{row.get('agreement', 0):.3f}"),
                Row("decisions differing", str(row.get("decisions_differing", "-"))),
                Row("approval gap, points", str(row.get("approval_gap_points", "-"))),
            ],
        )
        for row in rows[:6]
    ]
    return Page(
        title="05 Policy from prose",
        subtitle="A local model against project 02's own cohort",
        sections=sections
        or [
            Section(
                title="Nothing measured yet",
                given="run: uv run python -m projects.p05_policy_from_prose.benchmark --live",
                note="The dashboard reads results.json, which the benchmark writes.",
            )
        ],
        links=[("Dashboard", "/"), ("Source", SOURCE)],
        caveat="A policy that compiles and reads sensibly can still approve a different set "
        "of applicants. Agreement over the cohort is the only check that sees that.",
    )


attach_io(app, io_page)


def main(port: int = 8105) -> None:
    run(app, port=port)


# The __main__ block stays last: anything registered after it never runs when this module
# is executed as a program, because main() blocks in the server loop.
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve the project 05 dashboard.")
    parser.add_argument("--port", type=int, default=8105)
    main(parser.parse_args().port)
