"""The dataset layer: the manifest, the size policy, and the loaders.

Tests that need a 128 MB file skip themselves **individually** rather than behind a
module-level guard, because a module-level skip removes the whole file during collection
and the count of tests silently drops. Everything that can be checked without the data -
the policy arithmetic, the committed samples, the manifest's shape - runs always.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from projects.p01_switchboard import corpus
from projects.p02_policy_bench import cardbook, loanbook
from projects.p03_guardian import generator
from projects.p04_spec_to_wire import bank as wire_bank
from projects.p05_policy_from_prose import bank as policy_bank
from shared import datasets

#: Every dataset the repo claims, and the project that reads it.
EXPECTED = {
    corpus.KEY: "01",
    generator.KEY: "03",
    wire_bank.KEY: "04",
    policy_bank.KEY: "05",
    loanbook.KEY: "02",
    cardbook.KEY_APPLICATIONS: "02",
    cardbook.KEY_RECORDS: "02",
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return datasets.load_manifest()


def have(path) -> bool:
    return path.is_file()


# ------------------------------------------------------------------ the policy itself


def test_the_size_policy_is_the_one_the_readmes_quote() -> None:
    assert datasets.MINIMUM_ROWS == 50_000
    assert datasets.MAXIMUM_ROWS == 1_000_000


@pytest.fixture
def scratch_manifest(tmp_path, monkeypatch):
    """Point the manifest and the sample folder at a temporary directory.

    Without this, recording a test dataset writes a fictional entry into the repo's own
    data/MANIFEST.json - which the next test then reads and fails on. A test that leaves
    state behind in a committed file is worse than no test.
    """
    monkeypatch.setattr(datasets, "MANIFEST", tmp_path / "MANIFEST.json")
    monkeypatch.setattr(datasets, "SAMPLES", tmp_path / "samples")
    monkeypatch.setattr(datasets, "ROOT", tmp_path)
    sample = tmp_path / "rows.csv"
    sample.write_text("a\n" + "1\n" * 10, encoding="utf-8", newline="\n")
    return sample


def test_a_lookup_table_is_exempt_from_the_ceiling_and_a_cohort_is_not(
    scratch_manifest,
) -> None:
    """The repayment history has 1,048,575 rows and nothing reports a rate over it."""
    cohort = datasets.record(
        "test/cohort",
        scratch_manifest,
        rows=2_000_000,
        source="test",
        note="test",
        role="cohort",
    )
    lookup = datasets.record(
        "test/lookup",
        scratch_manifest,
        rows=2_000_000,
        source="test",
        note="test",
        role="lookup",
    )
    assert cohort["within_the_size_policy"] is False
    assert lookup["within_the_size_policy"] is True
    assert "no size policy" in lookup["role_means"]


def test_recording_a_dataset_leaves_the_repo_manifest_alone(scratch_manifest) -> None:
    """These tests used to write "test/cohort" into the committed manifest."""
    datasets.record(
        "test/cohort",
        scratch_manifest,
        rows=60_000,
        source="test",
        note="test",
        role="cohort",
    )
    assert "test/cohort" in json.loads(datasets.MANIFEST.read_text(encoding="utf-8"))
    real = pathlib.Path(__file__).resolve().parent.parent / "data" / "MANIFEST.json"
    if real.is_file():
        committed = json.loads(real.read_text(encoding="utf-8"))
        assert [key for key in committed if key.startswith("test/")] == []


def test_an_unknown_role_is_refused(scratch_manifest) -> None:
    with pytest.raises(ValueError, match="role must be one of"):
        datasets.record("test/x", scratch_manifest, rows=1, source="t", note="t", role="whatever")


def test_a_missing_dataset_says_how_to_build_it() -> None:
    with pytest.raises(datasets.DatasetMissing, match="make data"):
        datasets.require(datasets.generated("not-here.jsonl"), "make data")


def test_row_counting_does_not_count_the_header() -> None:
    path = datasets.SAMPLES / "real_loan_book.csv"
    if not path.is_file():
        pytest.skip("the committed sample is not here")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert datasets.count_rows(path) == len(lines) - 1


# ---------------------------------------------------------------------- the manifest


def test_every_dataset_the_repo_reads_is_in_the_manifest(manifest: dict) -> None:
    if not manifest:
        pytest.skip("no manifest yet; run make data")
    missing = sorted(set(EXPECTED) - set(manifest))
    assert missing == [], f"not in the manifest: {missing}"


def test_every_cohort_in_the_manifest_is_inside_the_size_policy(manifest: dict) -> None:
    if not manifest:
        pytest.skip("no manifest yet; run make data")
    for key, entry in manifest.items():
        if entry.get("role") == "lookup":
            continue
        assert entry["within_the_size_policy"], (
            f"{key} has {entry['rows']:,} rows, outside "
            f"{datasets.MINIMUM_ROWS:,}-{datasets.MAXIMUM_ROWS:,}"
        )
        assert entry["rows"] >= datasets.MINIMUM_ROWS


def test_each_manifest_entry_carries_a_fingerprint_and_a_sample(manifest: dict) -> None:
    if not manifest:
        pytest.skip("no manifest yet; run make data")
    for key, entry in manifest.items():
        assert len(entry["sha256"]) == 64, key
        assert entry["bytes"] > 0, key
        assert entry["sample"].startswith("data/samples/"), key
        assert entry["sample_rows"] > 0, key


def test_the_committed_samples_exist_and_are_small(manifest: dict) -> None:
    if not manifest:
        pytest.skip("no manifest yet; run make data")
    for key, entry in manifest.items():
        sample = datasets.ROOT / entry["sample"]
        assert sample.is_file(), f"{key}: {sample} is in the manifest and not on disk"
        assert sample.stat().st_size < 2_000_000, f"{key}: the committed sample is large"


# ------------------------------------------------------------------------ the loaders


def test_the_loan_book_loads_with_its_documented_shape() -> None:
    if not have(loanbook.path()):
        pytest.skip("the loan book is not here; run make fetch")
    book = loanbook.load(limit=500)
    assert len(book) == 500
    first = book[0]
    assert first.monthly_income == first.annual_income // 12
    assert 300 <= first.credit_score <= 849
    assert first.would_default in {0, 1}
    record = first.record()
    assert "would_default" not in record, "the label must never reach a policy"
    assert "id" not in record


def test_the_loan_book_summary_reports_the_published_base_rate() -> None:
    if not have(loanbook.path()):
        pytest.skip("the loan book is not here; run make fetch")
    book = loanbook.load()
    summary = loanbook.summary(book)
    assert summary["rows"] == 255_347
    assert summary["defaults"] == 29_653
    assert summary["base_default_rate"] == pytest.approx(0.1161, abs=0.0002)


def test_a_pensioner_is_flagged_rather_than_scored_as_minus_twelve_thousand_months() -> None:
    """DAYS_EMPLOYED carries 365243 for pensioners: positive where every value is negative."""
    if not have(cardbook.applications_path()):
        pytest.skip("the card book is not here; run make fetch")
    book, summary = cardbook.load(limit=5_000)
    assert summary["pensioners_flagged"] >= 0
    for item in book:
        assert item.months_employed >= 0, item
        if item.retired:
            assert item.months_employed == 0


def test_an_unstated_occupation_is_kept_as_not_stated() -> None:
    if not have(cardbook.applications_path()):
        pytest.skip("the card book is not here; run make fetch")
    book, _ = cardbook.load(limit=5_000)
    assert any(item.occupation == "not stated" for item in book)
    assert all(item.occupation for item in book)


def test_the_two_definitions_of_bad_are_both_offered() -> None:
    assert set(cardbook.BAD_DEFINITIONS) == {"ever_30_plus_dpd", "ever_60_plus_dpd"}
    with pytest.raises(cardbook.CardBookError, match="not a definition of bad"):
        cardbook.load(bad_definition="ever_late_at_all")


# ------------------------------------------------------------------- generated banks


def test_the_wire_bank_answers_match_the_product() -> None:
    if not have(corpus.path()):
        pytest.skip("the corpus is not here; run make data")
    if not have(wire_bank.path()):
        pytest.skip("the wire bank is not here; run make data")
    checked = 0
    for task in wire_bank.read(limit=300):
        answer = wire_bank.answer_with_the_product(task)
        if task["family"] == "negative":
            assert set(task["also_true"]) & set(answer), task["id"]
        else:
            assert answer == task["answer"], task["id"]
        checked += 1
    assert checked == 300


def test_every_fault_in_the_wire_bank_survives_being_packed() -> None:
    """A two-digit trace number was padded back to six, so the task had no fault in it."""
    if not have(wire_bank.path()):
        pytest.skip("the wire bank is not here; run make data")
    seen: set[str] = set()
    for task in wire_bank.read(limit=4_000):
        if task["family"] != "negative":
            continue
        seen.add(task["answer"])
        assert wire_bank.diagnose(task["message"]), task["id"]
    assert seen, "no negative tasks in the first 4,000"
    assert "trace_too_short" not in seen


def test_the_policy_bank_compiles_round_trips_and_evaluates() -> None:
    if not have(policy_bank.path()):
        pytest.skip("the policy bank is not here; run make data")
    from projects.p02_policy_bench import cohort as synthetic
    from projects.p02_policy_bench import dsl, engine

    people = [person.record() for person in synthetic.generate()[:20]]
    for row in policy_bank.read(limit=200):
        policy = dsl.compile_policy(row["reference"])
        assert dsl.compile_policy(policy.render()).render() == policy.render(), row["id"]
        for person in people:
            engine.evaluate(policy, person)


def test_the_prose_and_the_policy_describe_the_same_rules() -> None:
    """The prose used to say the opposite of the policy for two of the eight fields."""
    if not have(policy_bank.path()):
        pytest.skip("the policy bank is not here; run make data")
    for row in policy_bank.read(limit=400):
        tree = row["tree"]
        prose = row["prose"]
        for rule in tree["rules"] + tree["gates"]:
            assert rule["phrase"] in prose, (row["id"], rule["phrase"])
            value = rule["value"]
            if isinstance(value, int):
                assert f"{value:,}" in rule["phrase"] or str(value) in rule["phrase"]


def test_the_generated_utterances_are_the_set_project_06_compares_against() -> None:
    if not have(generator.path()):
        pytest.skip("the utterance set is not here; run make data")
    manifest = datasets.load_manifest()
    entry = manifest.get(generator.KEY)
    if not entry:
        pytest.skip("no manifest entry yet")
    results = datasets.ROOT / "projects" / "p06_model_vs_lexicon" / "results_datasets.json"
    if not results.is_file():
        pytest.skip("project 06 has not been run at scale")
    data = json.loads(results.read_text(encoding="utf-8"))
    assert data["the_same_dataset"]["sha256"] == entry["sha256"], (
        "project 06 quotes a different version of the dataset than the manifest holds"
    )
