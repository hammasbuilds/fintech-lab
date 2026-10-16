.PHONY: install test lint demo bench bench01 bench02 bench03 bench-models bench04 bench05 bench06 serve01 serve02 serve03 serve04 serve05 serve06 observatory observatory-all models pages fetch datasets data bench-datasets big01 big02 big03 big04 big05 big06 all

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check shared projects observatory scripts tests demo.py
	uv run ruff format --check shared projects observatory scripts tests demo.py

demo:
	uv run python demo.py

# ---------------------------------------------------------------------- datasets
# Every project is measured on between 50,000 and a million rows. Two of those datasets
# are downloaded and five are generated, and none of them is in git: the repo ships the
# generators, the manifest (row counts and sha256) and a 200-row sample of each.
#
#   make fetch      downloads the four public files (needs a Kaggle token)
#   make datasets   generates the corpora and writes data/MANIFEST.json
#   make data       both, in that order
fetch:
	uv run python scripts/fetch_raw.py

datasets:
	uv run python scripts/build_datasets.py

data: fetch datasets

# The at-scale runs. Each writes results_datasets.json next to the project, which is
# committed and - unlike results.json - not re-derived by CI, because CI has no data.
bench-datasets: big01 big02 big03 big04 big05 big06

big01:
	uv run python -m projects.p01_switchboard.bigbench

big02:
	uv run python -m projects.p02_policy_bench.bigbench

big03:
	uv run python -m projects.p03_guardian.bigbench

big04:
	uv run python -m projects.p04_spec_to_wire.bigbench

big05:
	uv run python -m projects.p05_policy_from_prose.bigbench

big06:
	uv run python -m projects.p06_model_vs_lexicon.bigbench

# The first three need nothing but the standard library.
bench: bench01 bench02 bench03

bench01:
	uv run python -m projects.p01_switchboard.benchmark

bench02:
	uv run python -m projects.p02_policy_bench.benchmark

bench03:
	uv run python -m projects.p03_guardian.benchmark

# One panel in front of all three: start them, stop them, watch their own counters, and
# open each project's dashboard, its input/output proof, its source and the published
# page from one place.
# The model arms. These need a local model for --live; without one they run from the
# committed reply cache and report any case they could not ask about as not run.
bench-models:
	uv run python scripts/run_model_benchmarks.py --live

bench04:
	uv run python -m projects.p04_spec_to_wire.benchmark --live

bench05:
	uv run python -m projects.p05_policy_from_prose.benchmark --live

bench06:
	uv run python -m projects.p06_model_vs_lexicon.benchmark --live

# What the model arms would use, and whether it is reachable.
models:
	uv run python -c "from shared import llm; print('host:', llm.DEFAULT_HOST); print('installed:', llm.tags()); print('loaded now:', [m.get('name') for m in llm.resident()]); print('cached replies:', llm.cache_size())"

observatory:
	uv run python -m observatory

observatory-all:
	uv run python -m observatory --start-all

# The published page, the READMEs' numbers and their Input / Output blocks are all
# generated from the projects' own results files, never typed.
pages:
	uv run python scripts/build_pages.py
	uv run python scripts/build_model_readmes.py
	uv run python scripts/build_dataset_readmes.py
	uv run python scripts/build_readme_io.py

# Each UI is a single page on the standard library. Nothing starts on import; these are
# the only commands that open a port.
serve01:
	uv run python -m projects.p01_switchboard.app --port 8101

serve02:
	uv run python -m projects.p02_policy_bench.app --port 8102

serve03:
	uv run python -m projects.p03_guardian.app --port 8103

serve04:
	uv run python -m projects.p04_spec_to_wire.app --port 8104

serve05:
	uv run python -m projects.p05_policy_from_prose.app --port 8105

serve06:
	uv run python -m projects.p06_model_vs_lexicon.app --port 8106

all: install lint test bench pages demo
