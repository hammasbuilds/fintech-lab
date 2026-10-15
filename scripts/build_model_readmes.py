"""Write each model project's numbers section into its README, from its results file.

    uv run python scripts/build_model_readmes.py

The three model arms have numbers that change whenever a benchmark is re-run against a
different model or a longer case list. Typing those into a README by hand is how a
published figure and the file that produced it drift apart, so the section between the
``numbers`` markers is generated and CI fails if the committed copy differs.

The prose around the markers is written by hand and never touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

START = "<!-- numbers:start -->"
END = "<!-- numbers:end -->"

PROJECTS = (
    ("p04_spec_to_wire", "04"),
    ("p05_policy_from_prose", "05"),
    ("p06_model_vs_lexicon", "06"),
)


def _load(folder: str) -> dict:
    path = ROOT / "projects" / folder / "results.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _header(data: dict) -> list[str]:
    setup = data.get("setup", {})
    usage = data.get("usage", {})
    lines = [
        "## The numbers",
        "",
        f"From [`results.json`](results.json). Models: "
        f"**{', '.join(setup.get('models', [])) or 'none recorded'}**. "
        f"{usage.get('generated', 0)} replies generated, "
        f"{usage.get('served_from_cache', 0)} served from the committed cache, "
        f"{usage.get('wall_clock_seconds', 0)}s of wall clock, "
        f"{usage.get('hit_the_token_cap', 0)} answers cut off by the token cap.",
        "",
    ]
    incomplete = data.get("incomplete", [])
    if incomplete:
        lines += [
            f"> **{len(incomplete)} case(s) have no cached reply and were not run**, so the "
            f"tables below are not a full sweep. Re-run with `--live` and a local model. "
            f"They are listed in `results.json` under `incomplete`.",
            "",
        ]
    return lines


def numbers_04() -> list[str]:
    data = _load("p04_spec_to_wire")
    if not data:
        return ["## The numbers", "", "_Not measured yet; run `make bench04`._", ""]
    lines = _header(data)
    families = data.get("setup", {}).get("families", [])
    head = "| Arm | " + " | ".join(families) + " |"
    rule = "|---|" + "".join("---:|" for _ in families)
    lines += ["### Mean score by task family", "", head, rule]

    baseline = data.get("baseline", {}).get("summary", {})
    cells = " | ".join(f"{baseline.get(f, {}).get('mean_score', 0):.2f}" for f in families)
    lines.append(f"| **project 01's generator (code)** | {cells} |")
    for name, block in data.get("by_model_and_arm", {}).items():
        cells = " | ".join(f"{block.get(f, {}).get('mean_score', 0):.2f}" for f in families)
        lines.append(f"| {name} | {cells} |")

    lines += [
        "",
        "0 to 1. The generator is 1.00 by construction and is shown because a model score "
        "with no baseline beside it invites the reader to supply their own idea of what "
        "good looks like.",
        "",
        "### How many answers were usable at all",
        "",
        "| Arm | Parsed | Packs | Invariants hold |",
        "|---|---:|---:|---:|",
    ]
    for name, block in data.get("by_model_and_arm", {}).items():
        total = sum(cell["cases"] for cell in block.values())
        lines.append(
            f"| {name} | {sum(c['parsed'] for c in block.values())}/{total} "
            f"| {sum(c['packs'] for c in block.values())}/{total} "
            f"| {sum(c['invariants_hold'] for c in block.values())}/{total} |"
        )
    lines += [
        "",
        "These stay separate on purpose: an unparseable message is not 90% of a message.",
        "",
    ]
    return lines


def numbers_05() -> list[str]:
    data = _load("p05_policy_from_prose")
    if not data:
        return ["## The numbers", "", "_Not measured yet; run `make bench05`._", ""]
    lines = _header(data)
    lines += [
        "### Turning prose into a policy that runs",
        "",
        "| Arm | Compiles | Identical behaviour | Mean agreement | Compiled but different |",
        "|---|---:|---:|---:|---:|",
    ]

    def compile_row(name: str, block: dict) -> str:
        return (
            f"| {name} | {block['compiled']}/{block['cases']} "
            f"| {block['behaved_identically']}/{block['cases']} "
            f"| {block['mean_agreement']:.3f} | {block['compiled_but_different']} |"
        )

    base = data.get("baseline", {}).get("compile", {})
    if base:
        lines.append(compile_row("**project 02's own policies**", base))
    for name, block in data.get("by_model_and_arm", {}).items():
        if block.get("compile"):
            lines.append(compile_row(name, block["compile"]))

    lines += [
        "",
        "Agreement is over all the borrowers in the cohort. **Compiled but different** is "
        "the column that matters: it parsed, it ran, and it decided a different set of "
        "applicants than the document it came from.",
        "",
        "### The decline and approval notices",
        "",
        "| Arm | Mean checks | All checks | Safe | Invented a reason | Promised something |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    def notice_row(name: str, block: dict) -> str:
        return (
            f"| {name} | {block['mean_score']:.2f} "
            f"| {block['all_checks_passed']}/{block['cases']} "
            f"| {block['safe']}/{block['cases']} "
            f"| {block['invented_a_reason']} | {block['promised_something']} |"
        )

    base_notice = data.get("baseline", {}).get("notice", {})
    if base_notice:
        lines.append(notice_row("**project 02's templates**", base_notice))
    for name, block in data.get("by_model_and_arm", {}).items():
        if block.get("notice"):
            lines.append(notice_row(name, block["notice"]))
    lines.append("")
    return lines


def numbers_06() -> list[str]:
    data = _load("p06_model_vs_lexicon")
    if not data:
        return ["## The numbers", "", "_Not measured yet; run `make bench06`._", ""]
    lines = _header(data)
    lines += [
        "### The same cases, both ways",
        "",
        "| Arm | Amounts | Resolved | Asked | Verdicts | Blind spot | False blocks |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    def row(name: str, block: dict) -> str:
        amount = block.get("amount", {})
        action = block.get("action", {})
        co = block.get("coercion", {})
        return (
            f"| {name} "
            f"| {amount.get('correct', 0)}/{amount.get('cases', 0)} "
            f"| {action.get('resolved_correctly', 0)}/{action.get('determined_cases', 0)} "
            f"| {action.get('asked_instead_of_guessing', 0)}/{action.get('underspecified_cases', 0)} "
            f"| {co.get('correct', 0)}/{co.get('cases', 0)} "
            f"| {co.get('caught_of_those', 0)}/{co.get('cases_the_lexicon_misses', 0)} "
            f"| {co.get('ordinary_traffic_blocked', 0)}/{co.get('ordinary_traffic', 0)} |"
        )

    if data.get("rules"):
        lines.append(row("**project 03's rules**", data["rules"]))
    for name, block in data.get("by_model_and_arm", {}).items():
        lines.append(row(name, block))
    lines += [
        "",
        "Every cell is correct over attempted. The rules row is the shipped product on the "
        "same cases through the same grader.",
        "",
    ]
    return lines


BUILDERS = {"04": numbers_04, "05": numbers_05, "06": numbers_06}


def main() -> int:
    changed = []
    for folder, number in PROJECTS:
        path = ROOT / "projects" / folder / "README.md"
        text = path.read_text(encoding="utf-8")
        if START not in text or END not in text:
            print(f"FAIL: {path.name} has no numbers markers")
            return 1
        head, rest = text.split(START, 1)
        _, tail = rest.split(END, 1)
        body = "\n".join(BUILDERS[number]())
        rebuilt = f"{head}{START}\n\n{body}\n{END}{tail}"
        if rebuilt != text:
            path.write_text(rebuilt, encoding="utf-8", newline="\n")
            changed.append(folder)
        print(f"{'updated' if folder in changed else 'unchanged'}  {folder}/README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
