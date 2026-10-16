"""Every project's dataset page, and the generated README sections.

The pages are built per request from the manifest and the committed results, so these
tests are about the thing a reader would notice: that the page exists, that it names the
dataset the project is actually scored on, and that it never prints a sample size as if it
were a rate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from observatory.registry import PROJECTS
from projects.p01_switchboard.app import app as switchboard
from projects.p02_policy_bench.app import app as policy_bench
from projects.p03_guardian.app import app as guardian
from projects.p04_spec_to_wire.app import app as spec_to_wire
from projects.p05_policy_from_prose.app import app as policy_from_prose
from projects.p06_model_vs_lexicon.app import app as model_vs_lexicon
from shared import datapage, datasets

ROOT = Path(__file__).resolve().parent.parent

APPS = {
    "01": switchboard,
    "02": policy_bench,
    "03": guardian,
    "04": spec_to_wire,
    "05": policy_from_prose,
    "06": model_vs_lexicon,
}


def body(response) -> str:
    return response.body if isinstance(response.body, str) else response.body.decode("utf-8")


@pytest.mark.parametrize("number", sorted(APPS))
def test_every_project_serves_a_dataset_page(number: str) -> None:
    response = APPS[number].handle("GET", "/datasets", b"")
    assert response.status == 200
    page = body(response)
    assert "<title>" in page
    assert "data/MANIFEST.json" in page
    assert "theme.css" in page


@pytest.mark.parametrize("number", sorted(APPS))
def test_the_dataset_api_names_the_keys_the_page_shows(number: str) -> None:
    response = APPS[number].handle("GET", "/api/datasets", b"")
    assert response.status == 200
    payload = json.loads(body(response))
    assert payload["datasets"], number
    for key, entry in payload["datasets"].items():
        assert key.startswith(("generated/", "real/")), key
        assert (
            "missing" in entry
            or entry["rows"] >= datasets.MINIMUM_ROWS
            or (entry.get("role") == "lookup")
        )


def test_the_manifest_keys_each_project_names_are_real_keys() -> None:
    manifest = datasets.load_manifest()
    if not manifest:
        pytest.skip("no manifest yet; run make data")
    for project in PROJECTS:
        for key in project.dataset_keys:
            assert key in manifest, f"{project.key} names {key}, which is not in the manifest"


def test_the_observatory_reports_each_projects_dataset_sizes() -> None:
    manifest = datasets.load_manifest()
    if not manifest:
        pytest.skip("no manifest yet; run make data")
    for project in PROJECTS:
        rows = project.datasets()
        assert rows, project.key
        for entry in rows:
            assert entry["built"] is True, f"{project.key}: {entry['key']} is not built"
            assert entry["rows"] >= datasets.MINIMUM_ROWS


def test_a_sample_is_always_shown_with_the_size_of_what_it_sampled() -> None:
    """A count read as a rate is the easiest way to mislead with a true number."""
    for number in ("04", "05", "06"):
        page = body(APPS[number].handle("GET", "/datasets", b""))
        assert "share of the" in page, number
        assert "sample" in page.lower(), number


def test_the_value_formatter_does_not_invent_precision() -> None:
    assert datapage.show(None) == "not measured"
    assert datapage.show(200000) == "200,000"
    assert datapage.show(0.9939) == "0.9939"
    assert datapage.show(1.0) == "1"
    assert datapage.show(True) == "yes"


def test_a_figure_without_a_denominator_still_renders() -> None:
    page = datapage.Page(
        title="t",
        subtitle="s",
        dataset_keys=["generated/p01_messages"],
        blocks=[datapage.Block(title="b", figures=[datapage.Figure("label", "1")])],
    )
    assert "label" in datapage.render(page)


def test_a_missing_dataset_says_how_to_build_it_rather_than_showing_nothing() -> None:
    page = datapage.Page(title="t", subtitle="s", dataset_keys=["generated/not-a-dataset"])
    rendered = datapage.render(page)
    assert "make data" in rendered


# ------------------------------------------------------------- the generated READMEs


def test_the_dataset_sections_in_the_readmes_are_current() -> None:
    """The at-scale tables are generated; a stale copy would be a published wrong number."""
    if not datasets.load_manifest():
        pytest.skip("no manifest yet; run make data")
    before = {
        path: path.read_text(encoding="utf-8")
        for path in [ROOT / "README.md"] + sorted((ROOT / "projects").glob("p0*/README.md"))
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_dataset_readmes.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-800:]
    changed = [
        str(path.relative_to(ROOT))
        for path, text in before.items()
        if path.read_text(encoding="utf-8") != text
    ]
    assert changed == [], (
        f"these README sections are stale: {changed}. "
        f"Run: uv run python scripts/build_dataset_readmes.py"
    )


def test_every_project_readme_has_the_generated_markers() -> None:
    for path in sorted((ROOT / "projects").glob("p0*/README.md")):
        text = path.read_text(encoding="utf-8")
        assert "<!-- datasets:start -->" in text, path.name
        assert "<!-- datasets:end -->" in text, path.name


def test_the_root_readme_quotes_the_size_policy_and_the_row_total() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "50,000 and one million rows" in text
    manifest = datasets.load_manifest()
    if not manifest:
        pytest.skip("no manifest yet; run make data")
    assert "distinct rows across the repo" in text


def test_the_readme_input_output_blocks_are_what_the_demo_prints() -> None:
    """These used to be pasted by hand, and showed a Gini the repo no longer produced."""
    readme = ROOT / "README.md"
    before = readme.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_readme_io.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-800:]
    assert readme.read_text(encoding="utf-8") == before, (
        "the README's Input / Output blocks are stale. "
        "Run: uv run python scripts/build_readme_io.py"
    )


def test_every_marked_io_block_is_non_empty() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for key in ("01", "02", "03"):
        start = text.index(f"<!-- io:{key}:start -->")
        end = text.index(f"<!-- io:{key}:end -->")
        block = text[start:end]
        assert "INPUT" in block, key
        assert len(block.splitlines()) > 20, key
