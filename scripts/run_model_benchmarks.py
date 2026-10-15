"""Run the three model benchmarks one after another, never two at once.

    uv run python scripts/run_model_benchmarks.py --live

One at a time on purpose. Two benchmarks against the same Ollama server take turns
loading each other's model out of a 16 GB card, and the measured cost of that was about
forty seconds a call - most of the run and none of the work.

Every reply is cached, so this is safe to interrupt and resume: a second run re-reads what
the first already asked and only fills the gaps.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BENCHMARKS = (
    ("04 spec to wire", "projects.p04_spec_to_wire.benchmark"),
    ("05 policy from prose", "projects.p05_policy_from_prose.benchmark"),
    ("06 model against lexicon", "projects.p06_model_vs_lexicon.benchmark"),
)


def cache_count() -> int:
    return len(list((ROOT / "llm_cache").glob("*.json")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="ask the model for anything uncached")
    parser.add_argument("--only", nargs="*", default=None, help="module substrings to run")
    args = parser.parse_args()

    failed: list[str] = []
    for label, module in BENCHMARKS:
        if args.only and not any(part in module for part in args.only):
            continue
        before = cache_count()
        started = time.time()
        print(f"\n=== {label} ===", flush=True)
        command = [sys.executable, "-m", module]
        if args.live:
            command.append("--live")
        outcome = subprocess.run(command, cwd=str(ROOT), check=False)  # noqa: S603
        elapsed = time.time() - started
        print(
            f"--- {label}: exit {outcome.returncode}, "
            f"{cache_count() - before} new replies, {elapsed / 60:.1f} min",
            flush=True,
        )
        if outcome.returncode != 0:
            failed.append(label)

    print(f"\ncache now holds {cache_count()} replies")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
