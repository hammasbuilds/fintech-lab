"""Parse every dashboard's JavaScript, and check each renderer's fields exist in the API.

Two failures are easy to ship in a page that is served as a static file and never built:

1. **A syntax error.** Python never sees this file. The server hands it over, the browser
   fails to parse it, and the page renders as a blank panel with an error only in a console
   nobody has open. ``node --check`` catches it in a second if Node is on the machine - and
   the check skips rather than lies when it is not.
2. **A renderer reading a field the endpoint does not send.** The JavaScript says
   ``data.stability.psi`` and the API sends ``data.psi``; the page shows "undefined" and
   the tests stay green, because the Python side is perfectly happy.

The second is what the pairs below are for: each one names an endpoint, the payload to send
it, and the dotted paths the page reads out of the response. Every path has to resolve.

    uv run python scripts/check_dashboard_scripts.py
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAGES = sorted((ROOT / "projects").glob("p0*/static/index.html"))

#: endpoint, request payload, and the dotted paths the dashboard reads from the reply.
CONTRACTS: tuple[tuple[str, str, str, dict, tuple[str, ...]], ...] = (
    (
        "p02_policy_bench",
        "POST",
        "/api/timeline",
        {"policy_file": "policies/nano_v4_compliant.policy", "size": 2000},
        (
            "borrowers",
            "note",
            "split_month",
            "vintages.curves",
            "vintages.reading",
            "stability.psi",
            "characteristics.monthly_income.psi",
            "out_of_time.cutoff_chosen_on_the_early_window",
            "out_of_time.promised_in_sample.approvals",
            "out_of_time.promised_in_sample.approval_rate",
            "out_of_time.promised_in_sample.bad_rate",
            "out_of_time.delivered_out_of_time.approvals",
            "out_of_time.net_per_loan_promised",
            "out_of_time.net_per_loan_delivered",
            "out_of_time.optimism_per_loan",
            "out_of_time.best_for_the_late_window.threshold",
            "out_of_time.reading",
            "approvals_only.approved_bad_rate",
            "approvals_only.population_bad_rate",
            "approvals_only.share_of_the_population_with_no_observable_outcome",
            "approvals_only.declined_who_would_have_repaid",
            "approvals_only.reading",
        ),
    ),
    (
        "p03_guardian",
        "POST",
        "/api/split",
        {"wallet_level": "level_0", "pattern": "across_days", "legs": 4},
        (
            "caps.label",
            "caps.daily",
            "caps.monthly",
            "caps.yearly",
            "legs",
            "per_leg",
            "total",
            "legs_detail.leg",
            "legs_detail.day",
            "legs_detail.amount",
            "legs_detail.day_total",
            "legs_detail.month_total",
            "legs_detail.level",
            "legs_detail.escalated",
            "legs_detail.signals",
            "crossed_the_daily_cap_on_leg",
            "crossed_the_monthly_cap_on_leg",
            "first_escalation",
            "reading",
        ),
    ),
    (
        # The control panel builds its cards from /api/status, and the dataset line was
        # written against /api/meta - so it was dead from the day it was added. Nothing in
        # Python noticed, because both endpoints were fine.
        "observatory",
        "GET",
        "/api/status",
        {},
        (
            "projects.key",
            "projects.name",
            "projects.state",
            "projects.port",
            "projects.headline",
            "projects.links",
            "projects.datasets",
            "projects.needs_model",
        ),
    ),
)


def script_of(page: pathlib.Path) -> str:
    text = page.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", text, re.DOTALL)
    if not blocks:
        raise SystemExit(f"{page}: no <script> block")
    return "\n".join(blocks)


def check_syntax() -> int:
    node = shutil.which("node")
    if node is None:
        print("node is not on PATH, so the syntax check is skipped (not passed)")
        return 0
    problems = 0
    for page in PAGES:
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / "page.js"
            target.write_text(script_of(page), encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(target)], capture_output=True, text=True, check=False
            )
        label = page.parent.parent.name
        if result.returncode == 0:
            print(f"parses   {label}")
        else:
            problems += 1
            print(f"BROKEN   {label}")
            print(result.stderr.strip()[:900])
    return problems


def dig(payload: object, path: str) -> tuple[bool, object]:
    current = payload
    for step in path.split("."):
        if isinstance(current, list):
            if not current:
                return False, None
            current = current[0]
        if not isinstance(current, dict) or step not in current:
            return False, None
        current = current[step]
    return True, current


def check_contracts() -> int:
    problems = 0
    for folder, method, route, payload, paths in CONTRACTS:
        module = (
            __import__("observatory.app", fromlist=["app"])
            if folder == "observatory"
            else __import__(f"projects.{folder}.app", fromlist=["app"])
        )
        app = module.app
        body = dict(payload)
        chosen = body.pop("policy_file", None)
        if chosen:
            body["source"] = (ROOT / "projects" / folder / chosen).read_text(encoding="utf-8")
        response = app.handle(method, route, json.dumps(body).encode() if body else b"")
        if response.status != 200:
            print(f"BROKEN   {folder} {route} returned {response.status}")
            problems += 1
            continue
        raw = response.body if isinstance(response.body, str) else response.body.decode("utf-8")
        reply = json.loads(raw)
        missing = [path for path in paths if not dig(reply, path)[0]]
        if missing:
            problems += 1
            print(f"BROKEN   {folder} {route} is missing: {', '.join(missing)}")
        else:
            print(f"matches  {folder} {route} ({len(paths)} fields the page reads)")
    return problems


def main() -> int:
    problems = check_syntax() + check_contracts()
    if problems:
        print(f"\n{problems} problem(s)")
        return 1
    print("\nevery dashboard parses, and every field its renderers read is in the reply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
