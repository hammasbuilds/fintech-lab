"""Render a project's Input / Output proof as a page, from the real engine.

Every project already proves itself through `demo.py`, which prints real input and real
output. That is the right content and the wrong place for anyone who has not cloned the
repo, so this renders the same thing as a page each project serves at ``/io``.

The important constraint: **the sections are produced by running the engine**, not stored
as text. A page of pasted output is a screenshot with extra steps - it goes stale the
moment the code moves and nothing notices. Each project builds its sections on request,
so the page cannot disagree with the code that serves it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from .webkit import App, Request, Response


@dataclass
class Row:
    """One measurement: a label, a value, and an optional unit or aside."""

    label: str
    value: str
    unit: str = ""


@dataclass
class Section:
    """One input and what the project did with it."""

    title: str
    given: str
    note: str = ""
    output: str = ""
    rows: list[Row] = field(default_factory=list)
    reading: str = ""


@dataclass
class Page:
    title: str
    subtitle: str
    sections: list[Section]
    links: list[tuple[str, str]] = field(default_factory=list)
    caveat: str = ""


def render(page: Page) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(page.title)} &mdash; input and output</title>",
        '<link rel="stylesheet" href="/theme.css">',
        "<style>",
        "  .io { max-width: 900px; }",
        "  .io h2 { font-size: 14px; margin: 0 0 2px; }",
        "  .given { font-family: var(--mono); font-size: 13px; background: var(--surface-sunken);",
        "    border-left: 3px solid var(--series-1); border-radius: 0 8px 8px 0;",
        "    padding: 9px 12px; margin: 0 0 12px; white-space: pre-wrap; }",
        "  .given b { font-family: var(--sans); font-weight: 600; color: var(--ink-2);",
        "    display: block; font-size: 11.5px; letter-spacing: 0.04em;",
        "    text-transform: uppercase; margin-bottom: 4px; }",
        "  .reading { color: var(--ink-2); font-size: 13.5px; margin: 12px 0 0;",
        "    border-top: 1px solid var(--grid); padding-top: 10px; }",
        "</style></head><body>",
        '<header class="top"><div>',
        f"<h1>{escape(page.title)}</h1>",
        f'<p class="sub">{escape(page.subtitle)}</p>',
        "</div><div class='spacer'></div>",
        '<button class="btn ghost" id="theme" type="button">Dark</button>',
        "</header>",
        '<div class="wrap io">',
    ]

    if page.links:
        parts.append('<div class="card"><div class="row" style="margin:0">')
        for label, href in page.links:
            external = href.startswith("http")
            target = ' target="_blank" rel="noopener"' if external else ""
            parts.append(
                f'<a class="btn ghost" style="text-decoration:none" href="{escape(href)}"'
                f"{target}>{escape(label)}</a>"
            )
        parts.append("</div></div>")

    parts.append(
        '<div class="card"><p class="hint" style="margin:0">Every figure below was produced '
        "by running the engine when this page was requested, not stored as text. It cannot "
        "disagree with the code serving it.</p></div>"
    )

    for section in page.sections:
        parts.append('<div class="card">')
        parts.append(f"<h2>{escape(section.title)}</h2>")
        if section.note:
            parts.append(f'<p class="hint">{escape(section.note)}</p>')
        parts.append(f"<p class='given'><b>input</b>{escape(section.given)}</p>")
        if section.output:
            parts.append(f'<pre class="out">{escape(section.output)}</pre>')
        if section.rows:
            parts.append('<div class="scroll"><table><tbody>')
            for row in section.rows:
                unit = (
                    f'<span style="color:var(--ink-muted)"> {escape(row.unit)}</span>'
                    if row.unit
                    else ""
                )
                parts.append(
                    f"<tr><td>{escape(row.label)}</td>"
                    f'<td class="num">{escape(row.value)}{unit}</td></tr>'
                )
            parts.append("</tbody></table></div>")
        if section.reading:
            parts.append(f'<p class="reading">{escape(section.reading)}</p>')
        parts.append("</div>")

    if page.caveat:
        parts.append(f'<div class="card"><div class="note warn">{escape(page.caveat)}</div></div>')

    parts += [
        "</div>",
        '<footer class="foot">Standard library only &mdash; no model, no GPU, no API key.</footer>',
        "<script>",
        'let dark = window.matchMedia("(prefers-color-scheme: dark)").matches;',
        "function paint() {",
        '  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");',
        '  document.getElementById("theme").textContent = dark ? "Light" : "Dark";',
        "}",
        'document.getElementById("theme")',
        '  .addEventListener("click", () => { dark = !dark; paint(); });',
        "paint();",
        "</script>",
        "</body></html>",
    ]
    return "\n".join(parts)


def attach_io(app: App, build: callable[[], Page]) -> None:
    """Serve ``build()`` at ``/io``. Built per request, on purpose."""

    @app.route("GET", "/io")
    def io(_request: Request) -> Response:
        return Response.text(render(build()), content_type="text/html")
