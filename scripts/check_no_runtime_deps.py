"""Fail if a runtime dependency has appeared.

The claim this repo makes is that every engine, server and dashboard in it runs on the
standard library. That claim decays the moment someone adds a convenience import, and it
decays quietly, so it is checked rather than trusted.
"""

from __future__ import annotations

import pathlib
import sys
import tomllib

ALLOWED_THIRD_PARTY_IN_DEV = {"pytest", "ruff"}


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = data["project"]["dependencies"]
    if runtime:
        print(f"FAIL: runtime dependencies appeared: {runtime}")
        return 1

    dev = data["project"]["optional-dependencies"]["dev"]
    names = {entry.split(">")[0].split("=")[0].split("[")[0].strip() for entry in dev}
    unexpected = names - ALLOWED_THIRD_PARTY_IN_DEV
    if unexpected:
        print(f"FAIL: unexpected dev dependencies: {sorted(unexpected)}")
        return 1

    # And the stronger check: nothing under shared/ or projects/ may import anything
    # outside the standard library.
    offenders: list[str] = []
    for path in sorted((root / "shared").rglob("*.py")) + sorted((root / "projects").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            module = stripped.split()[1].split(".")[0]
            if module in names:
                offenders.append(f"{path.relative_to(root)}:{number}: imports {module}")
    if offenders:
        print("FAIL: a third-party import reached the shipped code:")
        for offender in offenders:
            print(f"  {offender}")
        return 1

    print(f"runtime dependencies: none. dev: {sorted(names)}. shipped code imports: stdlib only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
