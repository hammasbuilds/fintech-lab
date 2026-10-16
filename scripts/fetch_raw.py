"""Download the four public datasets this repo measures against.

Nothing else in the repo touches the network. A dashboard that silently downloads 95 MB
the first time somebody opens it is not a dashboard anybody can trust, so fetching is one
explicit command and everything else fails with a message telling you to run it.

What it fetches, and why that one:

``nikhil1e9/loan-default``
    255,347 loan applications with the default outcome. The only file here with real
    outcomes at a size worth backtesting against.

``rikdifos/credit-card-approval-prediction``
    438,557 card applications and 1,048,575 monthly repayment records. Used as the real
    cohort where the outcome is mostly *missing*, which is the shape a credit team
    actually has.

``smat26/roman-urdu-dataset`` and ``yrrebeere/daraz-code-mixed-product-reviews``
    37,220 real Roman Urdu and code-mixed sentences. Not about payments at all: they are
    here to attest spellings, so the generated utterance set is written the way people
    write rather than the way the generator's author imagined.

Needs the Kaggle CLI and an API token in ``~/.kaggle/kaggle.json``. The CLI is not a
dependency of anything that runs: ``uv run --with kaggle`` borrows it for the download
and leaves the project's own dependency list empty.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared import datasets  # noqa: E402

DATASETS = (
    ("nikhil1e9/loan-default", "nikhil1e9_loan-default", "Loan_default.csv"),
    (
        "rikdifos/credit-card-approval-prediction",
        "rikdifos_credit-card-approval-prediction",
        "application_record.csv",
    ),
    (
        "smat26/roman-urdu-dataset",
        "smat26_roman-urdu-dataset",
        "Dataset/Roman Urdu DataSet.csv",
    ),
    (
        "yrrebeere/daraz-code-mixed-product-reviews",
        "yrrebeere_daraz-code-mixed-product-reviews",
        "daraz-code-mixed-product-reviews.csv",
    ),
)


def kaggle_available() -> bool:
    return shutil.which("kaggle") is not None or shutil.which("uv") is not None


def fetch(slug: str, folder: str, marker: str, force: bool = False) -> pathlib.Path:
    target = datasets.raw(folder)
    if (target / marker).is_file() and not force:
        print(f"have   {slug}")
        return target
    target.mkdir(parents=True, exist_ok=True)
    print(f"fetch  {slug}")
    command = [
        "uv",
        "run",
        "--with",
        "kaggle",
        "kaggle",
        "datasets",
        "download",
        "-d",
        slug,
        "-p",
        str(target),
        "--unzip",
    ]
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit(
            f"could not download {slug}. Check that ~/.kaggle/kaggle.json exists and "
            f"that the dataset is still published under that name."
        )
    return target


def main() -> None:
    force = "--force" in sys.argv
    if not kaggle_available():
        raise SystemExit("neither uv nor the kaggle CLI is on PATH")
    for slug, folder, marker in DATASETS:
        fetch(slug, folder, marker, force=force)
    print()
    print(f"raw data is in {datasets.raw()}")
    print("next: make datasets   (generates the two synthetic corpora and the manifest)")


if __name__ == "__main__":
    main()
