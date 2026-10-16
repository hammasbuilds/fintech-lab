"""Render a project's dataset page: what it is measured on, and what came out.

Each project serves this at ``/datasets``. It answers three questions that the dashboard
deliberately does not, because the dashboard shows the small reproducible run:

1. **What is the dataset?** Row count, byte size, sha256 and where it came from, read out
   of ``data/MANIFEST.json`` rather than typed. If the number on this page and the number
   in the README disagree, the README is wrong.
2. **What does the project score on all of it?** The at-scale figures, from the
   ``results_datasets.json`` the project committed.
3. **Where a number is a sample, how large a sample?** The model arms answer tens of
   cases drawn from banks of tens of thousands. The share is printed next to the figure,
   because a count read as a rate is the easiest way to mislead with a true number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from .datasets import load_manifest
from .webkit import App, Request, Response


@dataclass
class Figure:
    """One measured number, with the denominator it was measured over."""

    label: str
    value: str
    over: str = ""
    note: str = ""


@dataclass
class Block:
    title: str
    subtitle: str = ""
    figures: list[Figure] = field(default_factory=list)
    reading: str = ""


@dataclass
class Page:
    title: str
    subtitle: str
    dataset_keys: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    caveat: str = ""
    missing_note: str = "This dataset has not been built on this machine yet. Run: make data"


def _bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} GB"


def render(page: Page) -> str:
    manifest = load_manifest()
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(page.title)} &mdash; datasets</title>",
        '<link rel="stylesheet" href="/theme.css">',
        "<style>",
        "  .data { max-width: 940px; }",
        "  .data h2 { font-size: 14px; margin: 0 0 2px; }",
        "  .fig { display: flex; justify-content: space-between; gap: 16px;",
        "    border-bottom: 1px solid var(--grid); padding: 7px 0; }",
        "  .fig:last-child { border-bottom: 0; }",
        "  .fig .k { color: var(--ink-2); }",
        "  .fig .v { font-family: var(--mono); font-variant-numeric: tabular-nums;",
        "    white-space: nowrap; }",
        "  .fig .o { color: var(--ink-muted); font-size: 12.5px; }",
        "  .sha { font-family: var(--mono); font-size: 11.5px; color: var(--ink-muted); }",
        "  .reading { color: var(--ink-2); font-size: 13.5px; margin: 12px 0 0;",
        "    border-top: 1px solid var(--grid); padding-top: 10px; }",
        "</style></head><body>",
        '<header class="top"><div>',
        f"<h1>{escape(page.title)}</h1>",
        f'<p class="sub">{escape(page.subtitle)}</p>',
        "</div><div class='spacer'></div>",
        '<button class="btn ghost" id="theme" type="button">Dark</button>',
        "</header>",
        '<div class="wrap data">',
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

    parts.append('<div class="card">')
    parts.append("<h2>The dataset</h2>")
    parts.append(
        '<p class="hint">Row counts, sizes and fingerprints come from data/MANIFEST.json, '
        "which is written by counting the files rather than by quoting them.</p>"
    )
    parts.append('<div class="scroll"><table><thead><tr>')
    for column in ("Dataset", "Rows", "Size", "Role", "Source", "sha256"):
        parts.append(f"<th>{column}</th>")
    parts.append("</tr></thead><tbody>")
    for key in page.dataset_keys:
        entry = manifest.get(key)
        if not entry:
            parts.append(
                f'<tr><td>{escape(key)}</td><td colspan="5">'
                f'<span class="o">{escape(page.missing_note)}</span></td></tr>'
            )
            continue
        parts.append(
            f"<tr><td>{escape(key)}</td>"
            f'<td class="num">{entry["rows"]:,}</td>'
            f'<td class="num">{_bytes(int(entry["bytes"]))}</td>'
            f"<td>{escape(str(entry.get('role', 'cohort')))}</td>"
            f"<td>{escape(str(entry.get('source', '')))}</td>"
            f'<td class="sha">{escape(str(entry.get("sha256", ""))[:16])}&hellip;</td></tr>'
        )
    parts.append("</tbody></table></div>")
    notes = [str(manifest[key].get("note", "")) for key in page.dataset_keys if key in manifest]
    for note in notes:
        if note:
            parts.append(f'<p class="hint">{escape(note)}</p>')
    parts.append("</div>")

    for block in page.blocks:
        parts.append('<div class="card">')
        parts.append(f"<h2>{escape(block.title)}</h2>")
        if block.subtitle:
            parts.append(f'<p class="hint">{escape(block.subtitle)}</p>')
        for figure in block.figures:
            over = f'<div class="o">over {escape(figure.over)}</div>' if figure.over else ""
            note = f'<div class="o">{escape(figure.note)}</div>' if figure.note else ""
            parts.append(
                '<div class="fig">'
                f'<div class="k">{escape(figure.label)}{note}</div>'
                f'<div class="v">{escape(figure.value)}{over}</div>'
                "</div>"
            )
        if block.reading:
            parts.append(f'<p class="reading">{escape(block.reading)}</p>')
        parts.append("</div>")

    if page.caveat:
        parts.append(f'<div class="card"><div class="note warn">{escape(page.caveat)}</div></div>')

    parts += [
        "</div>",
        '<footer class="foot">Standard library only &mdash; the datasets are generated or '
        "downloaded, never committed.</footer>",
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


def attach(app: App, build) -> None:
    """Serve ``build()`` at ``/datasets``, built per request like ``/io``."""

    @app.route("GET", "/datasets")
    def datasets_page(_request: Request) -> Response:
        return Response.text(render(build()), content_type="text/html")

    @app.route("GET", "/api/datasets")
    def datasets_api(_request: Request) -> Response:
        page = build()
        manifest = load_manifest()
        return Response.json(
            {
                "title": page.title,
                "datasets": {
                    key: manifest.get(key, {"missing": True}) for key in page.dataset_keys
                },
                "blocks": [
                    {
                        "title": block.title,
                        "figures": [
                            {"label": f.label, "value": f.value, "over": f.over}
                            for f in block.figures
                        ],
                    }
                    for block in page.blocks
                ],
            }
        )


def dig(data: object, path: str, default: object = None) -> object:
    """Read ``a.b.c`` out of nested dicts. Returns ``default`` when any step is absent."""
    current = data
    for step in path.split("."):
        if not isinstance(current, dict) or step not in current:
            return default
        current = current[step]
    return current


def show(value: object) -> str:
    """Format a measured value without inventing precision."""
    if value is None:
        return "not measured"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".") if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


def figures_from(data: object, spec: list[tuple[str, str, str]]) -> list[Figure]:
    """Build figures from ``(label, dotted path, denominator)`` triples.

    The denominator is part of the spec rather than optional prose: a figure that cannot
    say what it was measured over does not belong on this page.
    """
    out: list[Figure] = []
    for label, path, over in spec:
        out.append(Figure(label=label, value=show(dig(data, path)), over=over))
    return out
