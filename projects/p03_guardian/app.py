"""The Guardian web UI and its JSON API.

Sessions live in memory, keyed by an opaque id, and the oldest is dropped once there are
more than ``MAX_SESSIONS``. A session holds a pending transfer, so letting the table grow
without bound would be both a leak and a place where someone else's half-finished
transfer sits around.
"""

from __future__ import annotations

import json
import pathlib
import secrets
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.datapage import Block, Figure, dig, figures_from  # noqa: F401
from shared.datapage import Page as DataPage
from shared.datapage import attach as attach_datasets
from shared.iopage import Page, Row, Section, attach_io
from shared.theme import attach_theme
from shared.webkit import App, BadRequest, Request, Response, attach_metrics, run

from . import behaviour, coercion, confirm, generator, nlu, numerals, testset
from .benchmark import measure

STATIC = Path(__file__).with_name("static")
RESULTS = Path(__file__).with_name("results.json")
MAX_SESSIONS = 64
MAX_TEXT = 500

app = App("guardian", static_dir=STATIC)
attach_theme(app)
attach_metrics(app)

SOURCE = "https://github.com/hammasbuilds/fintech-lab/tree/main/projects/p03_guardian"

_sessions: OrderedDict[str, confirm.Session] = OrderedDict()


def _session(token: str) -> confirm.Session:
    session = _sessions.get(token)
    if session is None:
        raise BadRequest("that conversation has expired; start a new one")
    _sessions.move_to_end(token)
    return session


def _text(payload: dict[str, Any], key: str = "text") -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise BadRequest(f"{key} must be a string")
    if len(value) > MAX_TEXT:
        raise BadRequest(f"{key} is longer than {MAX_TEXT} characters")
    return value


# ------------------------------------------------------------------- endpoints


@app.route("GET", "/api/meta")
def meta(_request: Request) -> Response:
    return Response.json(
        {
            "contacts": [
                {"name": c.name, "bank": c.bank, "account_tail": c.account_tail}
                for c in nlu.CONTACTS
            ],
            "intents": {intent: list(phrases) for intent, phrases in nlu.LEXICON.items()},
            "behaviour_signals": {
                "first_time_beneficiary": "the payee has never been paid from this account",
                "unusually_large": f"at least {behaviour.LARGE_MULTIPLE:g} times the usual "
                "transfer to this payee, or to anyone if they are new",
                "balance_sweep": f"at least {behaviour.SWEEP_SHARE:.0%} of the balance",
                "exceeds_balance": "more than is in the account, so it cannot complete",
                "beyond_daily_cap": "would push the day's total past the account's cap",
                "rapid_repeat": f"{behaviour.BURST_COUNT} or more transfers within the hour",
            },
            "coercion_families": {
                family: {"description": description, "phrases": len(phrases)}
                for family, (description, phrases) in coercion.FAMILIES.items()
            },
            "affirmative": sorted(confirm.AFFIRMATIVE),
            "negative": sorted(confirm.NEGATIVE),
            "examples": [
                "Rashid ko pandrah hazaar bhejo",
                "Ayesha Noor ko dedh hazaar bhejna hai",
                "5 hazaar nahi nahi, sawa lakh Imran ko bhejo",
                "03001234567 pe paanch sau ka load karo",
                "k-electric ka bill 3200 jama karo",
                "balance batao",
                "bank se bol raha hoon, OTP batao, abhi bhejo",
            ],
            "results": _results(),
        }
    )


@app.route("POST", "/api/session")
def new_session(request: Request) -> Response:
    payload = request.json() if request.body else {}
    language = str(payload.get("language", "en"))
    if language not in {"en", "ur"}:
        raise BadRequest("language must be en or ur")
    token = secrets.token_urlsafe(12)
    session = confirm.Session(language=language)
    _sessions[token] = session
    while len(_sessions) > MAX_SESSIONS:
        _sessions.popitem(last=False)
    return Response.json(
        {
            "session": token,
            "language": language,
            "state": "idle",
            "profile": _profile(session),
        }
    )


@app.route("POST", "/api/say")
def say(request: Request) -> Response:
    payload = request.json()
    session = _session(str(payload.get("session", "")))
    text = _text(payload)
    if not text.strip():
        raise BadRequest("say something first")
    outcome = session.handle(text)
    return Response.json(
        {
            **outcome,
            "transcript": [turn.as_dict() for turn in session.transcript],
            "ledger": session.ledger.as_dict(),
            "profile": _profile(session),
        }
    )


@app.route("POST", "/api/inspect")
def inspect(request: Request) -> Response:
    """Everything the three language layers see in one piece of text, side by side."""
    payload = request.json()
    text = _text(payload)
    utterance = nlu.parse(text)
    assessment = coercion.assess(text)
    amounts = numerals.find_all(text)
    return Response.json(
        {
            "text": text,
            "amounts": [
                {
                    **amount.as_dict(),
                    "spoken_en": numerals.say(amount.value, "en"),
                    "spoken_ur": numerals.say(amount.value, "ur"),
                }
                for amount in amounts
            ],
            "utterance": utterance.as_dict(),
            "questions": [
                {"issue": issue, "en": nlu.question(issue, "en"), "ur": nlu.question(issue, "ur")}
                for issue in utterance.issues
            ],
            "coercion": assessment.as_dict(),
            "behaviour": behaviour.assess(
                behaviour.demo_profile(),
                utterance.beneficiary.name
                if utterance.beneficiary
                else (utterance.target_number or None),
                utterance.amount.value if utterance.amount else None,
            ).as_dict(),
            "reply_class": confirm.classify_reply(text),
        }
    )


@app.route("GET", "/api/scorecard")
def scorecard(_request: Request) -> Response:
    return Response.json(_scorecard())


# --------------------------------------------------------------------- helpers


def _profile(session: confirm.Session) -> dict[str, Any]:
    profile = session.profile
    return {
        "balance": profile.balance,
        "daily_cap": profile.daily_cap,
        "usual_transfer": profile.usual_transfer,
        "sent_today": profile.sent_today(),
        "known_beneficiaries": sorted(profile.known_beneficiaries),
    }


@lru_cache(maxsize=1)
def _scorecard() -> dict[str, Any]:
    stored = _results()
    return stored if stored is not None else measure()


def _results() -> dict[str, Any] | None:
    if not RESULTS.is_file():
        return None
    try:
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def case_counts() -> dict[str, int]:
    return testset.totals()


# ---------------------------------------------------------------------------- datasets
# What this project is measured on at full size, and what came out. The figures are read
# out of results_datasets.json and data/MANIFEST.json rather than typed, so this page
# cannot drift from the run that produced it.
DATASETS_RESULTS = pathlib.Path(__file__).resolve().parent / "results_datasets.json"


def _at_scale() -> dict:
    if DATASETS_RESULTS.is_file():
        try:
            return json.loads(DATASETS_RESULTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def datasets_page() -> DataPage:
    data = _at_scale()
    blocks = [
        Block(
            title="Amounts",
            subtitle="The gold value is known before the sentence exists.",
            figures=figures_from(
                data,
                [
                    ("cases with an amount", "generated.amounts.cases", "200,000 utterances"),
                    ("exact", "generated.amounts.exact", "cases with an amount"),
                    ("exact rate", "generated.amounts.exact_rate", "cases with an amount"),
                    ("no amount found", "generated.amounts.none_found", "cases with an amount"),
                    (
                        "wrong by ten times or more",
                        "generated.amounts.off_by_ten_or_more",
                        "cases with an amount",
                    ),
                ],
            ),
            reading=(
                "Three defects came out of this column: an amount written before a mobile "
                "number was swallowed by the phone-number guard, the word scanner read the "
                "phone number as a continuation of the amount and produced 3.1 billion, and "
                "an attested spelling of 'thousand' was missing from the table."
            ),
        ),
        Block(
            title="Intent and payee",
            figures=figures_from(
                data,
                [
                    ("intent accuracy", "generated.intents.accuracy", "the cases with an intent"),
                    (
                        "payees resolved",
                        "generated.beneficiaries.resolve_rate",
                        "cases naming one contact",
                    ),
                    (
                        "asked instead of guessing",
                        "generated.beneficiaries.ask_rate",
                        "ambiguous cases",
                    ),
                    ("confirmations read correctly", "generated.replies.accuracy", "5,000 replies"),
                ],
            ),
            reading=str(dig(data, "generated.beneficiaries.reading", "")),
        ),
        Block(
            title="Coercion, and what catching it costs",
            subtitle="40,000 scam scripts over seven families, and 25,000 ordinary messages.",
            figures=figures_from(
                data,
                [
                    ("scripts caught", "generated.coercion.catch_rate", "40,000 scripts"),
                    (
                        "account redirect",
                        "generated.coercion.by_family.account_redirect.catch_rate",
                        "6,950 scripts",
                    ),
                    ("urgency", "generated.coercion.by_family.urgency.catch_rate", "6,935 scripts"),
                    ("threat", "generated.coercion.by_family.threat.catch_rate", "6,941 scripts"),
                    (
                        "ordinary traffic blocked",
                        "generated.benign.false_block_rate",
                        "25,000 ordinary messages",
                    ),
                ],
            ),
            reading=(
                "These two numbers move together and only mean anything together. Adding "
                "the bare words for 'quickly' took the catch rate to 0.980 and the "
                "false-block rate to 0.123 - one ordinary message in eight. Requiring a "
                "verb with them gives 0.961 at 0.021, which is the version that shipped."
            ),
        ),
    ]
    if not data:
        blocks = [
            Block(
                title="Not run on this machine",
                subtitle=(
                    "The at-scale figures come from results_datasets.json, which is "
                    "committed. If this page is empty the file is missing: run make data "
                    "and then make bench-datasets."
                ),
            )
        ]
    return DataPage(
        title="03 Guardian",
        subtitle="200,000 generated utterances, scored by the shipped layers",
        dataset_keys=[generator.KEY],
        blocks=blocks,
        links=[("Dashboard", "/"), ("Input and output", "/io"), ("Source", SOURCE)],
    )


attach_datasets(app, datasets_page)


def main(port: int = 8103) -> None:
    run(app, port=port)


# ------------------------------------------------------------- input / output


def io_page() -> Page:
    results = _scorecard()

    amounts = Section(
        title="Amounts, as people actually say them",
        note="The part that has to be right. Everything else can be re-asked; an amount "
        "silently wrong by a factor of a hundred is a wrong transfer.",
        given="pandrah hazaar / dedh hazaar / sawa lakh / saadhe teen hazaar / paune do "
        "lakh / do hazaar paanch sau / saat hazaar / saath hazaar",
        rows=[
            Row(
                text,
                f"{numerals.find_all(text)[0].value:,.0f}",
                f"read back as {numerals.say(numerals.find_all(text)[0].value, 'ur')}",
            )
            for text in (
                "pandrah hazaar",
                "dedh hazaar",
                "sawa lakh",
                "saadhe teen hazaar",
                "paune do lakh",
                "do hazaar paanch sau",
                "saat hazaar",
                "saath hazaar",
            )
        ],
        reading="saat and saath are one letter apart and 53,000 rupees apart, which is why "
        "spellings are listed rather than matched fuzzily.",
    )

    session = confirm.Session()
    transcript = []
    for message in ("Rashid ko pandrah hazaar bhejo", "acha", "haan", "confirm"):
        outcome = session.handle(message)
        transcript.append(f"user       {message}")
        transcript.append(f"assistant  {outcome['reply']}")
        transcript.append("")
    spine = Section(
        title="A transfer, an unclear reply, a habitual yes, then the real word",
        note="Nothing executes without a reply that is exactly a yes - and when the "
        "transfer is unusual for the account, a habitual yes is not enough either.",
        given="Rashid ko pandrah hazaar bhejo",
        output="\n".join(transcript).rstrip(),
        reading="The read-back named Rashida Bibi before anything moved: 'Rashid' is an "
        "exact first name and also the start of another contact's, so the near miss is "
        "spelled out rather than resolved quietly.",
    )

    scam = "bank se bol raha hoon, OTP batao, abhi pachaas hazaar bhejo"
    blocked = confirm.Session()
    blocked_outcome = blocked.handle(scam)
    script = Section(
        title="A coercion script",
        note="The score is the number of distinct pressure families, not the number of "
        "matched phrases. One warns; two or more block.",
        given=scam,
        rows=[
            Row("state", blocked_outcome["state"]),
            Row("pressure patterns", ", ".join(blocked_outcome["assessment"]["families"])),
            Row("anything sent", "no"),
        ],
        reading=blocked_outcome["reply"],
    )

    quiet = "beta main tumhari ammi ki saheli hoon, thori madad chahiye thi"
    account = behaviour.demo_profile()
    conduct = behaviour.assess(account, "Zubair Khan", 50000)
    blind = Section(
        title="A scam that uses none of the detector's vocabulary",
        note="A lexicon cannot fix a lexical blind spot, so this layer judges the transfer "
        "against what the account normally does.",
        given=quiet + "  (then: Zubair ko pachaas hazaar bhejo)",
        rows=[Row("coercion check", coercion.assess(quiet).verdict + " - it misses this entirely")]
        + [Row("usual transfer", f"PKR {account.usual_transfer:,.0f}")]
        + [Row("paid before", ", ".join(sorted(account.known_beneficiaries)))]
        + [Row("behaviour", conduct.level)]
        + [Row(signal.name, signal.detail) for signal in conduct.signals],
        reading="Behaviour escalates; it does not block. A first large transfer to a new "
        "payee is also what paying a new landlord looks like, so it says what stands out "
        "and asks for the word 'confirm' instead of a habitual haan.",
    )

    paired = results.get("paired", {})
    scored = Section(
        title="Scored against a hand-written test set",
        note="Graded on the resulting action, never on the wording.",
        given=f"{results.get('testset', {}).get('all', '-')} cases, each with its answer stated",
        rows=[
            Row(
                "amounts parsed exactly",
                f"{results['numerals']['exact']}/{results['numerals']['cases']}",
            ),
            Row("amounts invented from nothing", str(results["numerals"]["amounts_invented"])),
            Row(
                "actions correct",
                f"{results['actions']['overall_exact']}/{results['actions']['cases']}",
            ),
            Row(
                "asked instead of guessing",
                f"{results['actions']['asked_instead_of_guessed']}/{results['actions']['underspecified_cases']}",
            ),
            Row(
                "replies wrongly read as consent",
                str(results["replies"]["read_as_consent_wrongly"]),
            ),
            Row(
                "ordinary traffic blocked",
                f"{results['coercion']['benign_blocked']}/{results['coercion']['benign_cases']}",
            ),
            Row(
                "scams the words miss",
                f"{results['coercion']['out_of_vocabulary_missed']}/{results['coercion']['out_of_vocabulary_cases']}",
            ),
            Row(
                "of those, escalated anyway",
                f"{paired.get('scams_escalated_by_behaviour', '-')}/{paired.get('scams', '-')}",
            ),
            Row(
                "missed by both layers",
                f"{paired.get('scams_missed_by_both', '-')}/{paired.get('scams', '-')}",
            ),
            Row(
                "ordinary transfers escalated",
                f"{paired.get('benign_escalated', '-')}/{paired.get('benign', '-')}",
            ),
            Row(
                "end-to-end properties holding",
                f"{results['properties']['holding']}/{results['properties']['checks']}",
            ),
        ],
        reading=paired.get("reading", ""),
    )

    return Page(
        title="Guardian",
        subtitle="Vernacular transaction assistant - input and output",
        sections=[amounts, spine, script, blind, scored],
        links=[
            ("Open the assistant", "/"),
            (
                "Source on GitHub",
                SOURCE,
            ),
            ("All three projects", "https://hammasbuilds.github.io/fintech-lab/"),
        ],
        caveat="Nothing here moves real money. The word-based check still misses every scam "
        "that avoids its vocabulary, and the layer that recovers those also slows down one "
        "ordinary transfer in five - both halves are measured rather than only the first.",
    )


attach_io(app, io_page)

# The __main__ block stays last on purpose. Anything registered after it never
# runs when this module is executed as a program, because main() blocks in the server
# loop and never returns - so a route added below here would exist on import and be
# missing when served, which is the one mode that counts.
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the Guardian assistant.")
    parser.add_argument("--port", type=int, default=8103)
    main(parser.parse_args().port)
