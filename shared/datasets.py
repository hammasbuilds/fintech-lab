"""Where the datasets live, and how a project proves which one it read.

Every project in this repo is now measured on a dataset of between 50,000 and one
million records. That is far too much to commit, so the repo ships three things instead
of the bulk:

* the **loader or generator** that produces it, in the project that uses it;
* a **manifest entry** - row count, byte size and a sha256 over the file - written by
  ``scripts/build_datasets.py`` and committed, so a reader can tell whether the numbers
  in a README were computed over the same bytes;
* a **committed sample** of the first few hundred rows, so the shape of a dataset is
  visible in the repo without downloading anything.

The root is ``data/`` beside this file's repository, or whatever ``FINTECH_DATA_ROOT``
points at - the machine this was built on keeps large files on another drive.

Nothing here reaches the network. Downloads are a separate, explicit step
(``scripts/fetch_raw.py``), because a dashboard that silently downloads 95 MB the first
time someone opens it is not a dashboard anyone can trust.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import pathlib
from collections.abc import Iterator

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "MANIFEST.json"
SAMPLES = ROOT / "data" / "samples"

#: The smallest and largest a dataset in this repo is allowed to be. Both ends matter:
#: the floor is what the user asked for, and the ceiling is what a pure-standard-library
#: pass over a file can still do in seconds rather than minutes.
MINIMUM_ROWS = 50_000
MAXIMUM_ROWS = 1_000_000

#: What a file is for, which decides whether the size policy applies to it.
ROLES = {
    "cohort": (
        "rows a measurement reports a rate over; has to hold between 50,000 and 1,000,000 of them"
    ),
    "lookup": (
        "a table another dataset is joined against, such as a repayment history keyed "
        "by account; counted and fingerprinted, but no size policy"
    ),
}


class DatasetMissing(RuntimeError):
    """Raised with the command that would produce the missing file."""


def data_root() -> pathlib.Path:
    override = os.environ.get("FINTECH_DATA_ROOT", "").strip()
    return pathlib.Path(override).expanduser().resolve() if override else ROOT / "data"


def raw(*parts: str) -> pathlib.Path:
    return data_root().joinpath("raw", *parts)


def generated(*parts: str) -> pathlib.Path:
    return data_root().joinpath("generated", *parts)


def require(path: pathlib.Path, how: str) -> pathlib.Path:
    """Return ``path``, or explain how to make it rather than failing with an OSError."""
    if not path.is_file():
        raise DatasetMissing(f"{path} is not here yet. Build it with: {how}")
    return path


def digest(path: pathlib.Path, chunk: int = 1 << 20) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            sha.update(block)
    return sha.hexdigest()


def count_rows(path: pathlib.Path, header: bool = True) -> int:
    """Count data rows without holding the file in memory."""
    with path.open("rb") as handle:
        lines = sum(1 for _ in handle)
    return max(lines - (1 if header else 0), 0)


def read_csv(path: pathlib.Path, limit: int | None = None) -> Iterator[dict[str, str]]:
    """Stream a CSV as dicts. The large files here are read once and reduced."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if limit is not None and index >= limit:
                return
            yield row


def read_jsonl(path: pathlib.Path, limit: int | None = None) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if limit is not None and index >= limit:
                return
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: pathlib.Path, rows: Iterator[dict] | list[dict]) -> int:
    """Write a generated dataset. Returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            written += 1
    return written


def load_manifest() -> dict[str, dict]:
    if MANIFEST.is_file():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def record(
    key: str,
    path: pathlib.Path,
    *,
    rows: int,
    source: str,
    note: str,
    role: str = "cohort",
    sample_rows: int = 200,
    header: bool = True,
) -> dict:
    """Add one dataset to the manifest and write its committed sample.

    The sample is what makes the manifest checkable rather than decorative: a reader can
    open it and see the columns the loader claims to read.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(sorted(ROLES))}, not {role!r}")
    entry = {
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": digest(path),
        "source": source,
        "note": note,
        "role": role,
        "role_means": ROLES[role],
        # The size policy is about what a measurement reports a rate over. A lookup table
        # that a cohort is joined against is not a cohort, and holding it to the ceiling
        # would mean throwing away two thirds of a repayment history to satisfy a rule
        # that exists to keep a scored pass fast.
        "within_the_size_policy": (
            MINIMUM_ROWS <= rows <= MAXIMUM_ROWS if role == "cohort" else True
        ),
    }
    SAMPLES.mkdir(parents=True, exist_ok=True)
    sample = SAMPLES / (key.replace("/", "_") + ("".join(path.suffixes) or ".txt"))
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        head = []
        wanted = sample_rows + (1 if header else 0)
        for index, line in enumerate(handle):
            if index >= wanted:
                break
            head.append(line.rstrip("\n").rstrip("\r"))
    sample.write_text("\n".join(head) + "\n", encoding="utf-8", newline="\n")
    entry["sample"] = str(sample.relative_to(ROOT)).replace(os.sep, "/")
    entry["sample_rows"] = len(head) - (1 if header else 0)

    manifest = load_manifest()
    manifest[key] = entry
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return entry


def manifest_entry(key: str) -> dict:
    entry = load_manifest().get(key)
    if entry is None:
        raise DatasetMissing(f"{key} is not in {MANIFEST.name}. Run: make data")
    return entry
