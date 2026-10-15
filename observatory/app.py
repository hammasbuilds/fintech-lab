"""The Observatory server.

    uv run python -m observatory            # then open http://127.0.0.1:8100

One page, one port, and every project reachable from it: start it, stop it, open its
dashboard or its input/output proof, read its own request counters, and follow through to
the source or the published page.

Starting a server is a slow, blocking thing - a subprocess, then a wait for the port to
answer - so those are POST endpoints that return the *measured* state afterwards, never a
cheerful acknowledgement.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from shared.theme import attach_theme
from shared.webkit import App, BadRequest, Request, Response, attach_metrics, run

from . import supervisor
from .registry import BY_KEY, GITHUB, PAGES, PROJECTS

STATIC = Path(__file__).with_name("static")

app = App("observatory", static_dir=STATIC)
attach_theme(app)
attach_metrics(app)


def _project(payload: dict[str, Any]):
    key = str(payload.get("project", ""))
    project = BY_KEY.get(key)
    if project is None:
        raise BadRequest(f"unknown project {key!r}; known: {', '.join(BY_KEY)}")
    return project


@app.route("GET", "/api/meta")
def meta(_request: Request) -> Response:
    return Response.json(
        {
            "repo": GITHUB,
            "pages": PAGES,
            "projects": [
                {
                    "key": project.key,
                    "number": project.number,
                    "name": project.name,
                    "tagline": project.tagline,
                    "port": project.port,
                    "folder": project.folder,
                    "links": project.links(),
                }
                for project in PROJECTS
            ],
        }
    )


@app.route("GET", "/api/status")
def status(_request: Request) -> Response:
    """Every project's measured state. Polled by the page."""
    return Response.json({"projects": [item.as_dict() for item in supervisor.status_all()]})


@app.route("POST", "/api/start")
def start(request: Request) -> Response:
    project = _project(request.json())
    return Response.json({"status": supervisor.start(project).as_dict()})


@app.route("POST", "/api/stop")
def stop(request: Request) -> Response:
    project = _project(request.json())
    return Response.json({"status": supervisor.stop(project).as_dict()})


@app.route("POST", "/api/start-all")
def start_all(_request: Request) -> Response:
    return Response.json(
        {"projects": [supervisor.start(project).as_dict() for project in PROJECTS]}
    )


@app.route("POST", "/api/stop-all")
def stop_all(_request: Request) -> Response:
    return Response.json({"projects": [supervisor.stop(project).as_dict() for project in PROJECTS]})


@app.route("GET", "/api/telemetry")
def telemetry(_request: Request) -> Response:
    """Each running project's own counters, plus this panel's."""
    out = {}
    for project in PROJECTS:
        state = supervisor.status(project)
        out[project.key] = (
            supervisor.metrics(project)
            if state.running
            else {"unavailable": f"not running ({state.state})"}
        )
    return Response.json({"projects": out, "observatory": app.metrics.snapshot()})


@app.route("GET", "/api/log")
def log(request: Request) -> Response:
    key = request.query.get("project", "")
    project = BY_KEY.get(key)
    if project is None:
        raise BadRequest(f"unknown project {key!r}")
    try:
        lines = min(400, max(10, int(request.query.get("lines", "80"))))
    except ValueError:
        raise BadRequest("lines must be a number") from None
    return Response.json(
        {
            "project": project.key,
            "path": str(project.log_path),
            "lines": supervisor.log_tail(project, lines),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the fintech-lab Observatory.")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument(
        "--start-all",
        action="store_true",
        help="start every project server before serving the panel",
    )
    args = parser.parse_args()

    adopted = supervisor.adopt()
    if adopted:
        print(f"adopted already-running: {', '.join(adopted)}")
    if args.start_all:
        for project in PROJECTS:
            state = supervisor.start(project)
            print(f"  {project.name:14s} {state.state}  {project.base_url}")

    print("\nthe three projects:")
    for project in PROJECTS:
        print(f"  {project.number} {project.name:14s} {project.base_url}")
    print()
    run(app, port=args.port)


if __name__ == "__main__":
    main()
