"""The dashboards' own checks: they parse, and their renderers' fields exist.

A dashboard here is a static page the server hands over untouched, so Python never sees
its JavaScript. Two failures ship easily and silently:

* a **syntax error** - the browser refuses the whole script, every panel is blank, and no
  Python test notices
* a **renderer reading a field the endpoint does not send** - the page prints "undefined"
  and the API tests are perfectly happy

The first is caught by ``node --check`` when Node is on the machine, and skipped - loudly -
when it is not. The second needs no browser at all: the contract table in
``scripts/check_dashboard_scripts.py`` lists the dotted paths each renderer reads, and
every one has to resolve in a real response.

The full browser pass lives in ``scripts/ui_smoke.py`` and is not a test, on purpose: it
needs Chrome, and a check that quietly skips in CI is worse than no check. ``make ui-smoke``
runs it and says plainly when it cannot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.check_dashboard_scripts import CONTRACTS, PAGES, dig, script_of  # noqa: E402


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.parent.parent.name)
def test_every_dashboard_script_parses(page: Path, tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH, so the page cannot be parsed here")
    target = tmp_path / "page.js"
    target.write_text(script_of(page), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(target)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr[-1200:]


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda item: f"{item[0]}{item[2]}")
def test_every_field_a_renderer_reads_is_in_the_reply(contract) -> None:
    folder, method, route, payload, paths = contract
    module = (
        __import__("observatory.app", fromlist=["app"])
        if folder == "observatory"
        else __import__(f"projects.{folder}.app", fromlist=["app"])
    )
    body = dict(payload)
    chosen = body.pop("policy_file", None)
    if chosen:
        body["source"] = (ROOT / "projects" / folder / chosen).read_text(encoding="utf-8")
    response = module.app.handle(method, route, json.dumps(body).encode() if body else b"")
    assert response.status == 200, response.body
    raw = response.body if isinstance(response.body, str) else response.body.decode("utf-8")
    reply = json.loads(raw)
    missing = [path for path in paths if not dig(reply, path)[0]]
    assert missing == [], f"{route} does not send: {', '.join(missing)}"


def test_the_pages_do_not_reach_for_a_helper_they_lack() -> None:
    """``mount()`` exists on two of the three pages, and that cost a blank panel once.

    The amount reading on project 01 called it, Python never parsed the file, every test
    passed, and the browser threw ReferenceError the moment the currency changed.
    """
    for page in PAGES:
        script = script_of(page)
        # Comments mention these helpers by name, so the search is for a call rather than
        # for the word: a line of prose about mount() is not a call to it.
        code = chr(10).join(
            line for line in script.splitlines() if not line.strip().startswith("//")
        )
        for helper in ("mount", "pill", "table", "lineChart"):
            used = f"{helper}(" in code
            defined = f"function {helper}(" in code or f"const {helper} =" in code
            if used:
                assert defined, f"{page.parent.parent.name} calls {helper}() and never defines it"


def test_every_dashboard_has_a_browser_plan() -> None:
    """A panel nobody clicks in the smoke run is a panel nobody has seen work."""
    from scripts.ui_smoke import PLANS

    for folder in ("p01_switchboard", "p02_policy_bench", "p03_guardian"):
        assert folder in PLANS, f"{folder} has no browser plan"
        steps = PLANS[folder]["steps"]
        assert any("click" in step for step in steps), f"{folder}'s plan clicks nothing"
        assert any("expectText" in step or "expectNoEmpty" in step for step in steps), (
            f"{folder}'s plan asserts nothing"
        )
