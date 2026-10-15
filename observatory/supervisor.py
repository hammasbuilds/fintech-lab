"""Start, stop and inspect the project servers.

The rule throughout: **report what is true, not what was attempted.** ``start`` spawns the
process and then waits for the port to answer, so it returns "running" only when a request
would actually land. If the port was already taken by something else, that is said plainly
rather than reported as a successful start - otherwise the panel claims credit for a server
it did not launch and cannot stop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .registry import (
    LOGS,
    PROJECTS,
    ROOT,
    Project,
    headline_for,
    load_registry,
    pid_alive,
    port_answers,
    save_registry,
)

START_TIMEOUT = 20.0
POLL = 0.15

STOPPED = "stopped"
RUNNING = "running"
FOREIGN = "port taken by something else"
STARTING = "starting"


@dataclass
class Status:
    project: Project
    state: str
    pid: int | None = None
    since: float | None = None
    adopted: bool = False
    detail: str = ""

    @property
    def running(self) -> bool:
        return self.state == RUNNING

    @property
    def stoppable(self) -> bool:
        """Whether this panel can stop it.

        Only a server this panel started, and whose pid it therefore knows. An adopted
        server or one belonging to something else is shown honestly as beyond reach,
        rather than given a Stop button that silently does nothing.
        """
        return self.running and (self.pid or 0) > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.project.key,
            "number": self.project.number,
            "name": self.project.name,
            "tagline": self.project.tagline,
            "port": self.project.port,
            "base_url": self.project.base_url,
            "state": self.state,
            "running": self.running,
            "pid": self.pid if (self.pid or 0) > 0 else None,
            "stoppable": self.stoppable,
            "uptime_seconds": round(time.time() - self.since, 1) if self.since else None,
            "adopted": self.adopted,
            "detail": self.detail,
            "links": self.project.links(),
            "headline": headline_for(self.project),
            "log": str(self.project.log_path),
        }


def _ours(project: Project) -> dict | None:
    entry = load_registry().get(project.key)
    if not entry:
        return None
    if entry.get("adopted"):
        # An adopted server was already up when the panel launched, so there is no pid to
        # check liveness against. It stays ours for as long as the port answers. The first
        # version of this ran the pid check anyway, which discarded every adopted entry
        # the moment it was written and made adoption do nothing at all.
        if not port_answers(project.port):
            forget(project)
            return None
        return entry
    if not pid_alive(int(entry.get("pid", 0))):
        forget(project)
        return None
    return entry


def status(project: Project) -> Status:
    """What is actually true about this project right now."""
    entry = _ours(project)
    answering = port_answers(project.port)
    if entry and answering:
        adopted = bool(entry.get("adopted"))
        known_pid = int(entry.get("pid", 0))
        return Status(
            project,
            RUNNING,
            # None rather than -1 when there is no pid to know, so the object and the
            # JSON the panel reads agree about what is unknown.
            pid=known_pid if known_pid > 0 else None,
            since=float(entry.get("started_at", time.time())),
            adopted=adopted,
            detail=(
                "it was already up when this panel launched, so its process is not this "
                "panel's to stop - stop it where it was started"
                if adopted
                else ""
            ),
        )
    if entry and not answering:
        # The process is alive but nothing is listening: it is still coming up, or it
        # came up and failed. Either way, not running.
        return Status(
            project,
            STARTING,
            pid=int(entry["pid"]),
            since=float(entry.get("started_at", time.time())),
            detail="the process is alive but the port is not answering yet",
        )
    if answering:
        return Status(
            project,
            FOREIGN,
            detail=(
                f"something is already listening on port {project.port} that this panel "
                f"did not start, so it cannot be stopped from here"
            ),
        )
    return Status(project, STOPPED)


def status_all() -> list[Status]:
    return [status(project) for project in PROJECTS]


def start(project: Project) -> Status:
    current = status(project)
    if current.state in {RUNNING, FOREIGN}:
        return current

    LOGS.mkdir(parents=True, exist_ok=True)
    handle = project.log_path.open("a", encoding="utf-8", newline="\n")
    handle.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    handle.flush()

    creation = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", project.module, "--port", str(project.port)],
        cwd=str(ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creation,
    )

    entries = load_registry()
    entries[project.key] = {
        "pid": process.pid,
        "port": project.port,
        "started_at": time.time(),
        "adopted": False,
    }
    save_registry(entries)

    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            forget(project)
            tail = _log_tail(project, 12)
            return Status(
                project,
                STOPPED,
                detail=(
                    f"the process exited immediately with code {process.returncode}. "
                    f"Last lines of its log: {tail}"
                ),
            )
        if port_answers(project.port):
            return status(project)
        time.sleep(POLL)

    return Status(
        project,
        STARTING,
        pid=process.pid,
        since=time.time(),
        detail=(
            f"the process started but the port did not answer within {START_TIMEOUT:g} seconds"
        ),
    )


def stop(project: Project) -> Status:
    entry = _ours(project)
    if entry is None:
        current = status(project)
        if current.state == FOREIGN:
            return current
        return Status(project, STOPPED, detail="it was not running")

    pid = int(entry.get("pid", 0))
    if pid <= 0:
        # An adopted server: running, but not this panel's process. Saying so beats
        # reporting a stop that did not happen.
        return Status(
            project,
            RUNNING,
            since=float(entry.get("started_at", time.time())),
            adopted=True,
            detail=(
                "this panel did not start it and does not know its process, so it cannot "
                "stop it. Stop it in the terminal it was started from."
            ),
        )
    if os.name == "nt":
        # /T so the child tree goes with it, /F because a console app that is not in
        # the foreground will not take a polite request.
        subprocess.run(  # noqa: S603 - fixed argv
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=15,
        )
    else:
        import contextlib
        import signal

        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)

    deadline = time.time() + 10
    while time.time() < deadline and port_answers(project.port):
        time.sleep(POLL)
    forget(project)
    return status(project)


def forget(project: Project) -> None:
    entries = load_registry()
    if entries.pop(project.key, None) is not None:
        save_registry(entries)


def adopt() -> list[str]:
    """Record any project already answering on its port, so the panel can see it.

    Called on launch. A server left over from a previous session is a running server, and
    a panel that showed it as stopped would invite starting a second one on a taken port.
    """
    entries = load_registry()
    adopted: list[str] = []
    for project in PROJECTS:
        if project.key in entries:
            continue
        if port_answers(project.port):
            entries[project.key] = {
                "pid": -1,
                "port": project.port,
                "started_at": time.time(),
                "adopted": True,
            }
            adopted.append(project.key)
    if adopted:
        save_registry(entries)
    return adopted


def metrics(project: Project, timeout: float = 2.0) -> dict[str, Any]:
    """Ask the project for its own request counters."""
    url = project.base_url + "/api/metrics"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


def _log_tail(project: Project, lines: int) -> str:
    if not project.log_path.is_file():
        return "(no log)"
    try:
        content = project.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(log unreadable)"
    return " / ".join(content.splitlines()[-lines:]) or "(log empty)"


def log_tail(project: Project, lines: int = 80) -> list[str]:
    if not project.log_path.is_file():
        return []
    try:
        content = project.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return content.splitlines()[-lines:]
