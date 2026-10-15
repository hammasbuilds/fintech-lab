"""The Observatory - one control panel for all three fintech projects.

    uv run python -m observatory

Start and stop each project from one page, see whether it is actually up, read its own
request counters, and open its dashboard, its input/output proof, its source and the
published page without remembering a port or keeping a desktop shortcut per project.

Two design points worth stating, both taken from how this goes wrong:

* **Projects are separate processes, tracked on disk.** Closing the panel does not stop
  them, and the next launch adopts whatever is still running rather than pretending the
  machine is idle. A panel that loses track of its own children is worse than no panel.
* **Status is measured, not assumed.** "Started" means a request to the port came back,
  not that a subprocess was spawned. The two differ exactly when it matters: a port
  already taken, an import error, a crash two seconds in.
"""
