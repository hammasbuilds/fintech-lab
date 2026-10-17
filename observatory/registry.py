"""What the Observatory knows about each project, and where it keeps the live ones.

The project list is data, in one place. Adding a fourth project means one entry here and
nothing else, which is the only reason a control panel is less work than three shortcuts.

The *process* registry is a small JSON file. It exists so that closing the panel does not
orphan a running server: the next launch reads the file, checks each recorded pid is still
alive and still answering on its port, and adopts the ones that are.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "observatory" / "state"
REGISTRY = STATE / "running.json"
LOGS = STATE / "logs"

GITHUB = "https://github.com/hammasbuilds/fintech-lab"
PAGES = "https://hammasbuilds.github.io/fintech-lab/"


@dataclass(frozen=True)
class Project:
    key: str
    number: str
    name: str
    tagline: str
    module: str
    port: int
    folder: str
    #: True for the model arms (04-06). The panel says so, because "it needs a local
    #: model running" is the difference between a project that will start and one that
    #: starts and then has nothing to answer with.
    needs_model: bool = False
    #: The project whose grader scores this one, for the link between a pair.
    pairs_with: str = ""
    #: The headline figures to pull out of the project's ``results.json``, as
    #: (label, dotted path, formatter name).
    headline: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)
    #: Manifest keys for the datasets this project is measured on at full size. The
    #: panel reads the row counts out of data/MANIFEST.json rather than holding its own
    #: copy of them, so a rebuilt dataset cannot leave the panel quoting the old size.
    dataset_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def results_path(self) -> Path:
        return ROOT / "projects" / self.folder / "results.json"

    @property
    def datasets_path(self) -> Path:
        return ROOT / "projects" / self.folder / "results_datasets.json"

    def datasets(self) -> list[dict[str, object]]:
        """Row counts and fingerprints for this project's datasets, from the manifest."""
        from shared.datasets import load_manifest

        manifest = load_manifest()
        out: list[dict[str, object]] = []
        for key in self.dataset_keys:
            entry = manifest.get(key)
            out.append(
                {
                    "key": key,
                    "rows": entry.get("rows") if entry else None,
                    "bytes": entry.get("bytes") if entry else None,
                    "role": entry.get("role") if entry else None,
                    "source": entry.get("source") if entry else None,
                    "built": entry is not None,
                }
            )
        return out

    @property
    def readme_url(self) -> str:
        return f"{GITHUB}/blob/main/projects/{self.folder}/README.md"

    @property
    def gaps_url(self) -> str:
        """The gap analysis, which is one document for all six rather than one each."""
        return f"{GITHUB}/blob/main/GAPS.md"

    @property
    def source_url(self) -> str:
        return f"{GITHUB}/tree/main/projects/{self.folder}"

    @property
    def log_path(self) -> Path:
        return LOGS / f"{self.key}.log"

    def links(self) -> list[dict[str, str]]:
        """Everything you might want to open for this project, in one list."""
        paired = []
        if self.pairs_with:
            paired = [
                {
                    "label": "Its baseline",
                    "href": f"http://127.0.0.1:{BY_KEY[self.pairs_with].port}/",
                    "kind": "local",
                }
            ]
        return paired + [
            {"label": "Dashboard", "href": self.base_url + "/", "kind": "local"},
            {"label": "Input and output", "href": self.base_url + "/io", "kind": "local"},
            {"label": "Datasets", "href": self.base_url + "/datasets", "kind": "local"},
            {"label": "Source", "href": self.source_url, "kind": "external"},
            {"label": "README", "href": self.readme_url, "kind": "external"},
            {"label": "Gap analysis", "href": self.gaps_url, "kind": "external"},
            {"label": "Published page", "href": PAGES, "kind": "external"},
        ]


PROJECTS: tuple[Project, ...] = (
    Project(
        key="switchboard",
        number="01",
        name="Switchboard",
        tagline=(
            "ISO 8583 workbench: decode either framing, generate lifecycles, check 22 invariants"
        ),
        module="projects.p01_switchboard.app",
        port=8101,
        folder="p01_switchboard",
        dataset_keys=("generated/p01_messages",),
        headline=(
            ("Invariants", "invariants.rules", "int"),
            (
                "Faults killed by their rule",
                "mutation.killed_by_intended_rule",
                "over:mutation.mutants",
            ),
            (
                "False positives on valid traffic",
                "invariants.valid_flows_with_findings",
                "int",
            ),
            ("EMV tags parsed", "chip.tags_parsed", "int"),
        ),
    ),
    Project(
        key="policy-bench",
        number="02",
        name="Policy Bench",
        tagline=(
            "Lending-policy sandbox: compile, back-test, sweep the cutoff, load your own cohort"
        ),
        module="projects.p02_policy_bench.app",
        port=8102,
        folder="p02_policy_bench",
        dataset_keys=(
            "real/loan_book",
            "real/card_applications",
            "real/card_credit_records",
        ),
        headline=(
            ("Borrowers", "cohort.size", "int"),
            ("Scorecard Gini", "discrimination.gini", "float"),
            (
                "Tightening precision",
                "comparison_v1_to_v2.precision_of_the_tightening",
                "percent",
            ),
            ("Better cutoff worth", "cutoff_sweep.net_gain_available", "money"),
        ),
    ),
    Project(
        key="guardian",
        number="03",
        name="Guardian",
        tagline=(
            "Vernacular transaction assistant: confirmation spine, coercion check, behavioural risk"
        ),
        module="projects.p03_guardian.app",
        port=8103,
        folder="p03_guardian",
        dataset_keys=("generated/p03_utterances",),
        headline=(
            ("Test cases", "testset.all", "int"),
            ("Actions correct", "actions.overall_exact", "over:actions.cases"),
            ("Replies wrongly read as consent", "replies.read_as_consent_wrongly", "int"),
            (
                "Scams the words miss, recovered",
                "paired.scams_escalated_by_behaviour",
                "over:paired.scams",
            ),
        ),
    ),
    # 04-06 are the model arms of 01-03. Each one asks a local model to do the job its
    # pair does with code, and is scored by that pair's own grader - which is the only
    # reason the numbers mean anything, since the deterministic baseline sits in every
    # comparison table.
    Project(
        key="spec-to-wire",
        number="04",
        name="Spec to wire",
        tagline=(
            "A local model generating ISO 8583, graded by project 01's parser and its 22 invariants"
        ),
        module="projects.p04_spec_to_wire.app",
        port=8104,
        folder="p04_spec_to_wire",
        dataset_keys=("generated/p04_tasks",),
        needs_model=True,
        pairs_with="switchboard",
        headline=(
            ("Cases", "setup.cases", "int"),
            ("Replies generated", "usage.generated", "int"),
            ("Served from cache", "usage.served_from_cache", "int"),
            ("Hit the token cap", "usage.hit_the_token_cap", "int"),
        ),
    ),
    Project(
        key="policy-from-prose",
        number="05",
        name="Policy from prose",
        tagline=(
            "A local model turning written policy into project 02's language, graded on "
            "whether it decides the same 5,000 borrowers"
        ),
        module="projects.p05_policy_from_prose.app",
        port=8105,
        folder="p05_policy_from_prose",
        dataset_keys=("generated/p05_policies",),
        needs_model=True,
        pairs_with="policy-bench",
        headline=(
            ("Policies in prose", "setup.policy_cases", "int"),
            ("Notice cases", "setup.notice_cases", "int"),
            ("Replies generated", "usage.generated", "int"),
            ("Borrowers each decides", "setup.borrowers", "int"),
        ),
    ),
    Project(
        key="model-vs-lexicon",
        number="06",
        name="Model against lexicon",
        tagline=(
            "Project 03's test set answered by a model and by the rules, same grader, "
            "including the blind spot the rules publish"
        ),
        module="projects.p06_model_vs_lexicon.app",
        port=8106,
        folder="p06_model_vs_lexicon",
        dataset_keys=("generated/p03_utterances",),
        needs_model=True,
        pairs_with="guardian",
        headline=(
            ("Cases", "setup.cases", "int"),
            ("Rules: amounts", "rules.amount.correct", "over:rules.amount.cases"),
            (
                "Rules on the blind spot",
                "rules.coercion.caught_of_those",
                "over:rules.coercion.cases_the_lexicon_misses",
            ),
            ("Replies generated", "usage.generated", "int"),
        ),
    ),
)

BY_KEY: dict[str, Project] = {project.key: project for project in PROJECTS}


def dig(data: dict, path: str) -> object:
    """Read a dotted path out of a results file, returning ``None`` rather than raising."""
    current: object = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def format_value(value: object, how: str, data: dict) -> str:
    if value is None:
        return "-"
    if how.startswith("over:"):
        total = dig(data, how.split(":", 1)[1])
        return f"{value}/{total}" if total is not None else str(value)
    if how == "percent":
        return f"{float(value):.1%}"
    if how == "money":
        return f"{float(value):+,.0f} PKR"
    if how == "float":
        return f"{float(value):g}"
    if how == "int":
        return f"{int(value):,}"
    return str(value)


def headline_for(project: Project) -> list[dict[str, str]]:
    """The project's own published numbers, read from the file it writes."""
    if not project.results_path.is_file():
        return []
    try:
        data = json.loads(project.results_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out = []
    for label, path, how in project.headline:
        out.append({"label": label, "value": format_value(dig(data, path), how, data)})
    return out


# ---------------------------------------------------------------- the live ones


def load_registry() -> dict[str, dict]:
    if not REGISTRY.is_file():
        return {}
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_registry(entries: dict[str, dict]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8", newline="\n")


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` still exists. Not whether it is the process we started."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import subprocess

        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_answers(port: int, timeout: float = 0.4) -> bool:
    """Whether something accepts a connection on ``port``.

    This is the check that makes "running" mean something. A spawned subprocess that
    failed to bind is not a running server, and only the port can tell you that.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


# A note on what is deliberately *not* here: there was a ``pid_on_port`` helper that read
# netstat to find which process owns a listening socket, so that an adopted server could
# be stopped. It was removed. On this machine the pid it reported did not match the
# process that had actually bound the port, and a supervisor that kills a pid it inferred
# wrongly is far worse than one that admits it cannot stop something. Adopted servers are
# therefore shown as running and marked unstoppable, which is true and safe.
