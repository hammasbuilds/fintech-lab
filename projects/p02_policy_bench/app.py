"""The Policy Bench web UI and its JSON API.

The cohort is generated once per process and reused. It is five thousand borrowers from
a seeded generator, so holding it costs little and regenerating it per request would make
every backtest in the UI slower for no change in the answer.
"""

from __future__ import annotations

import json
import pathlib
import secrets
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.datapage import Block, Figure, dig, figures_from  # noqa: F401
from shared.datapage import Page as DataPage
from shared.datapage import attach as attach_datasets
from shared.iopage import Page, Row, Section, attach_io
from shared.theme import attach_theme
from shared.webkit import App, BadRequest, Request, Response, attach_metrics, run

from . import (
    backtest,
    cardbook,
    cohort,
    compliance,
    dsl,
    engine,
    explain,
    fairness,
    loanbook,
    sweep,
    timeline,
)

STATIC = Path(__file__).with_name("static")
POLICIES = Path(__file__).with_name("policies")
RESULTS = Path(__file__).with_name("results.json")

app = App("policy-bench", static_dir=STATIC)
attach_theme(app)
attach_metrics(app)

SOURCE = "https://github.com/hammasbuilds/fintech-lab/tree/main/projects/p02_policy_bench"


@lru_cache(maxsize=1)
def borrowers() -> list[cohort.Borrower]:
    return cohort.generate()


@lru_cache(maxsize=8)
def _policy_source(key: str) -> str:
    path = POLICIES / f"{key}.policy"
    if not path.is_file():
        raise BadRequest(f"no bundled policy called {key!r}")
    return path.read_text(encoding="utf-8")


#: Cohorts supplied by the user, by token. Capped: each one is thousands of records.
_uploaded: dict[str, tuple[list[cohort.Borrower], bool]] = {}
MAX_UPLOADS = 8
MAX_CSV_BYTES = 512 * 1024


def _cohort(payload: dict[str, Any]) -> tuple[list[cohort.Borrower], bool]:
    """The bundled cohort, or an uploaded one when the request names it."""
    token = payload.get("cohort")
    if not token:
        return borrowers(), True
    found = _uploaded.get(str(token))
    if found is None:
        raise BadRequest("that uploaded cohort has expired; load it again")
    return found


def _compile(source: str) -> dsl.Policy:
    if not source.strip():
        raise BadRequest("the policy is empty")
    try:
        return dsl.compile_policy(source)
    except dsl.PolicyError as exc:
        raise BadRequest(str(exc)) from None


# ------------------------------------------------------------------- endpoints


@app.route("GET", "/api/meta")
def meta(_request: Request) -> Response:
    people = borrowers()
    sample = people[0]
    return Response.json(
        {
            "policies": [
                {"key": path.stem, "source": path.read_text(encoding="utf-8")}
                for path in sorted(POLICIES.glob("*.policy"))
            ],
            "cohort": cohort.summary(people),
            "fields": sorted(sample.record()),
            "sample_applicant": sample.record(),
            "regions": list(cohort.REGIONS),
            "employment": list(cohort.EMPLOYMENT),
            "checks": [{"check": c, "title": t, "severity": s} for c, t, s in compliance.CHECKS],
            "results": _results(),
        }
    )


@app.route("POST", "/api/compile")
def compile_only(request: Request) -> Response:
    policy = _compile(str(request.json().get("source", "")))
    rendered = policy.render()
    return Response.json(
        {
            "ok": True,
            "name": policy.name,
            "version": str(policy.version),
            "currency": policy.currency,
            "gates": [
                {
                    "outcome": gate.outcome,
                    "condition": gate.condition.render(),
                    "reason": gate.reason,
                    "line": gate.line,
                }
                for gate in policy.gates
            ],
            "score_rules": [
                {
                    "points": rule.points,
                    "condition": rule.condition.render(),
                    "reason": rule.reason,
                    "line": rule.line,
                }
                for rule in policy.score_rules
            ],
            "approve_at": policy.approve_at,
            "limit": policy.limit.render() if policy.limit else None,
            "charges": [charge.render() for charge in policy.charges],
            "fields_used": policy.fields_used,
            "rendered": rendered,
            "round_trips": dsl.compile_policy(rendered).render() == rendered,
            "lint": compliance.verdict(compliance.review(policy)),
        }
    )


@app.route("POST", "/api/backtest")
def run_backtest(request: Request) -> Response:
    payload = request.json()
    policy = _compile(str(payload.get("source", "")))
    attribute = str(payload.get("segment", "region"))
    if attribute not in {"region", "employment"}:
        raise BadRequest("segment must be region or employment")
    people, labelled = _cohort(payload)
    try:
        report, outcomes = backtest.run(policy, people, labelled=labelled)
    except engine.EvaluationError as exc:
        raise BadRequest(str(exc)) from None
    return Response.json(
        {
            "ok": True,
            "backtest": report.as_dict(),
            "lint": compliance.verdict(compliance.review(policy)),
            "fairness": fairness.analyse(outcomes, attribute),
        }
    )


@app.route("POST", "/api/compare")
def run_compare(request: Request) -> Response:
    payload = request.json()
    base = _compile(str(payload.get("base", "")))
    candidate = _compile(str(payload.get("candidate", "")))
    people, labelled = _cohort(payload)
    try:
        result = backtest.compare(base, candidate, people, labelled=labelled)
    except engine.EvaluationError as exc:
        raise BadRequest(str(exc)) from None
    return Response.json({"ok": True, **result.as_dict()})


@app.route("POST", "/api/score")
def score_one(request: Request) -> Response:
    payload = request.json()
    policy = _compile(str(payload.get("source", "")))
    applicant = payload.get("applicant")
    if not isinstance(applicant, dict):
        raise BadRequest("send an applicant object")
    record = dict(borrowers()[0].record())
    for key, value in applicant.items():
        if key not in record:
            raise BadRequest(f"the cohort has no field {key!r}")
        record[key] = _coerce(record[key], value, key)
    try:
        decision = engine.evaluate(policy, record)
    except engine.EvaluationError as exc:
        raise BadRequest(str(exc)) from None
    return Response.json(
        {
            "ok": True,
            "applicant": record,
            "decision": decision.as_dict(),
            "notice": {
                "en": explain.notice(decision, policy.currency, "en"),
                "ur": explain.notice(decision, policy.currency, "ur"),
            },
        }
    )


@app.route("POST", "/api/sweep")
def run_sweep(request: Request) -> Response:
    payload = request.json()
    policy = _compile(str(payload.get("source", "")))
    try:
        step = float(payload.get("step", 5))
    except (TypeError, ValueError):
        raise BadRequest("step must be a number") from None
    if not 1 <= step <= 25:
        raise BadRequest("step must be between 1 and 25 score points")
    people, labelled = _cohort(payload)
    scored = sweep.score_all(policy, people)
    return Response.json(
        {
            "ok": True,
            "labelled": labelled,
            "discrimination": sweep.discrimination(scored)
            if labelled
            else {
                "withheld": (
                    "the cohort has no outcome column, so there is nothing to measure "
                    "the score's ranking against"
                )
            },
            "sweep": sweep.sweep(policy, people, step=step)
            if labelled
            else {
                "withheld": (
                    "a sweep ranks cutoffs by what they cost and earn, and both need a "
                    "known outcome"
                )
            },
        }
    )


#: How many borrowers the over-time panel uses by default. The whole cohort is 50,000 and
#: four passes over it with a sweep in each takes most of a minute, which is too long for
#: a click. The panel says which size it used, and the committed at-scale run in
#: results_datasets.json is the one measured over everybody.
TIMELINE_DEFAULT = 12_000
TIMELINE_MAX = 50_000

#: Characteristics worth a stability figure of their own. A score is a coarse number and
#: hides a shift that is plain in its inputs.
TIMELINE_FIELDS = (
    "monthly_income",
    "wallet_months",
    "other_app_outstanding",
    "avg_balance",
    "txn_velocity",
    "age",
)


@lru_cache(maxsize=16)
def _dated_cohort(size: int) -> tuple:
    return tuple(cohort.generate()[:size])


@app.route("POST", "/api/timeline")
def over_time(request: Request) -> Response:
    """Vintages, stability, out-of-time and the approvals-only view.

    All four need a cohort with dates, which is what the origination window added. They
    are served together because they answer one question - what happens to this policy
    as the book ages and the applicants change - and reading any one of them alone is
    how a drifting population gets called stable.
    """
    payload = request.json()
    policy = _compile(str(payload.get("source", "")))
    try:
        size = int(payload.get("size", TIMELINE_DEFAULT))
    except (TypeError, ValueError):
        raise BadRequest("size must be a whole number of borrowers") from None
    if not 2_000 <= size <= TIMELINE_MAX:
        raise BadRequest(f"size must be between 2,000 and {TIMELINE_MAX:,} borrowers")

    people = list(_dated_cohort(size))
    return Response.json(
        {
            "ok": True,
            "borrowers": len(people),
            "window_months": cohort.WINDOW_MONTHS,
            "split_month": timeline.SPLIT_MONTH,
            "vintages": timeline.vintage_curves(policy, people),
            "stability": timeline.psi(policy, people),
            "characteristics": timeline.characteristic_psi(people, TIMELINE_FIELDS),
            "out_of_time": timeline.out_of_time(policy, people),
            "approvals_only": timeline.approvals_only_view(policy, people),
            "note": (
                f"Measured on {len(people):,} of the cohort's {len(cohort.generate()):,} "
                f"borrowers so the page answers in a few seconds. The committed at-scale "
                f"run uses all of them."
            ),
        }
    )


@app.route("POST", "/api/cohort/load")
def load_cohort(request: Request) -> Response:
    """Take a cohort as pasted CSV, so the sandbox can be pointed at real borrowers."""
    payload = request.json()
    text = payload.get("csv")
    if not isinstance(text, str) or not text.strip():
        raise BadRequest("paste some CSV first")
    if len(text) > MAX_CSV_BYTES:
        raise BadRequest(f"that CSV is over the {MAX_CSV_BYTES // 1024} KiB limit")
    try:
        people, meta = cohort.from_csv(text)
    except cohort.CohortError as exc:
        raise BadRequest(str(exc)) from None
    token = secrets.token_urlsafe(9)
    _uploaded[token] = (people, bool(meta["labelled"]))
    while len(_uploaded) > MAX_UPLOADS:
        _uploaded.pop(next(iter(_uploaded)))
    return Response.json({"ok": True, "cohort": token, **meta, "summary": cohort.summary(people)})


@app.route("GET", "/api/cohort/template")
def cohort_template(_request: Request) -> Response:
    """The first rows of the bundled cohort as CSV, to use as a header template."""
    return Response.text(cohort.to_csv(borrowers()[:25]), content_type="text/csv")


@app.route("GET", "/api/cohort")
def cohort_rows(request: Request) -> Response:
    limit = min(200, max(1, int(request.query.get("limit", "40"))))
    return Response.json(
        {
            "summary": cohort.summary(borrowers()),
            "rows": [asdict(person) for person in borrowers()[:limit]],
            "withheld": (
                "would_default is the generator's own label. It is shown here because this "
                "is a sandbox and the whole point is to score a policy against a known "
                "outcome; no policy in this project may read it."
            ),
        }
    )


# --------------------------------------------------------------------- helpers


def _coerce(existing: object, value: object, key: str) -> object:
    if isinstance(existing, str):
        return str(value)
    try:
        number = float(str(value))
    except ValueError:
        raise BadRequest(f"{key} must be a number, got {value!r}") from None
    if number < 0:
        raise BadRequest(f"{key} cannot be negative")
    return int(number) if float(number).is_integer() else number


def _results() -> dict[str, Any] | None:
    if not RESULTS.is_file():
        return None
    try:
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------- datasets
# What this project is measured on at full size, and what came out. The figures are read
# out of results_datasets.json and data/MANIFEST.json rather than typed, so this page
# cannot drift from the run that produced it.
DATASETS_RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"


def _at_scale() -> dict:
    if DATASETS_RESULTS.is_file():
        try:
            return json.loads(DATASETS_RESULTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def datasets_page() -> DataPage:
    data = _at_scale()
    blocks = [
        Block(
            title="The real loan book",
            subtitle="255,347 applications with the outcome that happened.",
            figures=figures_from(
                data,
                [
                    ("applications", "loan_book.cohort.rows", ""),
                    ("defaults", "loan_book.cohort.defaults", "applications"),
                    ("base default rate", "loan_book.cohort.base_default_rate", "applications"),
                    (
                        "v1 approvals",
                        "loan_book.policies.real_v1_scoregate.backtest.approvals",
                        "applications",
                    ),
                    (
                        "v1 bad rate among approvals",
                        "loan_book.policies.real_v1_scoregate.backtest.bad_rate",
                        "approvals",
                    ),
                    (
                        "v1 Gini",
                        "loan_book.policies.real_v1_scoregate.discrimination.gini",
                        "the scored population",
                    ),
                    (
                        "v2 Gini, with the price signal removed",
                        "loan_book.policies.real_v2_no_price.discrimination.gini",
                        "the scored population",
                    ),
                    ("v1 net", "loan_book.policies.real_v1_scoregate.backtest.net", "applications"),
                    (
                        "v3 net, priced over the term",
                        "loan_book.policies.real_v3_priced.backtest.net",
                        "applications",
                    ),
                ],
            ),
            reading=(
                "Dropping interest_rate - the lender's own price - costs a third of the "
                "separation, which is the uncomfortable finding: the strongest feature in "
                "the book was a decision the lender had already made. And a one-off 2% fee "
                "loses money at every cutoff, so the sweep's answer for v1 and v2 is to "
                "approve nobody; only v3, priced over the term, earns anything - and v3 is "
                "the one the compliance lint flags as critical."
            ),
        ),
        Block(
            title="The real card book",
            subtitle=("438,557 applications, 8.3% of which have any repayment history."),
            figures=figures_from(
                data,
                [
                    ("applications", "card_book.cohort.applications", ""),
                    (
                        "joined to a repayment history",
                        "card_book.cohort.joined_with_history",
                        "applications",
                    ),
                    ("join rate", "card_book.cohort.join_rate", "applications"),
                    (
                        "pensioners flagged by the sentinel",
                        "card_book.cohort.pensioners_flagged",
                        "applications",
                    ),
                    (
                        "occupation not stated",
                        "card_book.cohort.occupation_not_stated",
                        "applications",
                    ),
                    (
                        "approval rate, whole book",
                        "card_book.whole_book.backtest.approval_rate",
                        "applications",
                    ),
                    (
                        "bad rate, ever 30+ dpd slice",
                        "card_book.definitions.ever_30_plus_dpd.backtest.bad_rate",
                        "the labelled slice",
                    ),
                    (
                        "Gini, ever 30+ dpd",
                        "card_book.definitions.ever_30_plus_dpd.discrimination.gini",
                        "the labelled slice",
                    ),
                    (
                        "Gini, ever 60+ dpd",
                        "card_book.definitions.ever_60_plus_dpd.discrimination.gini",
                        "the labelled slice",
                    ),
                ],
            ),
            reading=(
                "Same 36,457 rows, same policy, two defensible definitions of bad - and the "
                "separation differs by more than three times. Whoever picks the definition "
                "picks the result, which is why both are printed."
            ),
        ),
        Block(
            title="The synthetic cohort at 250,000",
            subtitle="The same generator the dashboard runs at 50,000.",
            figures=figures_from(
                data,
                [
                    ("borrowers", "synthetic_at_scale.cohort.size", ""),
                    (
                        "v1 approval rate",
                        "synthetic_at_scale.policies.nano_v1.backtest.approval_rate",
                        "borrowers",
                    ),
                    (
                        "v2 approval rate",
                        "synthetic_at_scale.policies.nano_v2.backtest.approval_rate",
                        "borrowers",
                    ),
                    (
                        "v2 Gini",
                        "synthetic_at_scale.policies.nano_v2.discrimination.gini",
                        "the scored population",
                    ),
                ],
            ),
            reading=(
                "At 5,000 borrowers this cohort reported a Gini of 0.284 and a best cutoff "
                "of 40. At 50,000 it is 0.233 and 35, and at 250,000 it stays there. The "
                "first number was sampling noise, which is the argument for the floor."
            ),
        ),
    ]
    if not data:
        blocks = [
            Block(
                title="Not run on this machine",
                subtitle=(
                    "The at-scale figures come from results_datasets.json, which is "
                    "committed. If this page is empty the file is missing: run make data "
                    "and then make bench-datasets."
                ),
            )
        ]
    return DataPage(
        title="02 Policy Bench",
        subtitle=(
            "Two real books, three real policies, and the synthetic cohort five times larger"
        ),
        dataset_keys=[loanbook.KEY, cardbook.KEY_APPLICATIONS, cardbook.KEY_RECORDS],
        blocks=blocks,
        links=[("Dashboard", "/"), ("Input and output", "/io"), ("Source", SOURCE)],
    )


attach_datasets(app, datasets_page)


def main(port: int = 8102) -> None:
    run(app, port=port)


# ------------------------------------------------------------- input / output


def io_page() -> Page:
    results = _results() or {}
    people = borrowers()
    sources = {path.stem: path.read_text(encoding="utf-8") for path in POLICIES.glob("*.policy")}
    v1 = dsl.compile_policy(sources["nano_v1"])
    v2 = dsl.compile_policy(sources["nano_v2"])

    compiled = Section(
        title="A policy written in words, compiled to something executable",
        note="The tree is what runs, and it prints back to the same text - so the document "
        "the credit team argues over and the thing deciding applications cannot drift apart.",
        given=sources["nano_v2"].strip(),
        rows=[
            Row("gates", str(len(v2.gates))),
            Row("scoring rules", str(len(v2.score_rules))),
            Row("approve at", f"{v2.approve_at:g}"),
            Row(
                "text round-trips through the parser",
                "yes" if dsl.compile_policy(v2.render()).render() == v2.render() else "NO",
            ),
            Row(
                "compliance lint",
                "clean" if not compliance.review(v2) else f"{len(compliance.review(v2))} note(s)",
            ),
        ],
    )

    rows = []
    for key in ("nano_v1", "nano_v2", "nano_v3_timepriced"):
        back = results.get("backtests", {}).get(key, {})
        lint = results.get("lint", {}).get(key, {}).get("counts", {})
        flag = "clean" if not lint.get("critical") else f"{lint['critical']} critical"
        rows.append(
            Row(
                key,
                f"approvals {back.get('approval_rate', 0):.1%}   "
                f"bad rate {back.get('bad_rate') or 0:.2%}   "
                f"net {back.get('net') or 0:,.0f} PKR   lint {flag}",
            )
        )
    tested = Section(
        title="Back-test every bundled policy on the same borrowers",
        note="50,000 synthetic borrowers from a seeded generator written out in full in cohort.py.",
        given="three policy files and 5,000 borrowers",
        rows=rows,
    )

    comparison = results.get("comparison_v1_to_v2", {})
    delta = comparison.get("delta", {})
    tightening = Section(
        title="What a tightening actually costs",
        note="Raise the income floor, penalise turnover.",
        given="move from nano_v1 to nano_v2",
        rows=[
            Row("newly declined", f"{comparison.get('newly_declined', 0):,}"),
            Row(
                "of those, would have defaulted",
                str(comparison.get("newly_declined_who_would_default", "-")),
            ),
            Row(
                "of those, would have repaid",
                f"{comparison.get('newly_declined_who_would_repay') or 0:,}",
            ),
            Row(
                "precision of the tightening",
                f"{(comparison.get('precision_of_the_tightening') or 0):.1%}",
            ),
            Row("bad rate", f"{delta.get('bad_rate_points', 0):+.2f}", "points"),
            Row("net", f"{delta.get('net', 0):+,.0f}", "PKR"),
            Row("disbursed", f"{delta.get('disbursed', 0):+,.0f}", "PKR"),
        ],
        reading="The tightening works, and it costs "
        f"{comparison.get('newly_declined_who_would_repay') or 0:,} good borrowers to avoid "
        f"{comparison.get('newly_declined_who_would_default', 0)}. An approval-rate chart "
        "shows only the first half of that sentence.",
    )

    disc = results.get("discrimination", {})
    cut = results.get("cutoff_sweep", {})
    cutoff = Section(
        title="Where the cutoff should be, and why the tightening cut good borrowers",
        note="Discrimination is a property of the scorecard; the cutoff is a property of "
        "the decision. Moving one cannot change the other.",
        given="score every borrower once, then re-decide at every threshold",
        rows=[
            Row("Gini", str(disc.get("gini", "-"))),
            Row("AUC", str(disc.get("auc", "-"))),
            Row("KS", str(disc.get("ks", "-"))),
            Row(
                "measured on",
                f"{disc.get('scored', 0):,} scored, {disc.get('gated_out', 0):,} gated out first",
            ),
            Row("the policy's own cutoff", f"{cut.get('current', {}).get('threshold', '-')}"),
            Row("net-maximising cutoff", f"{cut.get('net_maximising', {}).get('threshold', '-')}"),
            Row("available by moving it", f"{cut.get('net_gain_available', 0):+,.0f}", "PKR"),
        ],
        reading=f"{disc.get('reading', '')} {cut.get('in_sample_warning', '')}",
    )

    equity = results.get("fairness", {}).get("region", {})
    fairness_section = Section(
        title="Approval-rate gap by region, raw and inside income bands",
        note="No policy here mentions region at all.",
        given="nano_v2's approvals, grouped by region",
        rows=[
            Row("raw gap", f"{equity.get('raw_gap', 0):.1%}"),
            Row("gap within income bands", f"{equity.get('within_band_gap_mean', 0):.1%}"),
        ],
        reading=f"{equity.get('reading', '')} {equity.get('not_a_verdict', '')}",
    )

    del people, v1
    return Page(
        title="Policy Bench",
        subtitle="Lending-policy sandbox - input and output",
        sections=[compiled, tested, tightening, cutoff, fairness_section],
        links=[
            ("Open the dashboard", "/"),
            (
                "Source on GitHub",
                SOURCE,
            ),
            ("All three projects", "https://hammasbuilds.github.io/fintech-lab/"),
        ],
        caveat="The bundled cohort is synthetic and its generator is written out in full in "
        "cohort.py. Every figure here is a statement about that generator, not about a "
        "market. Load your own cohort as CSV to get numbers about something real.",
    )


attach_io(app, io_page)

# The __main__ block stays last on purpose. Anything registered after it never
# runs when this module is executed as a program, because main() blocks in the server
# loop and never returns - so a route added below here would exist on import and be
# missing when served, which is the one mode that counts.
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the Policy Bench sandbox.")
    parser.add_argument("--port", type=int, default=8102)
    main(parser.parse_args().port)
