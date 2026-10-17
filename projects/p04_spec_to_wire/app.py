"""The project 04 dashboard: the scores, and every reply the grader saw."""

from __future__ import annotations

import argparse
import json
import pathlib
from pathlib import Path

from shared.datapage import Block, Figure, dig, figures_from  # noqa: F401
from shared.datapage import Page as DataPage
from shared.datapage import attach as attach_datasets
from shared.modelpage import Column, Spec, Table, attach
from shared.theme import attach_theme
from shared.webkit import App, attach_metrics, run

from . import bank
from .benchmark import RESULTS
from .benchmark import run as measure

app = App("spec-to-wire", static_dir=None)
attach_theme(app)
attach_metrics(app)

SOURCE = "https://github.com/hammasbuilds/fintech-lab/tree/main/projects/p04_spec_to_wire"


def _data() -> dict:
    if RESULTS.is_file():
        import json

        try:
            return json.loads(RESULTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return measure(live=False)


def _at_scale() -> dict:
    """The whole-bank run, if it has been done on this machine."""
    path = pathlib.Path(__file__).with_name("results_datasets.json")
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _bank_rows() -> list[dict[str, object]]:
    """Project 01's codec on all 60,000 tasks, beside the model's 29.

    The two columns are measured at sizes four orders of magnitude apart, and the page
    says so in both of them rather than letting a reader assume one number covers both.
    """
    data = _at_scale()
    product = (data.get("product_on_the_whole_bank") or {}).get("by_family") or {}
    sample = data.get("model_on_a_sample") or {}
    rows: list[dict[str, object]] = []
    for family in ("decode", "fields", "wire", "negative"):
        entry = product.get(family) or {}
        rows.append(
            {
                "family": family,
                "who": "project 01's codec",
                "cases": entry.get("cases"),
                "score": entry.get("accuracy"),
                "note": (
                    "found a fault on every one; named the injected one "
                    f"{entry.get('named_the_injected_fault_rate')}"
                    if family == "negative"
                    else "no sampling: every task in the bank"
                ),
            }
        )
    if sample.get("cases_the_model_answered"):
        rows.append(
            {
                "family": "all four",
                "who": "the model",
                "cases": sample.get("cases_the_model_answered"),
                "score": None,
                "note": (
                    f"a sample: {sample.get('share_of_the_bank')} of "
                    f"{sample.get('tasks_in_the_bank')} tasks, "
                    f"{sample.get('aborted_by_the_server')} replies aborted by the server"
                ),
            }
        )
    return rows


def spec() -> Spec:
    data = _data()
    setup = data.get("setup", {})
    usage = data.get("usage", {})
    families = setup.get("families", [])

    summary_rows = []
    baseline = data.get("baseline", {}).get("summary", {})
    row = {"arm": "project 01's generator (code)"}
    for family in families:
        cell = baseline.get(family, {})
        row[family] = f"{cell.get('mean_score', 0):.2f}"
    summary_rows.append(row)
    for name, block in data.get("by_model_and_arm", {}).items():
        row = {"arm": name}
        for family in families:
            cell = block.get(family, {})
            row[family] = f"{cell.get('mean_score', 0):.2f}"
        summary_rows.append(row)

    usable_rows = []
    for name, block in data.get("by_model_and_arm", {}).items():
        total = sum(cell["cases"] for cell in block.values())
        usable_rows.append(
            {
                "arm": name,
                "parsed": f"{sum(c['parsed'] for c in block.values())}/{total}",
                "packs": f"{sum(c['packs'] for c in block.values())}/{total}",
                "clean": f"{sum(c['invariants_hold'] for c in block.values())}/{total}",
            }
        )

    case_rows = []
    for row_data in data.get("rows", []):
        case_rows.append(
            {
                "case": row_data.get("case"),
                "arm": f"{row_data.get('model', '')} / {row_data.get('arm', '')}",
                "score": f"{row_data.get('score', 0):.2f}",
                "packs": row_data.get("packs"),
                "detail": row_data.get("detail", ""),
                "seconds": row_data.get("seconds", ""),
            }
        )

    return Spec(
        title="04 Spec to wire",
        subtitle="Can a local model produce payment traffic a switch would accept?",
        tiles=[
            ("Cases", str(setup.get("cases", "-")), "across four families"),
            (
                "Replies generated",
                str(usage.get("generated", 0)),
                f"{usage.get('served_from_cache', 0)} from cache",
            ),
            ("Wall clock", str(usage.get("wall_clock_seconds", 0)), "seconds"),
            ("Hit the token cap", str(usage.get("hit_the_token_cap", 0)), "truncated answers"),
            (
                "Cache entries",
                str(data.get("cache", {}).get("entries", 0)),
                "committed, so this is recheckable",
            ),
        ],
        notes=[
            "Project 01 generates ISO 8583 from a scenario with code and checks it with a "
            "parser and 22 invariants. Here a model does the generating and project 01 is "
            "the oracle, so every grade is computed rather than judged: a message either "
            "packs or it does not, and the invariants either hold or name what broke.",
            "The generator's row is 1.00 by construction. It is shown because a model score "
            "with no baseline beside it invites the reader to supply their own idea of what "
            "good looks like.",
            "The two arms are identical apart from the prompt: 'bare' gives the task alone, "
            "'spec' pastes in the data element dictionary and the invariants. The question "
            "an engineer has is whether handing it the specification closes the gap.",
        ],
        tables=[
            Table(
                title="The same four families on all 60,000 tasks",
                note=(
                    "The codec answers every task in the generated bank; the model answers "
                    "29 of them. A baseline measured on 29 cases cannot tell a parser that "
                    "is right from a parser that is right about those 29."
                ),
                columns=[
                    Column("family", "Family", "text"),
                    Column("who", "Answered by", "text"),
                    Column("cases", "Cases", "num"),
                    Column("score", "Accuracy", "num"),
                    Column("note", "What that is over", "reply"),
                ],
                rows=_bank_rows(),
                baseline_key="who",
                baseline_value="project 01's codec",
            ),
            Table(
                title="Mean score by task family",
                note="0 to 1. decode: did it read a message. fields: did it name the right "
                "elements. wire: did the parser accept what it wrote. negative: did it break "
                "the one invariant it was asked to break, and only that one.",
                columns=[Column("arm", "Arm", "text")]
                + [Column(family, family, "num") for family in families],
                rows=summary_rows,
                baseline_key="arm",
                baseline_value="project 01's generator (code)",
            ),
            Table(
                title="How many answers were usable at all",
                note="Parsed as the requested shape, then packed by the codec, then clean "
                "against the invariants. These stay separate: an unparseable message is not "
                "90% of a message.",
                columns=[
                    Column("arm", "Arm", "text"),
                    Column("parsed", "Parsed", "num"),
                    Column("packs", "Packs", "num"),
                    Column("clean", "Invariants hold", "num"),
                ],
                rows=usable_rows,
            ),
            Table(
                title="Every case, with the grader's verdict",
                note="The detail column is the grader's own words about what differed.",
                columns=[
                    Column("case", "Case", "mono"),
                    Column("arm", "Arm", "text"),
                    Column("score", "Score", "num"),
                    Column("packs", "Packs", "verdict"),
                    Column("seconds", "Seconds", "num"),
                    Column("detail", "What the grader found", "reply"),
                ],
                rows=case_rows,
            ),
        ],
        links=[
            ("Input and output", "/io"),
            ("Datasets", "/datasets"),
            ("Project 01, the oracle", "http://127.0.0.1:8101/"),
            ("Source", SOURCE),
        ],
        incomplete=data.get("incomplete", []),
        setup=setup | {"usage": usage, "cache": data.get("cache", {})},
    )


attach(app, spec, RESULTS)


def io_page():
    from shared.iopage import Page, Row, Section

    data = _data()
    setup = data.get("setup", {})
    sections = []
    for row in data.get("rows", [])[:6]:
        sections.append(
            Section(
                title=f"{row['case']}  ({row['model']} / {row['arm']})",
                given=row.get("text", row["case"]),
                note=row.get("detail", ""),
                rows=[
                    Row("score", f"{row.get('score', 0):.2f}"),
                    Row("parsed", str(row.get("parsed"))),
                    Row("packs", str(row.get("packs"))),
                    Row("invariants hold", str(row.get("invariants_hold"))),
                ],
            )
        )
    return Page(
        title="04 Spec to wire",
        subtitle="A local model against project 01's own oracle",
        sections=sections
        or [
            Section(
                title="Nothing measured yet",
                given="run: uv run python -m projects.p04_spec_to_wire.benchmark --live",
                note="The dashboard reads results.json, which the benchmark writes.",
            )
        ],
        links=[("Dashboard", "/"), ("Source", SOURCE)],
        caveat=f"Models used: {', '.join(setup.get('models', []))}. "
        "Every reply is cached on disk and the cache is committed, so these numbers can be "
        "rechecked without a GPU.",
    )


from shared.iopage import attach_io  # noqa: E402

attach_io(app, io_page)


# ---------------------------------------------------------------------------- datasets
# What this project is measured on at full size, and what came out. The figures are read
# out of results_datasets.json and data/MANIFEST.json rather than typed, so this page
# cannot drift from the run that produced it.
DATASETS_RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"


def _at_scale() -> dict:
    if DATASETS_RESULTS.is_file():
        try:
            return json.loads(DATASETS_RESULTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def datasets_page() -> DataPage:
    data = _at_scale()
    blocks = [
        Block(
            title="Project 01's codec, on the whole bank",
            figures=figures_from(
                data,
                [
                    ("tasks scored", "product_on_the_whole_bank.tasks_scored", ""),
                    (
                        "decode",
                        "product_on_the_whole_bank.by_family.decode.accuracy",
                        "15,000 tasks",
                    ),
                    (
                        "fields",
                        "product_on_the_whole_bank.by_family.fields.accuracy",
                        "15,000 tasks",
                    ),
                    ("wire", "product_on_the_whole_bank.by_family.wire.accuracy", "15,000 tasks"),
                    (
                        "negative: found a fault",
                        "product_on_the_whole_bank.by_family.negative.found_a_fault_rate",
                        "15,000 tasks",
                    ),
                    (
                        "negative: named the injected fault",
                        "product_on_the_whole_bank.by_family.negative.named_the_injected_fault_rate",
                        "15,000 tasks",
                    ),
                ],
            ),
            reading=str(dig(data, "product_on_the_whole_bank.how_to_read_the_negative_family", "")),
        ),
        Block(
            title="The model, on a sample",
            figures=figures_from(
                data,
                [
                    ("cases answered", "model_on_a_sample.cases_the_model_answered", ""),
                    ("tasks in the bank", "model_on_a_sample.tasks_in_the_bank", ""),
                    ("share of the bank", "model_on_a_sample.share_of_the_bank", "the bank"),
                    (
                        "replies aborted by the server",
                        "model_on_a_sample.aborted_by_the_server",
                        "58 replies",
                    ),
                ],
            ),
            reading=str(dig(data, "model_on_a_sample.why_it_is_a_sample", "")),
        ),
    ]
    if not data:
        blocks = [
            Block(
                title="Not run on this machine",
                subtitle=(
                    "The at-scale figures come from results_datasets.json, which is "
                    "committed. If this page is empty the file is missing: run make data "
                    "and then make bench-datasets."
                ),
            )
        ]
    return DataPage(
        title="04 Spec to wire",
        subtitle="60,000 tasks for the codec, 29 of them for the model",
        dataset_keys=[bank.KEY],
        blocks=blocks,
        links=[("Dashboard", "/"), ("Input and output", "/io"), ("Source", SOURCE)],
    )


attach_datasets(app, datasets_page)


def main(port: int = 8104) -> None:
    run(app, port=port)


# The __main__ block stays last: anything registered after it never runs when this module
# is executed as a program, because main() blocks in the server loop.
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve the project 04 dashboard.")
    parser.add_argument("--port", type=int, default=8104)
    main(parser.parse_args().port)


del Path
