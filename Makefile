.PHONY: install test lint demo bench bench01 bench02 bench03 serve01 serve02 serve03 observatory observatory-all pages all

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check shared projects tests demo.py
	uv run ruff format --check shared projects tests demo.py

demo:
	uv run python demo.py

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

all: install lint test bench pages demo
