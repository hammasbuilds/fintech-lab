"""The Guardian web UI and its JSON API.

Sessions live in memory, keyed by an opaque id, and the oldest is dropped once there are
more than ``MAX_SESSIONS``. A session holds a pending transfer, so letting the table grow
without bound would be both a leak and a place where someone else's half-finished
transfer sits around.
"""

from __future__ import annotations

import json
import secrets
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.theme import attach_theme
from shared.webkit import App, BadRequest, Request, Response, run

from . import behaviour, coercion, confirm, nlu, numerals, testset
from .benchmark import measure

STATIC = Path(__file__).with_name("static")
RESULTS = Path(__file__).with_name("results.json")
MAX_SESSIONS = 64
MAX_TEXT = 500

app = App("guardian", static_dir=STATIC)
attach_theme(app)

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


def main(port: int = 8103) -> None:
    run(app, port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the Guardian assistant.")
    parser.add_argument("--port", type=int, default=8103)
    main(parser.parse_args().port)
