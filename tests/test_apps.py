"""The three web apps: every endpoint, the error paths, and one real HTTP round trip each.

``App.handle`` is a pure function from (method, path, body) to a response, so nearly all
of this runs without opening a socket. One test per app does bind an ephemeral port and
close it again, because "the handler returns 200" and "the server serves it" are
different claims and only the second one is what a person opening the page relies on.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest

from projects.p01_switchboard.app import app as switchboard
from projects.p02_policy_bench.app import app as policy_bench
from projects.p03_guardian.app import app as guardian
from shared.webkit import App, BackgroundServer

APPS = [switchboard, policy_bench, guardian]


def call(app: App, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    response = app.handle(method, path, raw)
    if response.content_type.startswith("application/json"):
        return response.status, json.loads(response.body)
    return response.status, response.body


# -------------------------------------------------------------- every app


@pytest.mark.parametrize("app", APPS, ids=lambda app: app.name)
def test_the_page_and_the_stylesheet_are_served(app: App) -> None:
    status, body = call(app, "GET", "/")
    assert status == 200
    assert b"<title>" in body
    assert b'href="/theme.css"' in body
    status, css = call(app, "GET", "/theme.css")
    assert status == 200
    assert b"--series-1" in css


@pytest.mark.parametrize("app", APPS, ids=lambda app: app.name)
def test_the_page_declares_a_viewport_and_a_language(app: App) -> None:
    _, body = call(app, "GET", "/")
    text = body.decode("utf-8")
    assert 'name="viewport"' in text
    assert '<html lang="en">' in text


@pytest.mark.parametrize("app", APPS, ids=lambda app: app.name)
def test_an_unknown_path_is_404_and_a_climb_out_is_refused(app: App) -> None:
    assert call(app, "GET", "/api/does-not-exist")[0] == 404
    status, body = call(app, "GET", "/../pyproject.toml")
    assert status in {403, 404}
    assert b"fintech-lab" not in (body if isinstance(body, bytes) else b"")


@pytest.mark.parametrize("app", APPS, ids=lambda app: app.name)
def test_meta_loads_and_carries_the_recorded_results(app: App) -> None:
    status, data = call(app, "GET", "/api/meta")
    assert status == 200
    assert data["results"] is not None, "run the benchmark so the UI has numbers to show"


@pytest.mark.parametrize("app", APPS, ids=lambda app: app.name)
def test_every_page_references_only_routes_the_app_serves(app: App) -> None:
    # A page that fetches a path the app does not route renders an empty panel and no
    # error, which is the kind of break a screenshot does not show.
    import re

    _, body = call(app, "GET", "/")
    text = body.decode("utf-8")
    served = {path for _method, path in app.routes}
    for match in re.finditer(r'api\("(/api/[a-z_]+)', text):
        assert match.group(1) in served, match.group(1)


# ------------------------------------------------------------- switchboard


def test_switchboard_decodes_a_generated_message() -> None:
    _, flow = call(switchboard, "POST", "/api/build", {"scenario": "atm_partial_approval"})
    assert flow["ok"] and len(flow["steps"]) == 2
    assert flow["findings"] == []
    status, decoded = call(switchboard, "POST", "/api/decode", {"raw": flow["steps"][0]["raw"]})
    assert status == 200 and decoded["ok"]
    assert decoded["mti"] == "0200"
    assert any(row["de"] == "4" for row in decoded["rows"])


def test_switchboard_reports_a_message_it_cannot_parse_without_failing() -> None:
    # A workbench that answers only "invalid" when handed real broken traffic is useless.
    status, data = call(switchboard, "POST", "/api/decode", {"raw": "0200FFFF"})
    assert status == 200
    assert data["ok"] is False
    assert data["error"]
    assert data["partial"]["mti"] == "0200"
    assert data["length"] == 8


def test_switchboard_validates_pasted_traffic() -> None:
    _, flow = call(switchboard, "POST", "/api/build", {"scenario": "atm_reversal_after_timeout"})
    raws = "\n".join(step["raw"] for step in flow["steps"])
    status, data = call(switchboard, "POST", "/api/validate", {"raws": raws})
    assert status == 200 and data["ok"]
    assert len(data["steps"]) == 3
    assert data["findings"] == []


def test_switchboard_reports_a_bad_line_by_number() -> None:
    _, flow = call(switchboard, "POST", "/api/build", {"scenario": "atm_withdrawal_approved"})
    raws = flow["steps"][0]["raw"] + "\nnot a message"
    _, data = call(switchboard, "POST", "/api/validate", {"raws": raws})
    assert data["ok"] is False
    assert data["parse_errors"][0]["line"] == 2


def test_switchboard_build_rejects_bad_input() -> None:
    assert call(switchboard, "POST", "/api/build", {"scenario": "nope"})[0] == 400
    assert (
        call(
            switchboard,
            "POST",
            "/api/build",
            {"scenario": "atm_withdrawal_approved", "amount": "x"},
        )[0]
        == 400
    )
    assert (
        call(
            switchboard, "POST", "/api/build", {"scenario": "atm_withdrawal_approved", "amount": -5}
        )[0]
        == 400
    )
    assert call(switchboard, "POST", "/api/decode", {"raw": "   "})[0] == 400
    assert call(switchboard, "POST", "/api/validate", {"raws": ""})[0] == 400


def test_switchboard_faults_endpoint_reports_the_kill_rate() -> None:
    status, data = call(switchboard, "GET", "/api/faults")
    assert status == 200
    assert data["mutation"]["killed"] == data["mutation"]["total"]
    assert data["fuzz"]["illegal_characters_accepted"] == []
    from projects.p01_switchboard import mutations

    assert len(data["catalogue"]) == len(mutations.FLOW_MUTATORS) + len(mutations.CODEC_MUTATORS)
    assert all(entry["description"].strip() for entry in data["catalogue"])


# ------------------------------------------------------------- policy bench


def test_policy_bench_compiles_and_lints_a_bundled_policy() -> None:
    _, meta = call(policy_bench, "GET", "/api/meta")
    source = next(p["source"] for p in meta["policies"] if p["key"] == "nano_v2")
    status, data = call(policy_bench, "POST", "/api/compile", {"source": source})
    assert status == 200
    assert data["round_trips"] is True
    assert data["lint"]["counts"]["critical"] == 0
    assert len(data["gates"]) == 5


def test_policy_bench_flags_the_time_priced_policy() -> None:
    _, meta = call(policy_bench, "GET", "/api/meta")
    source = next(p["source"] for p in meta["policies"] if p["key"] == "nano_v3_timepriced")
    _, data = call(policy_bench, "POST", "/api/compile", {"source": source})
    assert data["lint"]["blocking"] is True
    assert any(note["check"] == "C1" for note in data["lint"]["notes"])


def test_policy_bench_backtests_and_measures_fairness() -> None:
    _, meta = call(policy_bench, "GET", "/api/meta")
    source = next(p["source"] for p in meta["policies"] if p["key"] == "nano_v2")
    status, data = call(policy_bench, "POST", "/api/backtest", {"source": source})
    assert status == 200
    report = data["backtest"]
    assert report["applications"] == 5000
    assert report["approvals"] + report["refers"] + report["declines"] == 5000
    assert data["fairness"]["raw_gap"] >= 0
    assert "not a compliance opinion" in data["fairness"]["not_a_verdict"]


def test_policy_bench_compares_two_versions_on_the_same_cohort() -> None:
    _, meta = call(policy_bench, "GET", "/api/meta")
    sources = {p["key"]: p["source"] for p in meta["policies"]}
    status, data = call(
        policy_bench,
        "POST",
        "/api/compare",
        {"base": sources["nano_v1"], "candidate": sources["nano_v2"]},
    )
    assert status == 200
    assert data["newly_declined"] > 0
    assert 0 <= data["precision_of_the_tightening"] <= 1
    assert len(data["examples"]) <= 8


def test_policy_bench_scores_one_applicant_in_both_languages() -> None:
    _, meta = call(policy_bench, "GET", "/api/meta")
    source = next(p["source"] for p in meta["policies"] if p["key"] == "nano_v2")
    status, data = call(
        policy_bench,
        "POST",
        "/api/score",
        {
            "source": source,
            "applicant": {
                "monthly_income": 45000,
                "wallet_months": 24,
                "prior_defaults": 0,
                "on_time_repayments": 3,
                "avg_balance": 9000,
                "employment": "salaried",
            },
        },
    )
    assert status == 200
    assert data["decision"]["outcome"] == "approve"
    assert data["notice"]["en"]["headline"]
    assert data["notice"]["ur"]["headline"]
    assert data["notice"]["en"]["headline"] != data["notice"]["ur"]["headline"]


def test_policy_bench_rejects_bad_input() -> None:
    assert call(policy_bench, "POST", "/api/compile", {"source": ""})[0] == 400
    assert call(policy_bench, "POST", "/api/compile", {"source": 'policy "x"'})[0] == 400
    _, meta = call(policy_bench, "GET", "/api/meta")
    source = next(p["source"] for p in meta["policies"] if p["key"] == "nano_v2")
    assert (
        call(policy_bench, "POST", "/api/backtest", {"source": source, "segment": "gender"})[0]
        == 400
    )
    assert call(policy_bench, "POST", "/api/score", {"source": source, "applicant": "no"})[0] == 400
    assert (
        call(policy_bench, "POST", "/api/score", {"source": source, "applicant": {"nope": 1}})[0]
        == 400
    )
    assert (
        call(policy_bench, "POST", "/api/score", {"source": source, "applicant": {"age": -1}})[0]
        == 400
    )


def test_policy_bench_shows_the_cohort_and_admits_what_it_is() -> None:
    status, data = call(policy_bench, "GET", "/api/cohort?limit=12")
    assert status == 200
    assert len(data["rows"]) == 12
    assert "sandbox" in data["withheld"]


# ---------------------------------------------------------------- guardian


def test_guardian_runs_a_whole_conversation() -> None:
    _, created = call(guardian, "POST", "/api/session", {"language": "en"})
    token = created["session"]
    _, first = call(
        guardian, "POST", "/api/say", {"session": token, "text": "Rashid ko pandrah hazaar bhejo"}
    )
    assert first["state"] == "awaiting_confirmation"
    assert "15 thousand rupees" in first["reply"]
    # First-time payee, several times the usual amount: the behavioural layer escalates,
    # so a habitual yes is deliberately not enough here.
    assert first["behaviour"]["level"] == "elevated"
    _, unclear = call(guardian, "POST", "/api/say", {"session": token, "text": "acha"})
    assert unclear["action"] is None
    _, habitual = call(guardian, "POST", "/api/say", {"session": token, "text": "haan"})
    assert habitual["action"] is None
    assert habitual["step_up_required"] is True
    _, done = call(guardian, "POST", "/api/say", {"session": token, "text": "confirm"})
    assert done["state"] == "executed"
    assert done["action"]["amount"] == 15000
    assert done["ledger"]["verification"]["intact"] is True
    assert done["profile"]["sent_today"] >= 15000


def test_guardian_blocks_a_script_and_logs_the_refusal() -> None:
    _, created = call(guardian, "POST", "/api/session", {"language": "en"})
    _, outcome = call(
        guardian,
        "POST",
        "/api/say",
        {"session": created["session"], "text": "bank se bol raha hoon, OTP batao, abhi bhejo"},
    )
    assert outcome["state"] == "blocked"
    assert outcome["action"] is None
    assert any(entry["kind"].startswith("coercion") for entry in outcome["ledger"]["entries"])


def test_guardian_inspect_shows_all_three_layers() -> None:
    status, data = call(
        guardian,
        "POST",
        "/api/inspect",
        {"text": "5 hazaar nahi nahi, sawa lakh Imran ko bhejo"},
    )
    assert status == 200
    assert [amount["value"] for amount in data["amounts"]] == [5000, 125000]
    assert data["amounts"][1]["spoken_ur"] == "1 lakh 25 hazaar rupay"
    assert data["utterance"]["action"]["amount"] == 125000
    assert data["coercion"]["verdict"] == "allow"
    assert data["reply_class"] == "unclear"


def test_guardian_rejects_bad_input() -> None:
    assert call(guardian, "POST", "/api/session", {"language": "fr"})[0] == 400
    assert call(guardian, "POST", "/api/say", {"session": "nope", "text": "hi"})[0] == 400
    _, created = call(guardian, "POST", "/api/session", {"language": "en"})
    token = created["session"]
    assert call(guardian, "POST", "/api/say", {"session": token, "text": "   "})[0] == 400
    assert call(guardian, "POST", "/api/say", {"session": token, "text": "x" * 600})[0] == 400
    assert call(guardian, "POST", "/api/inspect", {"text": 5})[0] == 400


def test_guardian_sessions_do_not_grow_without_bound() -> None:
    from projects.p03_guardian import app as module

    for _ in range(module.MAX_SESSIONS + 10):
        call(guardian, "POST", "/api/session", {"language": "en"})
    assert len(module._sessions) <= module.MAX_SESSIONS


def test_guardian_scorecard_reports_the_blind_spot() -> None:
    status, data = call(guardian, "GET", "/api/scorecard")
    assert status == 200
    assert data["coercion"]["out_of_vocabulary_cases"] > 0
    assert data["replies"]["read_as_consent_wrongly"] == 0
    assert data["properties"]["holding"] == data["properties"]["checks"]


# ------------------------------------------------------- over real HTTP


@pytest.mark.parametrize("app", APPS, ids=lambda app: app.name)
def test_each_app_answers_over_a_real_socket_then_closes(app: App) -> None:
    with BackgroundServer(app) as server:
        page = urllib.request.urlopen(server.base_url + "/", timeout=10).read()
        assert b"<title>" in page
        css = urllib.request.urlopen(server.base_url + "/theme.css", timeout=10).read()
        assert b"--series-1" in css
        meta = json.loads(urllib.request.urlopen(server.base_url + "/api/meta", timeout=20).read())
        assert meta["results"] is not None


# ------------------------------------ the recorded results must not go stale

# results.json is committed, and the dashboards read it rather than recomputing on every
# page load. A committed build output drifts from the code that made it, so the headline
# figures are re-derived here and compared.


def test_switchboard_results_json_matches_a_fresh_measurement() -> None:
    from projects.p01_switchboard.benchmark import RESULTS, measure

    recorded = json.loads(RESULTS.read_text(encoding="utf-8"))
    fresh = measure()
    for section, key in [
        ("dictionary", "elements_defined"),
        ("scenarios", "count"),
        ("scenarios", "messages"),
        ("invariants", "rules"),
        ("invariants", "valid_flows_with_findings"),
        ("mutation", "mutants"),
        ("mutation", "killed"),
        ("mutation", "killed_by_intended_rule"),
    ]:
        assert recorded[section][key] == fresh[section][key], f"{section}.{key} is stale"


def test_guardian_results_json_matches_a_fresh_measurement() -> None:
    from projects.p03_guardian.benchmark import RESULTS, measure

    recorded = json.loads(RESULTS.read_text(encoding="utf-8"))
    fresh = measure()
    for section, key in [
        ("testset", "all"),
        ("numerals", "exact"),
        ("numerals", "amounts_invented"),
        ("actions", "overall_exact"),
        ("coercion", "verdict_exact"),
        ("coercion", "out_of_vocabulary_missed"),
        ("replies", "read_as_consent_wrongly"),
        ("properties", "holding"),
    ]:
        assert recorded[section][key] == fresh[section][key], f"{section}.{key} is stale"


def test_policy_bench_results_json_matches_the_bundled_policies() -> None:
    # The full backtest is slow enough that only the parts that would change silently
    # are re-derived: the policy set and each policy's compiled shape.
    from projects.p02_policy_bench.benchmark import RESULTS, load_policies

    recorded = json.loads(RESULTS.read_text(encoding="utf-8"))
    policies = load_policies()
    assert set(recorded["policies"]) == set(policies)
    for key, policy in policies.items():
        entry = recorded["policies"][key]
        assert entry["gates"] == len(policy.gates), f"{key} gate count is stale"
        assert entry["score_rules"] == len(policy.score_rules), f"{key} rule count is stale"
        assert entry["approve_at"] == policy.approve_at, f"{key} threshold is stale"
        assert entry["fields_used"] == policy.fields_used, f"{key} field list is stale"


# --------------------------------------- the endpoints added in the second round


def test_switchboard_decodes_a_binary_dump_and_says_how_it_knew() -> None:
    from projects.p01_switchboard import binary, scenarios
    from projects.p01_switchboard.codec import pack

    message = scenarios.build("atm_withdrawal_approved").steps[0].message
    for raw, expected in (
        (pack(message), "ascii"),
        (binary.pack_binary(message).hex().upper(), "binary"),
        (binary.pack_binary(message, binary.Framing(header=False)).hex(), "binary-no-header"),
    ):
        status, data = call(switchboard, "POST", "/api/decode", {"raw": raw})
        assert status == 200 and data["ok"], expected
        assert data["framing"] == expected
        assert data["detection"]["reasons"]
        assert data["chip"]["tags"]
        assert data["chip"]["problems"] == []
        assert [row["de"] for row in data["rows"]] == [str(de) for de in message.present]


def test_switchboard_reports_a_chip_amount_that_disagrees_with_de4() -> None:
    from projects.p01_switchboard import scenarios
    from projects.p01_switchboard.codec import pack

    flow = scenarios.build("atm_withdrawal_approved")
    tampered = flow.steps[0].message.replace(de4="000001000000")
    status, data = call(switchboard, "POST", "/api/decode", {"raw": pack(tampered)})
    assert status == 200 and data["ok"], "every field is well-formed, so it must parse"
    assert any("never approved" in problem for problem in data["chip"]["problems"])


def test_switchboard_rejects_an_unknown_framing() -> None:
    assert call(switchboard, "POST", "/api/decode", {"raw": "0200", "framing": "nope"})[0] == 400


def test_policy_bench_sweeps_the_cutoff_and_keeps_both_denominators() -> None:
    _, meta = call(policy_bench, "GET", "/api/meta")
    source = next(p["source"] for p in meta["policies"] if p["key"] == "nano_v2")
    status, data = call(policy_bench, "POST", "/api/sweep", {"source": source, "step": 10})
    assert status == 200
    assert data["discrimination"]["gini"] is not None
    assert data["sweep"]["points"]
    for point in data["sweep"]["points"]:
        assert point["approval_rate_of_all"] <= point["approval_rate_of_scored"]
    assert "in_sample_warning" in data["sweep"]


def test_policy_bench_takes_a_cohort_as_csv_and_withholds_what_it_cannot_know() -> None:
    from projects.p02_policy_bench import cohort

    _, meta = call(policy_bench, "GET", "/api/meta")
    source = next(p["source"] for p in meta["policies"] if p["key"] == "nano_v2")
    people = cohort.generate(size=200, seed=77)

    status, labelled = call(
        policy_bench, "POST", "/api/cohort/load", {"csv": cohort.to_csv(people)}
    )
    assert status == 200 and labelled["labelled"] is True
    _, report = call(
        policy_bench, "POST", "/api/backtest", {"source": source, "cohort": labelled["cohort"]}
    )
    assert report["backtest"]["applications"] == 200
    assert report["backtest"]["bad_rate"] is not None

    status, bare = call(
        policy_bench,
        "POST",
        "/api/cohort/load",
        {"csv": cohort.to_csv(people, include_label=False)},
    )
    assert status == 200 and bare["labelled"] is False
    _, blind = call(
        policy_bench, "POST", "/api/backtest", {"source": source, "cohort": bare["cohort"]}
    )
    assert blind["backtest"]["bad_rate"] is None
    assert blind["backtest"]["withheld"]
    _, no_sweep = call(
        policy_bench, "POST", "/api/sweep", {"source": source, "cohort": bare["cohort"]}
    )
    assert "withheld" in no_sweep["sweep"]
    assert "withheld" in no_sweep["discrimination"]


def test_policy_bench_serves_a_cohort_template_as_csv() -> None:
    status, body = call(policy_bench, "GET", "/api/cohort/template")
    assert status == 200
    header = body.decode("utf-8").splitlines()[0]
    assert "monthly_income" in header and "would_default" in header


def test_policy_bench_rejects_bad_cohort_input() -> None:
    assert call(policy_bench, "POST", "/api/cohort/load", {"csv": "  "})[0] == 400
    assert call(policy_bench, "POST", "/api/cohort/load", {"csv": "a,b\n1,2"})[0] == 400
    _, meta = call(policy_bench, "GET", "/api/meta")
    source = next(p["source"] for p in meta["policies"] if p["key"] == "nano_v2")
    assert call(policy_bench, "POST", "/api/backtest", {"source": source, "cohort": "no"})[0] == 400
    assert call(policy_bench, "POST", "/api/sweep", {"source": source, "step": 0})[0] == 400
    assert call(policy_bench, "POST", "/api/sweep", {"source": source, "step": "x"})[0] == 400


def test_guardian_reports_the_account_profile_and_the_behavioural_verdict() -> None:
    _, created = call(guardian, "POST", "/api/session", {"language": "en"})
    assert created["profile"]["usual_transfer"] > 0
    _, out = call(
        guardian,
        "POST",
        "/api/say",
        {"session": created["session"], "text": "Zubair ko pachaas hazaar bhejo"},
    )
    assert out["behaviour"]["level"] == "elevated"
    assert {s["name"] for s in out["behaviour"]["signals"]} >= {"first_time_beneficiary"}
    assert "confirm" in out["reply"]


def test_guardian_scorecard_reports_what_the_behavioural_layer_recovers() -> None:
    _, data = call(guardian, "GET", "/api/scorecard")
    paired = data["paired"]
    assert paired["scams"] > 0
    assert paired["scams_escalated_by_behaviour"] == paired["scams"]
    assert paired["scams_missed_by_both"] == 0
    # And the cost is reported alongside it, not left out.
    assert "benign_escalated" in paired
    assert paired["benign_escalated"] <= paired["benign"]


# ------------------------------------------------- the model arms (04, 05, 06)

from projects.p04_spec_to_wire.app import app as spec_to_wire  # noqa: E402
from projects.p05_policy_from_prose.app import app as policy_from_prose  # noqa: E402
from projects.p06_model_vs_lexicon.app import app as model_vs_lexicon  # noqa: E402

MODEL_APPS = [spec_to_wire, policy_from_prose, model_vs_lexicon]


@pytest.mark.parametrize("app", MODEL_APPS, ids=lambda app: app.name)
def test_a_model_arm_serves_its_dashboard_without_a_model(app: App) -> None:
    # The dashboards read committed results and a committed reply cache. A page that
    # needed a GPU to render would be a page nobody else can look at.
    status, body = call(app, "GET", "/")
    assert status == 200
    assert b"<title>" in body
    assert b'href="/theme.css"' in body
    assert call(app, "GET", "/theme.css")[0] == 200


@pytest.mark.parametrize("app", MODEL_APPS, ids=lambda app: app.name)
def test_a_model_arm_serves_its_input_output_proof(app: App) -> None:
    status, body = call(app, "GET", "/io")
    assert status == 200
    assert b"input" in body.lower()


@pytest.mark.parametrize("app", MODEL_APPS, ids=lambda app: app.name)
def test_a_model_arm_exposes_its_results_and_counters(app: App) -> None:
    status, data = call(app, "GET", "/api/results")
    assert status == 200
    assert data["setup"]["models"]
    assert "usage" in data
    status, metrics = call(app, "GET", "/api/metrics")
    assert status == 200
    assert metrics["app"] == app.name


@pytest.mark.parametrize("app", MODEL_APPS, ids=lambda app: app.name)
def test_a_model_arm_names_its_deterministic_baseline_on_the_page(app: App) -> None:
    # The whole claim of these three is that they are scored against a baseline that does
    # not move. The page has to say which one.
    _, body = call(app, "GET", "/")
    text = body.decode("utf-8")
    assert "project 0" in text, "the page must name the project whose grader scores it"


@pytest.mark.parametrize("app", MODEL_APPS, ids=lambda app: app.name)
def test_a_model_arm_declares_what_it_did_not_run(app: App) -> None:
    _, data = call(app, "GET", "/api/results")
    # The key must exist even when empty: a page that silently omits unrun cases implies
    # a full sweep it did not do.
    assert "incomplete" in data
    assert isinstance(data["incomplete"], list)


def test_the_model_dashboards_show_every_reply_the_grader_saw() -> None:
    # A mean score says how it went; the row where the model said 60000 for "saat hazaar"
    # says why. Hiding the replies behind an average asks to be believed, not read.
    _, body = call(model_vs_lexicon, "GET", "/")
    text = body.decode("utf-8")
    assert "Every case" in text


@pytest.mark.parametrize("app", MODEL_APPS, ids=lambda app: app.name)
def test_a_model_arm_answers_over_a_real_socket(app: App) -> None:
    with BackgroundServer(app) as server:
        page = urllib.request.urlopen(server.base_url + "/", timeout=30).read()
        assert b"<title>" in page
        proof = urllib.request.urlopen(server.base_url + "/io", timeout=30).read()
        assert proof
        results = json.loads(
            urllib.request.urlopen(server.base_url + "/api/results", timeout=30).read()
        )
        assert results["setup"]
