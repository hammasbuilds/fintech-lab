"""One dashboard for the three model projects, driven by each one's ``results.json``.

The three model projects differ in what they measure and agree completely in what a reader
needs: the setup, a comparison table with the deterministic baseline in it, and then the
individual cases with the model's actual answer and the grader's verdict beside it.

That last part is the one worth having. A mean score tells you how it went; the row where
the model said ``60000`` for ``saat hazaar`` tells you *why*, and a dashboard that hides
the replies behind an average is asking to be believed rather than read.

A project supplies a :class:`Spec` and gets the page. Nothing here knows about ISO 8583,
credit policy or Urdu.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .webkit import App, Request, Response


@dataclass
class Column:
    key: str
    label: str
    #: ``"text"`` left-aligns, ``"num"`` right-aligns and uses tabular figures,
    #: ``"verdict"`` renders a pass/fail pill, ``"mono"`` for code and replies.
    kind: str = "text"
    width: str = ""


@dataclass
class Table:
    title: str
    note: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    #: Rows matching this key/value are the baseline and are marked as such.
    baseline_key: str = ""
    baseline_value: Any = None


@dataclass
class Spec:
    title: str
    subtitle: str
    #: Shown as stat tiles: (label, value, unit).
    tiles: list[tuple[str, str, str]]
    #: Free-text paragraphs under the tiles, each a point the numbers need.
    notes: list[str]
    tables: list[Table]
    links: list[tuple[str, str]] = field(default_factory=list)
    #: Cases that did not run, so the page cannot imply a full sweep.
    incomplete: list[str] = field(default_factory=list)
    setup: dict[str, Any] = field(default_factory=dict)


def _results(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="/theme.css">
<style>
  .wide { max-width: 1320px; }
  td.reply, td.mono { font-family: var(--mono); font-size: 12px; }
  td.reply { max-width: 420px; white-space: pre-wrap; word-break: break-word; }
  td.text { max-width: 340px; }
  tr.base { background: var(--surface-sunken); }
  tr.base td:first-child { font-weight: 600; }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 12px; }
  details.case { margin: 0; }
  details.case summary { font-size: 12.5px; }
  .mini { font-size: 12px; color: var(--ink-2); }
</style>
</head>
<body>
<header class="top">
  <div>
    <h1>__TITLE__</h1>
    <p class="sub">__SUBTITLE__</p>
  </div>
  <div class="spacer"></div>
  <button class="btn ghost" id="theme" type="button" aria-label="Switch between light and dark">Dark</button>
</header>
<nav class="tabs" role="tablist">
  <button role="tab" id="t-summary" aria-selected="true" aria-controls="p-summary">Results</button>
  <button role="tab" id="t-cases" aria-selected="false" aria-controls="p-cases">Every case</button>
  <button role="tab" id="t-setup" aria-selected="false" aria-controls="p-setup">Setup</button>
</nav>
<div class="wrap wide">
  <section class="panel active" id="p-summary" role="tabpanel" aria-labelledby="t-summary"></section>
  <section class="panel" id="p-cases" role="tabpanel" aria-labelledby="t-cases"></section>
  <section class="panel" id="p-setup" role="tabpanel" aria-labelledby="t-setup"></section>
</div>
<footer class="foot">
  Standard library only. The model is a local one reached over HTTP; every reply is cached
  on disk, so these numbers can be rechecked without a GPU.
</footer>
<div class="tip" id="tip" role="status" aria-live="polite"></div>
<script>
const SPEC = __SPEC__;
const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, kids = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of [].concat(kids)) if (kid) node.append(kid);
  return node;
};
let dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
function paint() {
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  $("#theme").textContent = dark ? "Light" : "Dark";
}
$("#theme").addEventListener("click", () => { dark = !dark; paint(); });
paint();
for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener("click", () => {
    for (const other of document.querySelectorAll('[role="tab"]')) other.setAttribute("aria-selected", String(other === tab));
    for (const panel of document.querySelectorAll(".panel")) panel.classList.toggle("active", panel.id === tab.getAttribute("aria-controls"));
  });
}
function pill(ok, label) {
  return el("span", { class: "pill " + (ok ? "good" : "critical") }, [
    el("span", { class: "dot" }), el("span", { text: label }),
  ]);
}
function cell(column, row) {
  const value = row[column.key];
  if (column.kind === "verdict") {
    const ok = value === true || value === "true";
    return el("td", {}, pill(ok, ok ? "yes" : "no"));
  }
  const text = value === null || value === undefined ? "-"
    : (typeof value === "object" ? JSON.stringify(value) : String(value));
  const cls = column.kind === "num" ? "num" : (column.kind === "mono" ? "mono"
    : (column.kind === "reply" ? "reply" : "text"));
  return el("td", { class: cls, text });
}
function table(spec) {
  const card = el("div", { class: "card" }, [
    el("h2", { text: spec.title }),
    spec.note ? el("p", { class: "hint", text: spec.note }) : null,
  ]);
  const node = el("table");
  node.append(el("thead", {}, el("tr", {}, spec.columns.map((c) =>
    el("th", { class: c.kind === "num" ? "num" : null, text: c.label })))));
  const body = el("tbody");
  for (const row of spec.rows) {
    const isBase = spec.baseline_key && row[spec.baseline_key] === spec.baseline_value;
    body.append(el("tr", { class: isBase ? "base" : null }, spec.columns.map((c) => cell(c, row))));
  }
  node.append(body);
  card.append(el("div", { class: "scroll" }, node));
  return card;
}
(function render() {
  const summary = $("#p-summary");
  const tiles = el("div", { class: "tiles" });
  for (const [label, value, unit] of SPEC.tiles) {
    tiles.append(el("div", { class: "tile" }, [
      el("span", { class: "k", text: label }),
      el("span", { class: "v", text: String(value) }),
      el("span", { class: "u", text: unit }),
    ]));
  }
  const head = el("div", { class: "card" }, [el("h2", { text: "What this measures" })]);
  for (const note of SPEC.notes) head.append(el("p", { class: "hint", text: note }));
  head.append(tiles);
  if (SPEC.links.length) {
    const row = el("div", { class: "row", style: "margin-top:14px" });
    for (const [label, href] of SPEC.links) {
      row.append(el("a", { class: "btn ghost", style: "text-decoration:none", href, text: label,
        target: href.startsWith("http") ? "_blank" : null, rel: "noopener" }));
    }
    head.append(row);
  }
  summary.append(head);
  if (SPEC.incomplete.length) {
    summary.append(el("div", { class: "card" }, el("div", { class: "note warn",
      text: SPEC.incomplete.length + " case(s) have no cached reply and were not run, so the "
        + "table below is not a full sweep. Re-run the benchmark with --live. First few: "
        + SPEC.incomplete.slice(0, 3).join("; ") })));
  }
  for (const spec of SPEC.tables.filter((t) => !t.per_case)) summary.append(table(spec));

  const cases = $("#p-cases");
  const perCase = SPEC.tables.filter((t) => t.per_case);
  if (!perCase.length) cases.append(el("div", { class: "card" }, el("p", { class: "hint", text: "No per-case rows were recorded." })));
  for (const spec of perCase) cases.append(table(spec));

  const setup = $("#p-setup");
  const list = el("dl", { class: "kv" });
  for (const [key, value] of Object.entries(SPEC.setup)) {
    list.append(el("dt", { text: key.replace(/_/g, " ") }));
    list.append(el("dd", { text: typeof value === "object" ? JSON.stringify(value) : String(value) }));
  }
  setup.append(el("div", { class: "card" }, [el("h2", { text: "Setup" }), list]));
})();
</script>
</body>
</html>
"""


def render(spec: Spec) -> str:
    payload = {
        "tiles": spec.tiles,
        "notes": spec.notes,
        "links": spec.links,
        "incomplete": spec.incomplete,
        "setup": spec.setup,
        "tables": [
            {
                "title": table.title,
                "note": table.note,
                "columns": [
                    {"key": column.key, "label": column.label, "kind": column.kind}
                    for column in table.columns
                ],
                "rows": table.rows,
                "baseline_key": table.baseline_key,
                "baseline_value": table.baseline_value,
                "per_case": table.title.lower().startswith("every"),
            }
            for table in spec.tables
        ],
    }
    return (
        PAGE.replace("__TITLE__", spec.title)
        .replace("__SUBTITLE__", spec.subtitle)
        .replace("__SPEC__", json.dumps(payload, ensure_ascii=False))
    )


def attach(app: App, build: callable[[], Spec], results_path: Path) -> None:
    """Serve the dashboard at ``/`` and the raw results at ``/api/results``."""

    @app.route("GET", "/")
    def page(_request: Request) -> Response:
        return Response.text(render(build()), content_type="text/html")

    @app.route("GET", "/api/results")
    def results(_request: Request) -> Response:
        data = _results(results_path)
        if data is None:
            return Response.json(
                {"error": "no results file; run this project's benchmark"}, status=404
            )
        return Response.json(data)
