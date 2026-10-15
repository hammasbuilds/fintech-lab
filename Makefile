.PHONY: install test lint demo bench bench01 bench02 bench03 bench-models bench04 bench05 bench06 serve01 serve02 serve03 serve04 serve05 serve06 observatory observatory-all models pages all

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check shared projects tests demo.py
	uv run ruff format --check shared projects tests demo.py

demo:
	uv run python demo.py

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

# The published page is generated from the projects' own results files, never typed.
pages:
	uv run python scripts/build_pages.py

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
