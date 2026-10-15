"""The Policy Bench web UI and its JSON API.

The cohort is generated once per process and reused. It is five thousand borrowers from
a seeded generator, so holding it costs little and regenerating it per request would make
every backtest in the UI slower for no change in the answer.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.theme import attach_theme
from shared.webkit import App, BadRequest, Request, Response, run

from . import backtest, cohort, compliance, dsl, engine, explain, fairness, sweep

STATIC = Path(__file__).with_name("static")
POLICIES = Path(__file__).with_name("policies")
RESULTS = Path(__file__).with_name("results.json")

app = App("policy-bench", static_dir=STATIC)
attach_theme(app)


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


def main(port: int = 8102) -> None:
    run(app, port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the Policy Bench sandbox.")
    parser.add_argument("--port", type=int, default=8102)
    main(parser.parse_args().port)
