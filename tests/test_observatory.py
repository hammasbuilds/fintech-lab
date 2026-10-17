"""The control panel: the registry, the supervisor, telemetry, and the published page.

The supervisor tests actually start and stop real subprocesses. That is slow and it is the
only way to test it honestly - a supervisor whose "running" is a mock is exactly the
supervisor that reports a crashed server as up. Each one cleans up after itself, and the
module-scoped fixture stops anything left behind.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from observatory import supervisor
from observatory.app import app as panel
from observatory.registry import (
    BY_KEY,
    PAGES,
    PROJECTS,
    dig,
    format_value,
    headline_for,
    load_registry,
    port_answers,
)
from shared.webkit import App, Metrics, Request, Response

ROOT = Path(__file__).resolve().parents[1]


def call(method: str, path: str, body: object = None) -> tuple[int, object]:
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    response = panel.handle(method, path, raw)
    if response.content_type.startswith("application/json"):
        return response.status, json.loads(response.body)
    return response.status, response.body


def _clear_the_ports() -> None:
    """Stop anything of ours on the project ports, before and after the module.

    Checking only afterwards is not enough. A server wedged on a port by an earlier run
    makes every one of these tests fail in a way that looks like a supervisor bug - which
    is exactly how the first version of this file was read, for several rounds.
    """
    for project in PROJECTS:
        if supervisor.status(project).running:
            supervisor.stop(project)
        supervisor.forget(project)


@pytest.fixture(autouse=True, scope="module")
def _leave_nothing_running():
    _clear_the_ports()
    yield
    _clear_the_ports()


# ------------------------------------------------------------------- registry


def test_all_six_projects_are_registered_with_distinct_ports() -> None:
    assert len(PROJECTS) == 6
    ports = [project.port for project in PROJECTS]
    assert len(set(ports)) == 6
    assert sorted(BY_KEY) == [
        "guardian",
        "model-vs-lexicon",
        "policy-bench",
        "policy-from-prose",
        "spec-to-wire",
        "switchboard",
    ]


def test_the_model_arms_are_marked_and_paired_with_a_real_project() -> None:
    arms = [project for project in PROJECTS if project.needs_model]
    assert len(arms) == 3
    for project in arms:
        assert project.pairs_with in BY_KEY, project.key
        assert BY_KEY[project.pairs_with].needs_model is False
        # Its first opener is the baseline that grades it.
        assert project.links()[0]["label"] == "Its baseline"
        assert str(BY_KEY[project.pairs_with].port) in project.links()[0]["href"]


def test_the_projects_built_with_code_need_no_model() -> None:
    plain = [project for project in PROJECTS if not project.needs_model]
    assert len(plain) == 3
    assert all(project.pairs_with == "" for project in plain)


def test_every_project_points_at_files_that_exist() -> None:
    for project in PROJECTS:
        assert (ROOT / "projects" / project.folder).is_dir(), project.key
        assert project.results_path.is_file(), f"{project.key} has no results.json"
        module_path = ROOT / Path(project.module.replace(".", "/") + ".py")
        assert module_path.is_file(), project.module


def test_every_project_offers_the_same_set_of_openers() -> None:
    # The point of the panel is that there is one place to open a project from, so a
    # project missing one of these would send the reader back to a shortcut.
    for project in PROJECTS:
        labels = [link["label"] for link in project.links()]
        expected = [
            "Dashboard",
            "Input and output",
            "Datasets",
            "Source",
            "README",
            "Gap analysis",
            "Published page",
        ]
        if project.needs_model:
            expected = ["Its baseline", *expected]
        assert labels == expected, project.key
        own_ports = {project.port}
        if project.pairs_with:
            own_ports.add(BY_KEY[project.pairs_with].port)
        for link in project.links():
            if link["kind"] == "local":
                # A local opener points at this project, or - for a model arm - at the
                # project whose grader scores it.
                assert any(
                    link["href"].startswith(f"http://127.0.0.1:{port}") for port in own_ports
                ), link
            else:
                assert link["href"].startswith("https://")


def test_headline_figures_come_from_the_results_files() -> None:
    for project in PROJECTS:
        figures = headline_for(project)
        assert len(figures) == len(project.headline), project.key
        assert all(figure["value"] != "-" for figure in figures), project.key


def test_a_missing_results_file_yields_no_figures_rather_than_an_error(tmp_path) -> None:
    from dataclasses import replace

    broken = replace(PROJECTS[0], folder="does_not_exist")
    assert headline_for(broken) == []
    del tmp_path


def test_dig_returns_none_for_a_path_that_is_not_there() -> None:
    data = {"a": {"b": 1}}
    assert dig(data, "a.b") == 1
    assert dig(data, "a.c") is None
    assert dig(data, "x.y.z") is None
    assert dig(data, "a.b.c") is None  # b is not a dict


@pytest.mark.parametrize(
    ("value", "how", "expected"),
    [
        (None, "int", "-"),
        (1234, "int", "1,234"),
        (0.2842, "float", "0.2842"),
        (0.038, "percent", "3.8%"),
        (25183.71, "money", "+25,184 PKR"),
    ],
)
def test_values_are_formatted_for_reading(value: object, how: str, expected: str) -> None:
    assert format_value(value, how, {}) == expected


def test_an_over_format_reads_its_denominator_from_the_same_file() -> None:
    data = {"m": {"killed": 38, "total": 38}}
    assert format_value(38, "over:m.total", data) == "38/38"
    assert format_value(38, "over:m.missing", data) == "38"


# ------------------------------------------------------------------ the panel


def test_the_panel_serves_its_page_and_the_shared_theme() -> None:
    status, body = call("GET", "/")
    assert status == 200
    assert b"<title>Observatory</title>" in body
    assert b'href="/theme.css"' in body
    assert call("GET", "/theme.css")[0] == 200


def test_the_page_only_fetches_routes_the_panel_serves() -> None:
    import re

    _, body = call("GET", "/")
    text = body.decode("utf-8")
    served = {path for _method, path in panel.routes}
    for match in re.finditer(r'(?:api|fetch)\("(/api/[a-z_/-]+)', text):
        assert match.group(1) in served, match.group(1)
    # The start/stop paths are built from a variable, so they are checked by name.
    assert ("POST", "/api/start") in panel.routes
    assert ("POST", "/api/stop") in panel.routes


def test_meta_lists_every_project_with_its_links() -> None:
    status, data = call("GET", "/api/meta")
    assert status == 200
    assert data["pages"] == PAGES
    assert [item["key"] for item in data["projects"]] == [p.key for p in PROJECTS]
    assert all(len(item["links"]) >= 5 for item in data["projects"])


def test_the_panel_reports_the_model_server_rather_than_assuming_it() -> None:
    # A model project will start without a model and then have nothing to answer with,
    # so a green "running" must not be the only thing the page says about it.
    status, data = call("GET", "/api/model")
    assert status == 200
    assert "reachable" in data
    assert isinstance(data["models"], list)
    assert data["reachable"] is bool(data["models"])
    assert data["cache"]["entries"] >= 0
    assert "committed cache" in data["note"] or "committed" in data["note"]


def test_status_reports_every_project_whether_running_or_not() -> None:
    status, data = call("GET", "/api/status")
    assert status == 200
    assert len(data["projects"]) == 6
    for item in data["projects"]:
        assert item["state"] in {
            "running",
            "stopped",
            "starting",
            "port taken by something else",
        }
        assert item["running"] is (item["state"] == "running")
        assert item["headline"]


def test_telemetry_says_so_rather_than_inventing_numbers_when_a_project_is_down() -> None:
    for project in PROJECTS:
        if supervisor.status(project).running:
            supervisor.stop(project)
    status, data = call("GET", "/api/telemetry")
    assert status == 200
    for key in BY_KEY:
        assert "unavailable" in data["projects"][key]
    assert data["observatory"]["requests"] > 0


def test_an_unknown_project_is_rejected_by_name() -> None:
    status, data = call("POST", "/api/start", {"project": "nope"})
    assert status == 400
    assert "unknown project" in data["error"]
    assert call("POST", "/api/stop", {"project": ""})[0] == 400
    assert call("GET", "/api/log?project=nope")[0] == 400
    assert call("GET", "/api/log?project=switchboard&lines=many")[0] == 400


def test_the_log_endpoint_answers_before_anything_has_been_written() -> None:
    status, data = call("GET", "/api/log?project=switchboard&lines=20")
    assert status == 200
    assert data["project"] == "switchboard"
    assert isinstance(data["lines"], list)


# -------------------------------------------------------------- the supervisor


@pytest.mark.parametrize("key", ["switchboard"])
def test_a_project_starts_serves_and_stops(key: str) -> None:
    project = BY_KEY[key]
    if supervisor.status(project).running:
        supervisor.stop(project)
    assert not port_answers(project.port)

    state = supervisor.start(project)
    try:
        assert state.running, state.detail
        assert state.pid and state.pid > 0
        assert load_registry()[key]["pid"] == state.pid

        # "Running" has to mean a request comes back, not that a process was spawned.
        for path in ("/", "/io", "/api/metrics"):
            with urllib.request.urlopen(project.base_url + path, timeout=20) as response:
                assert response.status == 200
                assert response.read()

        # Starting again is a no-op rather than a second process on a taken port.
        again = supervisor.start(project)
        assert again.running
        assert again.pid == state.pid

        counters = supervisor.metrics(project)
        assert counters["requests"] >= 3
        assert "unavailable" not in counters
    finally:
        stopped = supervisor.stop(project)
    assert stopped.state == "stopped"
    assert key not in load_registry()
    assert not port_answers(project.port)


def test_stopping_something_that_was_never_running_is_not_an_error() -> None:
    project = BY_KEY["guardian"]
    if supervisor.status(project).running:
        supervisor.stop(project)
    state = supervisor.stop(project)
    assert state.state == "stopped"
    assert "not running" in state.detail


def test_a_port_held_by_something_else_is_reported_as_such(monkeypatch) -> None:
    """The panel must not claim a server it did not start, because it cannot stop it.

    The port check is substituted rather than a real socket bound. An earlier version of
    this test did bind one, and spent several rounds failing for reasons that had nothing
    to do with the code: Windows lets a second process bind over an existing listener
    with SO_REUSEADDR, and a wedged socket owned by an exited process stays in netstat as
    LISTENING while refusing connections. Substituting the one function whose answer the
    decision depends on tests the decision, which is the part that was written here.
    """
    project = BY_KEY["policy-bench"]
    supervisor.forget(project)
    monkeypatch.setattr(supervisor, "port_answers", lambda port, timeout=0.4: True)

    state = supervisor.status(project)
    assert state.state == "port taken by something else"
    assert state.running is False
    assert state.stoppable is False
    assert state.pid is None
    assert f"port {project.port}" in state.detail
    assert "did not start" in state.detail

    # Starting must not spawn a doomed second process on a taken port, and stopping must
    # not report a stop that did not happen.
    assert supervisor.start(project).state == "port taken by something else"
    assert supervisor.stop(project).state == "port taken by something else"
    assert project.key not in load_registry()


def test_a_dead_process_in_the_registry_is_forgotten(monkeypatch) -> None:
    # The registry is a file: it outlives the processes it names. An entry whose pid is
    # gone must not be reported as running.
    from observatory.registry import save_registry

    project = BY_KEY["switchboard"]
    save_registry(
        {
            project.key: {
                "pid": 999_999_999,
                "port": project.port,
                "started_at": 0.0,
                "adopted": False,
            }
        }
    )
    monkeypatch.setattr(supervisor, "port_answers", lambda port, timeout=0.4: False)
    assert supervisor.status(project).state == "stopped"
    assert project.key not in load_registry(), "the stale entry should have been dropped"


def test_a_live_process_whose_port_is_silent_is_not_called_running(monkeypatch) -> None:
    # Spawned is not serving. This is the gap the panel exists to show: an import error
    # or a taken port leaves a process alive and nothing listening.
    from observatory.registry import save_registry

    project = BY_KEY["switchboard"]
    save_registry(
        {
            project.key: {
                "pid": 4321,
                "port": project.port,
                "started_at": 0.0,
                "adopted": False,
            }
        }
    )
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: True)
    monkeypatch.setattr(supervisor, "port_answers", lambda port, timeout=0.4: False)
    state = supervisor.status(project)
    assert state.state == "starting"
    assert state.running is False
    assert "port is not answering" in state.detail
    supervisor.forget(project)


def test_adopt_records_a_server_the_panel_did_not_start() -> None:
    project = BY_KEY["guardian"]
    if supervisor.status(project).running:
        supervisor.stop(project)
    state = supervisor.start(project)
    real_pid = state.pid
    try:
        assert state.running
        assert state.stoppable, "a server this panel started must be stoppable"

        # Forget it, as though the panel had been restarted with the server still up.
        supervisor.forget(project)
        assert supervisor.status(project).state == "port taken by something else"

        adopted = supervisor.adopt()
        assert project.key in adopted
        after = supervisor.status(project)
        assert after.running, "an adopted server is running, and must be shown as running"
        assert after.adopted is True

        # And it is honest about the limit: no pid, so no stopping it from here. The
        # alternative - guessing the pid from the port - was tried and removed, because
        # the pid it produced was not the process that had bound the port.
        assert after.stoppable is False
        assert after.pid is None
        refused = supervisor.stop(project)
        assert refused.running
        assert "cannot stop it" in refused.detail
        assert port_answers(project.port), "a refused stop must leave it running"
    finally:
        # The test started this process, so the test ends it: put the real pid back and
        # let the supervisor do it through its normal path.
        entries = load_registry()
        entries[project.key] = {
            "pid": real_pid,
            "port": project.port,
            "started_at": 0.0,
            "adopted": False,
        }
        from observatory.registry import save_registry

        save_registry(entries)
        supervisor.stop(project)
    assert not port_answers(project.port)
    assert project.key not in load_registry()


# ----------------------------------------------------------------- metrics


def test_metrics_count_requests_errors_and_timings() -> None:
    app = App("t")

    @app.route("GET", "/ok")
    def ok(_request: Request) -> Response:
        return Response.json({})

    @app.route("GET", "/boom")
    def boom(_request: Request) -> Response:
        raise RuntimeError("deliberate")

    app.handle("GET", "/ok")
    app.handle("GET", "/ok?x=1")  # the query must not split the route
    app.handle("GET", "/boom")
    app.handle("GET", "/missing")

    snapshot = app.metrics.snapshot()
    assert snapshot["requests"] == 4
    assert snapshot["errors"] == 1
    assert snapshot["client_errors"] == 1
    assert snapshot["routes"]["GET /ok"]["requests"] == 2
    assert snapshot["routes"]["GET /boom"]["errors"] == 1
    assert snapshot["routes"]["GET /missing"]["client_errors"] == 1
    assert snapshot["slowest_ms"] >= 0


def test_an_empty_metrics_snapshot_is_still_well_formed() -> None:
    snapshot = Metrics().snapshot()
    assert snapshot["requests"] == 0
    assert snapshot["slowest_ms"] == 0.0
    assert snapshot["routes"] == {}


def test_each_project_app_reports_its_own_counters() -> None:
    from projects.p01_switchboard.app import app as one
    from projects.p02_policy_bench.app import app as two
    from projects.p03_guardian.app import app as three

    for app in (one, two, three):
        status, body = (
            app.handle("GET", "/api/metrics").status,
            app.handle("GET", "/api/metrics").body,
        )
        assert status == 200
        data = json.loads(body)
        assert data["app"] == app.name
        assert data["requests"] > 0


# ------------------------------------------------------- the published page


def test_the_published_page_is_committed_and_current() -> None:
    # It is generated from the projects' own results files. A page typed by hand drifts
    # from the numbers it quotes and nothing notices.
    from scripts.build_pages import DOCS, build

    committed = (DOCS / "index.html").read_text(encoding="utf-8")
    assert committed == build(), "docs/index.html is stale; run: make pages"


def test_the_published_page_quotes_real_figures_and_links_out() -> None:
    from scripts.build_pages import DOCS

    html = (DOCS / "index.html").read_text(encoding="utf-8")
    for project in PROJECTS:
        assert project.source_url in html
        assert project.readme_url in html
        for figure in headline_for(project):
            assert figure["value"] in html, f"{project.key}: {figure}"
    assert "127.0.0.1:8100" in html, "the page must say how to reach the panel"
    assert "theme.css" in html


def test_the_published_page_ships_the_theme_and_disables_jekyll() -> None:
    from scripts.build_pages import DOCS

    assert (DOCS / "theme.css").is_file()
    assert (DOCS / ".nojekyll").is_file()
    theme = (DOCS / "theme.css").read_text(encoding="utf-8")
    assert ':root[data-theme="dark"]' in theme
    assert "@media (prefers-color-scheme: dark)" in theme


def test_the_published_page_quotes_the_real_test_count() -> None:
    """The badge number must be the suite's own size.

    A hand-written figure on a published page is the first thing to go stale. The count is
    taken by collecting the whole suite in a separate process rather than from this run's
    own session, which would report however many tests happened to be selected - 35 when
    this file is run alone, and the check would then pass or fail on the invocation rather
    than on the number.
    """
    import re
    import subprocess
    import sys

    from scripts.build_pages import TESTS

    out = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    found = re.search(r"(\d+) tests? collected", out.stdout)
    assert found, f"could not read the collected count: {out.stdout[-400:]}"
    collected = int(found.group(1))
    assert collected == TESTS, (
        f"docs/index.html claims {TESTS} tests but the suite collects {collected}; "
        f"set TESTS in scripts/build_pages.py and run: make pages"
    )
