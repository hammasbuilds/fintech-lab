"""Generate the published page in ``docs/`` from the projects' own results files.

    uv run python scripts/build_pages.py

The page is committed, and generated rather than written by hand, for the same reason
``results.json`` is: a published number typed into HTML drifts from the code that produced
it and nothing notices. CI regenerates this and fails if the committed copy differs, so the
page cannot quietly disagree with the repo.

It is deliberately static. GitHub Pages serves files, not Python, so the three dashboards
are local-only and the page says so and shows the command instead of pretending to host
them.
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REPO = "https://github.com/hammasbuilds/fintech-lab"

#: The suite size shown on the badge. A hand-written number here would be the first
#: thing to go stale, so a test asserts it against what pytest actually collected -
#: see test_the_published_page_quotes_the_real_test_count.
TESTS = 585

sys.path.insert(0, str(ROOT))

from observatory.registry import PROJECTS, format_value  # noqa: E402
from shared.theme import THEME_CSS  # noqa: E402


def read(project) -> dict:
    path = project.results_path
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def figures(project) -> list[tuple[str, str]]:
    data = read(project)
    if not data:
        return []
    out = []
    for label, path, how in project.headline:
        current: object = data
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        out.append((label, format_value(current, how, data)))
    return out


#: The one sentence each project should be remembered for, and the figure behind it.
CLAIMS = {
    "switchboard": (
        "Reads both the ASCII form a specification uses and the binary framing a live "
        "switch sends, and works out which it was given by trying each against the parser.",
        "38 of 38 injected faults killed, every one by the rule written for it, with zero "
        "false positives on valid traffic. Of 2,000 random character corruptions, all 651 "
        "that were illegal for their field were rejected.",
    ),
    "policy-bench": (
        "A lending policy written in words, compiled to something executable that prints "
        "back to the same text, and back-tested before it goes live.",
        "The v1 to v2 tightening declines 1,238 more applicants: 47 would have defaulted "
        "and 1,191 would have repaid. The cutoff sweep says why - the scorecard's Gini is "
        "0.284, so any cutoff cuts both groups at once.",
    ),
    "guardian": (
        "A transaction assistant for Roman Urdu and code-mixed speech, with no model: a "
        "lexicon, a grammar, and the account's own history.",
        "Its word-based check misses all 4 scams phrased in ordinary language, and that is "
        "published. Judging the transfer instead escalates 4 of 4, at a cost of 1 ordinary "
        "transfer in 5 also slowed.",
    ),
    "spec-to-wire": (
        "A local model asked to produce ISO 8583 traffic, graded by project 01's own "
        "parser and its 22 invariants.",
        "Every grade is computed, not judged: a message either packs or it does not, and "
        "the invariants either hold or name what broke. The deterministic generator sits "
        "in the comparison table at 1.00 on every family.",
    ),
    "policy-from-prose": (
        "A local model asked to turn a written credit policy into project 02's language, "
        "and then judged on what it decides.",
        "A compiled policy can be run, so the grader runs it over all 5,000 borrowers. The "
        "column that matters is the one no text check can see: policies that compile, read "
        "sensibly, and approve a different set of applicants.",
    ),
    "model-vs-lexicon": (
        "Project 03's test set answered by a local model and by the rules, through the "
        "same grader.",
        "Both columns come from the same cases. The rules score 0 of 4 on the scams they "
        "publicly miss - the one place a model has room to win - and the cost of winning "
        "there is counted in false alarms on ordinary traffic.",
    ),
}


def build() -> str:
    cards = []
    model_cards = []
    for project in PROJECTS:
        claim, number = CLAIMS[project.key]
        rows = "".join(
            f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in figures(project)
        )
        target = model_cards if project.needs_model else cards
        target.append(
            f"""      <div class="card proj">
        <span class="num">{escape(project.number)}</span>
        <h2>{escape(project.name)}</h2>
        <p class="tag">{escape(claim)}</p>
        <dl class="figs">{rows}</dl>
        <p class="hint small">From the project's own results.json, written by its benchmark.</p>
        <p class="claim">{escape(number)}</p>
        <div class="opener">
          <a class="btn" href="{escape(project.source_url)}">Source</a>
          <a class="btn ghost" href="{escape(project.readme_url)}">What it does and what it measures</a>
        </div>
      </div>"""
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fintech-lab</title>
<meta name="description" content="Three fintech products that run on the standard library: an ISO 8583 payment-message workbench, a lending-policy sandbox, and a Roman Urdu transaction assistant.">
<link rel="stylesheet" href="theme.css">
<style>
  .hero {{ padding: 36px 0 8px; }}
  .hero h1 {{ font-size: 30px; margin: 0 0 8px; letter-spacing: -0.02em; }}
  .hero p {{ color: var(--ink-2); font-size: 15.5px; max-width: 70ch; margin: 0 0 6px; }}
  .badges {{ display: flex; gap: 7px; flex-wrap: wrap; margin: 16px 0 0; }}
  .badges .pill {{ background: var(--surface); }}
  .grid3 {{ display: grid; gap: 16px; margin-top: 8px; }}
  @media (min-width: 1000px) {{ .grid3 {{ grid-template-columns: 1fr 1fr 1fr; }} }}
  .proj {{ display: flex; flex-direction: column; margin: 0; }}
  .proj .num {{ font-size: 11.5px; letter-spacing: 0.08em; color: var(--ink-muted); font-weight: 600; }}
  .proj h2 {{ font-size: 17px; margin: 2px 0 6px; }}
  .proj .tag {{ color: var(--ink-2); font-size: 13.5px; margin: 0 0 10px; }}
  .figs {{ display: grid; grid-template-columns: 1fr auto; gap: 3px 12px; font-size: 13px; margin: 0; }}
  .figs dt {{ color: var(--ink-2); }}
  .figs dd {{ margin: 0; font-variant-numeric: tabular-nums; font-weight: 600; }}
  .small {{ font-size: 12px; margin: 8px 0 0; }}
  .claim {{ font-size: 13.5px; margin: 10px 0 0; flex: 1 1 auto; }}
  .opener {{ display: flex; gap: 7px; flex-wrap: wrap; margin-top: 14px; }}
  .opener a {{ text-decoration: none; font-size: 12.5px; padding: 6px 11px; }}
  h2.group {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--ink-2); margin: 30px 0 10px; font-weight: 650; }}
  .lead {{ color: var(--ink-2); font-size: 13.5px; max-width: 80ch; margin: -2px 0 14px; }}
  code.cmd {{ font-family: var(--mono); background: var(--surface-sunken); padding: 2px 6px; border-radius: 5px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>fintech-lab</h1>
    <p>Six fintech projects on the standard library. Three products &mdash; an ISO 8583
       payment-message workbench, a lending-policy sandbox, and a transaction assistant for
       Roman Urdu &mdash; and three that ask a local model to do those same jobs and are
       scored by the products' own graders.</p>
    <p>No dependencies anywhere, and no model needed for the first three: the thing deciding
       the outcome is small enough to read, and what grades it is deterministic code rather
       than a model's opinion. That is also what makes the model arms worth anything &mdash;
       there is a baseline on the same cases that does not move.</p>
    <div class="badges">
      <span class="pill neutral"><span class="dot"></span><span>{TESTS} tests</span></span>
      <span class="pill good"><span class="dot"></span><span>zero runtime dependencies</span></span>
      <span class="pill good"><span class="dot"></span><span>GPU not required</span></span>
      <span class="pill neutral"><span class="dot"></span><span>Python 3.11+</span></span>
    </div>
    <div class="opener" style="margin-top:18px">
      <a class="btn" href="{REPO}">Repository</a>
      <a class="btn ghost" href="{REPO}#input--output">Input and output</a>
      <a class="btn ghost" href="{REPO}/actions/workflows/ci.yml">CI</a>
    </div>
  </div>

  <h2 class="group">Built with code &mdash; no model, no GPU</h2>
  <div class="grid3">
{chr(10).join(cards)}
  </div>

  <h2 class="group">The model arms &mdash; the same jobs, asked of a local model</h2>
  <p class="lead">Each one asks a local model to do what its pair does with code, and is
     scored by that pair's own grader. The deterministic version is in every comparison
     table, because a model number with no baseline beside it invites the reader to supply
     their own idea of what good looks like. Every reply is cached and the cache is
     committed, so the numbers can be rechecked without a GPU.</p>
  <div class="grid3">
{chr(10).join(model_cards)}
  </div>

  <div class="card">
    <h2>The dashboards run locally, and that is not a limitation being hidden</h2>
    <p class="hint">
      Each project serves a single-page dashboard, an input/output proof at <code
      class="cmd">/io</code>, and its own request counters. They need a Python process, and
      this page is static files on GitHub Pages, so they are not hosted here. One command
      brings all three up together with a control panel in front of them:
    </p>
    <pre class="out">git clone {REPO}
cd fintech-lab
uv run python -m observatory --start-all      # then open http://127.0.0.1:8100</pre>
    <p class="hint">
      The Observatory starts and stops each project, shows whether it is actually answering
      on its port rather than merely spawned, reads each one's own counters, and links
      through to every project's dashboard, proof page, source and README &mdash; so there is
      one place to open them from instead of a shortcut per project.
    </p>
    <p class="hint">
      Or run one on its own: <code class="cmd">make serve01</code>,
      <code class="cmd">make serve02</code>, <code class="cmd">make serve03</code>. To see
      the numbers above without a browser at all: <code class="cmd">uv run python demo.py</code>.
    </p>
  </div>

  <div class="card">
    <h2>What is deliberately not flattering</h2>
    <p class="hint">
      Each project publishes a figure that does not favour it, because the alternative is a
      page of round numbers that cannot be wrong.
    </p>
    <ul class="hint" style="padding-left:20px">
      <li><strong>Switchboard</strong> stops at 97.4% on random framing damage. An ISO 8583
          bitmap carries no redundancy, so one flipped bit can swap a field for another of the
          same width and produce a different valid message. No parser can catch that, which is
          why the invariant layer exists.</li>
      <li><strong>Policy Bench</strong> reports a Gini of 0.284 and calls it weak separation in
          those words. Its cohort is synthetic and its generator is written out in full, so every
          figure is a statement about that generator rather than about a market.</li>
      <li><strong>Guardian</strong> misses every scam that avoids its vocabulary, and the layer
          that recovers those also slows one ordinary transfer in five. Both halves are
          measured; reporting only the first would be advertising.</li>
      <li><strong>The model arms</strong> report what they cost and what they could not do:
          replies that hit the token cap, cases with no cached reply that were therefore
          <em>not run</em> rather than scored, and the two models excluded from the
          headline with the reason stated &mdash; one does not fit this machine's 16 GB
          without running partly on CPU, and the other is a base model that answered
          &ldquo;reply with OK&rdquo; with &ldquo;88888888&rdquo;.</li>
    </ul>
  </div>

  <footer class="foot" style="padding-left:0">
    Muhammad Hammas &middot; <a href="{REPO}">github.com/hammasbuilds/fintech-lab</a> &middot; MIT
  </footer>
</div>
</body>
</html>
"""


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    page = DOCS / "index.html"
    theme = DOCS / "theme.css"
    nojekyll = DOCS / ".nojekyll"

    page.write_text(build(), encoding="utf-8", newline="\n")
    theme.write_text(THEME_CSS.lstrip("\n"), encoding="utf-8", newline="\n")
    nojekyll.write_text("", encoding="utf-8", newline="\n")
    print(f"wrote {page.relative_to(ROOT)} ({page.stat().st_size:,} bytes)")
    print(f"wrote {theme.relative_to(ROOT)} ({theme.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
