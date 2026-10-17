"""Open each dashboard in a real browser, click what a reader clicks, and check it filled.

    uv run python scripts/ui_smoke.py

Each dashboard is a static page the server hands over untouched. Python never parses it, so
a reference error in one renderer leaves a blank panel behind a button and every test in
this repo stays green. ``scripts/check_dashboard_scripts.py`` catches a syntax error and a
field the API does not send; neither of them catches a handler wired to a selector that
does not exist, or a renderer that throws halfway through.

So this serves each app on a free port, drives headless Chrome over the DevTools protocol
(``scripts/ui_smoke.mjs``, no dependencies - Node's own WebSocket and Chrome's own
protocol), clicks through the panels and fails on an empty one. Console errors and page
exceptions fail the run on their own, because a panel that throws after rendering half of
itself still looks finished.

It skips - loudly, and without passing - when Node or Chrome is not on the machine. That is
why it is a script rather than a test: a check that quietly skips in CI is worse than no
check, and this one says so every time.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.webkit import BackgroundServer  # noqa: E402

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser() -> str | None:
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in CHROME_CANDIDATES:
        if pathlib.Path(candidate).is_file():
            return candidate
    return None


#: What a reader does on each page, and what has to be on the screen afterwards.
PLANS: dict[str, dict] = {
    "p01_switchboard": {
        "settle": 1500,
        "steps": [
            {"waitFor": "#scenario"},
            {"expectNoEmpty": ["#tiles", "#rules"]},
            {"click": "#t-build"},
            {"select": "#scenario", "value": "end_of_day_reconciliation"},
            {"select": "#currency", "value": "414"},
            {"click": "#go-build", "after": 900},
            {"waitFor": "#build-out"},
            {"expectText": "#build-out", "contains": "cutover"},
            {"expectText": "#amount-reading", "contains": "KWD"},
            {"select": "#flow-kind", "value": "invalid"},
            {"select": "#scenario", "value": "duplicate_trace_in_one_window"},
            {"click": "#go-build", "after": 900},
            {"expectText": "#build-out", "contains": "L15"},
        ],
    },
    "p02_policy_bench": {
        "settle": 2500,
        "steps": [
            {"waitFor": "#tiles"},
            {"click": "#t-time"},
            {"select": "#tl-size", "value": "12000"},
            {"click": "#go-time", "after": 500},
            {"waitFor": "#tl-vintages", "timeout": 60000},
            {
                "expectNoEmpty": [
                    "#tl-vintage-table",
                    "#tl-psi-tiles",
                    "#tl-psi-table",
                    "#tl-oot",
                    "#tl-censor-tiles",
                ]
            },
            {"expectText": "#tl-psi-table", "contains": "other_app_outstanding"},
            {"expectText": "#tl-oot", "contains": "net per loan"},
        ],
    },
    "p03_guardian": {
        "settle": 1800,
        "steps": [
            # The thread starts empty - there is nothing to say yet - so the check is on
            # the panel that boot does fill.
            {"waitFor": "#profile"},
            {"click": "#t-split"},
            {"select": "#sp-level", "value": "level_0"},
            {"select": "#sp-pattern", "value": "across_days"},
            {"click": "#go-split", "after": 900},
            {"waitFor": "#sp-out"},
            {"expectText": "#sp-out", "contains": "beyond_monthly_cap"},
            {"expectText": "#sp-caps", "contains": "Level 0"},
            {"select": "#sp-pattern", "value": "same_day"},
            {"click": "#go-split", "after": 900},
            {"expectText": "#sp-out", "contains": "beyond_daily_cap"},
        ],
    },
    "observatory": {
        "settle": 2500,
        "steps": [
            {"waitFor": "#projects"},
            {"expectText": "#projects", "contains": "Switchboard"},
            {"expectText": "#projects", "contains": "Measured on"},
            {"expectText": "#model-projects", "contains": "Spec to wire"},
            {"expectText": "#projects", "contains": "Dataset page"},
        ],
    },
    "p05_policy_from_prose": {
        "settle": 2500,
        "steps": [
            {"waitFor": "body"},
            {"expectText": "body", "contains": "What this language cannot say"},
            {"expectText": "body", "contains": "refused by the grammar"},
        ],
    },
}


def run(folder: str, plan: dict, browser: str, node: str) -> bool:
    module = (
        __import__("observatory.app", fromlist=["app"])
        if folder == "observatory"
        else __import__(f"projects.{folder}.app", fromlist=["app"])
    )
    with tempfile.TemporaryDirectory() as workspace, BackgroundServer(module.app) as server:
        plan_file = pathlib.Path(workspace) / "plan.json"
        plan_file.write_text(
            json.dumps({**plan, "url": server.base_url + "/"}),
            encoding="utf-8",
        )
        if True:
            result = subprocess.run(
                [node, str(ROOT / "scripts" / "ui_smoke.mjs"), browser, str(plan_file)],
                capture_output=True,
                text=True,
                check=False,
                cwd=ROOT,
            )
    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        print(f"BROKEN   {folder}: the browser driver printed nothing usable")
        print(result.stdout[-1500:])
        print(result.stderr[-1500:])
        return False

    if report.get("ok"):
        print(f"ok       {folder}: {len(report.get('log', []))} checks in a real browser")
        return True
    print(f"BROKEN   {folder}")
    for failure in report.get("failures", []):
        print(f"           {failure}")
    for problem in report.get("problems", []):
        print(f"           {problem}")
    return False


def main() -> int:
    node = shutil.which("node")
    browser = find_browser()
    if node is None or browser is None:
        print("Node and a Chromium browser are both needed; this machine has:")
        print(f"  node    {node or 'not found'}")
        print(f"  browser {browser or 'not found'}")
        print("SKIPPED, not passed.")
        return 0

    print(f"browser: {browser}")
    failures = [folder for folder, plan in PLANS.items() if not run(folder, plan, browser, node)]
    if failures:
        print(f"\n{len(failures)} dashboard(s) failed: {', '.join(failures)}")
        return 1
    print("\nevery panel rendered, every click did something, no console errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
