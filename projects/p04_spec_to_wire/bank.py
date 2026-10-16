"""60,000 wire tasks drawn from project 01's corpus, and the oracle that scores them.

Project 04 ships 29 hand-written tasks. That is a reasonable number of prompts to send a
model and a useless number for measuring a parser, so the same four task families are
generated from the 120,120-message corpus instead: every task's answer is already known,
because project 01 built the message.

The four families, unchanged from the hand-written set:

``decode``
    here is a message, what is the MTI, the amount, the trace, the terminal
``fields``
    here is a message, which data elements are present
``negative``
    here is a message with one thing wrong, say what
``wire``
    here are the fields, build the message

The point of generating them is the **baseline**. A model answers 29 prompts; project
01's own codec answers all 60,000, and that is the column every model number is read
against. A baseline measured on 29 cases cannot distinguish a parser that is right from a
parser that is right about those 29.
"""

from __future__ import annotations

import random

from shared import datasets

from ..p01_switchboard import binary, corpus, fields, lifecycle
from ..p01_switchboard.codec import CodecError, is_request, pack, response_mti, unpack

KEY = "generated/p04_tasks"
FILENAME = "p04_tasks.jsonl"
SEED = 20261018

#: 15,000 of each family.
PLAN = {"decode": 15_000, "fields": 15_000, "negative": 15_000, "wire": 15_000}

#: What a decode task asks for, and the data element it comes from.
DECODE_SLOTS = (
    ("mti", None),
    ("amount", 4),
    ("trace", 11),
    ("terminal", 41),
    ("pan", 2),
    ("currency", 49),
    ("processing_code", 3),
)

#: The faults a negative task carries, and the names that are a *true* description of
#: the damage once it is done.
#:
#: Two things are written into this table rather than discovered later. The first is that
#: a fault has to survive being packed: an earlier version injected a two-digit trace
#: number, the codec padded it back to six on the way out, and the task asked about a
#: fault that was no longer in the message. The second is that a flipped bitmap bit is
#: detectable but not *distinguishable* - it shifts the layout, so the field that lands
#: where the amount should be is genuinely not numeric. Project 01 publishes the reason:
#: an ISO 8583 bitmap carries no redundancy, so nothing downstream can tell you which bit
#: moved. Naming that message "malformed" is correct, and demanding the word "bitmap" would
#: be scoring a guess.
FAULTS = {
    "amount_is_not_numeric": ("amount_is_not_numeric",),
    "missing_processing_code": ("missing_processing_code",),
    "response_mti_is_a_request": ("response_mti_is_a_request",),
    "currency_has_letters": ("currency_has_letters",),
    "pan_fails_its_check_digit": ("pan_fails_its_check_digit",),
    "bitmap_claims_an_absent_field": (
        "bitmap_claims_an_absent_field",
        "amount_is_not_numeric",
        "currency_has_letters",
        "unparseable",
    ),
}


def _slot_value(message, slot: str, de: int | None) -> str | None:
    if slot == "mti":
        return message.mti
    return message.get(de) if de is not None else None


def _decode_task(row: dict, rng: random.Random) -> dict | None:
    message = corpus.message_from_row(row)
    slot, de = rng.choice(DECODE_SLOTS)
    answer = _slot_value(message, slot, de)
    if answer is None:
        return None
    return {
        "family": "decode",
        "flow_id": row["flow_id"],
        "scenario": row["scenario"],
        "ask": slot,
        "message": row["ascii"],
        "answer": answer,
    }


def _fields_task(row: dict, rng: random.Random) -> dict:
    message = corpus.message_from_row(row)
    del rng
    return {
        "family": "fields",
        "flow_id": row["flow_id"],
        "scenario": row["scenario"],
        "ask": "which data elements are present",
        "message": row["ascii"],
        "answer": sorted(message.fields),
    }


def _negative_task(row: dict, rng: random.Random) -> dict | None:
    """Break one thing, and record which thing. The breakage is the answer."""
    message = corpus.message_from_row(row)
    fault = rng.choice(sorted(FAULTS))
    broken = None
    if fault == "amount_is_not_numeric" and message.get(4):
        broken = message.replace(de4="00000000A000")
    elif fault == "pan_fails_its_check_digit" and message.get(2):
        pan = message.get(2) or ""
        last = str((int(pan[-1]) + 5) % 10)
        broken = message.replace(de2=pan[:-1] + last)
    elif fault == "missing_processing_code" and message.get(3):
        broken = message.replace(de3=None)
    elif fault == "response_mti_is_a_request" and not message.mti.endswith("0"):
        broken = message.replace(mti=message.mti[:3] + "0")
    elif fault == "currency_has_letters" and message.get(49):
        broken = message.replace(de49="5X6")
    elif fault == "bitmap_claims_an_absent_field":
        # Pack, then flip a bitmap bit so the layout claims a field that is not there.
        text = pack(message)
        bitmap = text[4:20]
        flipped = ("1" if bitmap[-1] != "1" else "2") + bitmap[1:]
        broken_text = text[:4] + flipped + text[20:]
        return {
            "family": "negative",
            "flow_id": row["flow_id"],
            "scenario": row["scenario"],
            "ask": "what is wrong with this message",
            "message": broken_text,
            "answer": fault,
            "also_true": list(FAULTS[fault]),
        }
    if broken is None:
        return None
    try:
        text = pack(broken)
    except CodecError:
        # The packer refuses some breakages outright, which is itself correct; those
        # cases cannot become a task because there is no message to show.
        return None
    return {
        "family": "negative",
        "flow_id": row["flow_id"],
        "scenario": row["scenario"],
        "ask": "what is wrong with this message",
        "message": text,
        "answer": fault,
        "also_true": list(FAULTS[fault]),
    }


def _wire_task(row: dict, rng: random.Random) -> dict:
    message = corpus.message_from_row(row)
    del rng
    return {
        "family": "wire",
        "flow_id": row["flow_id"],
        "scenario": row["scenario"],
        "ask": "build this message",
        "fields": {str(de): value for de, value in sorted(message.fields.items())},
        "mti": message.mti,
        "answer": row["ascii"],
    }


BUILDERS = {
    "decode": _decode_task,
    "fields": _fields_task,
    "negative": _negative_task,
    "wire": _wire_task,
}


def path():
    return datasets.generated(FILENAME)


def build(plan: dict[str, int] | None = None, seed: int = SEED) -> dict[str, object]:
    """Draw tasks from the corpus. Needs the corpus, so ``make data`` comes first."""
    plan = plan or PLAN
    wanted = dict(plan)
    rng = random.Random(seed)
    counts: dict[str, int] = {family: 0 for family in plan}
    skipped = 0

    def rows():
        nonlocal skipped
        remaining = sum(wanted.values())
        for row in corpus.read():
            if remaining <= 0:
                return
            families = [family for family, left in wanted.items() if left > 0]
            if not families:
                return
            family = rng.choice(families)
            task = BUILDERS[family](row, rng)
            if task is None:
                skipped += 1
                continue
            wanted[family] -= 1
            remaining -= 1
            counts[family] += 1
            task["id"] = f"{family}-{counts[family]}"
            yield task

    written = datasets.write_jsonl(path(), rows())
    return {
        "path": str(path()),
        "tasks": written,
        "by_family": dict(sorted(counts.items())),
        "messages_skipped_because_the_fault_did_not_apply": skipped,
        "seed": seed,
    }


def read(limit: int | None = None):
    return datasets.read_jsonl(datasets.require(path(), "make data"), limit=limit)


# --------------------------------------------------------------------------- the oracle


def answer_with_the_product(task: dict) -> object:
    """What project 01 says. This is the baseline column, not a reference answer.

    It is the product answering the same question the model is asked, through the same
    code a switch would run - no special-casing per task, and no access to the answer.
    """
    family = task["family"]
    if family == "decode":
        message = unpack(task["message"])
        return _slot_value(message, task["ask"], dict(DECODE_SLOTS).get(task["ask"]))
    if family == "fields":
        return sorted(unpack(task["message"]).fields)
    if family == "wire":
        from ..p01_switchboard.codec import Message

        message = Message(
            mti=task["mti"], fields={int(de): value for de, value in task["fields"].items()}
        )
        return pack(message)
    if family == "negative":
        return diagnose(task["message"])
    raise ValueError(f"unknown family {family!r}")


def diagnose(text: str) -> list[str]:
    """Every fault the product can name in this message, not just the first one.

    A single injected fault often makes more than one true statement about the message,
    and a flipped bitmap bit makes several: the layout shifts, so the field that lands
    where the amount should be is not numeric *and* the message is short. Scoring this
    as "name the one fault I injected" gave 0.508 and measured the order of the checks
    in here rather than the product. So it returns everything it can see, and the
    benchmark reports two numbers - did it find a fault at all, and was the injected
    fault among the ones it named.
    """
    found: list[str] = []
    try:
        message = unpack(text)
    except CodecError as exc:
        detail = str(exc).lower()
        if "de 49" in detail or "currency" in detail:
            found.append("currency_has_letters")
        if "de 4" in detail or "amount" in detail:
            found.append("amount_is_not_numeric")
        if any(word in detail for word in ("class", "numeric", "character")):
            found.append("amount_is_not_numeric")
        if any(word in detail for word in ("length", "short", "ran out", "bitmap")):
            found.append("bitmap_claims_an_absent_field")
        if not found:
            found.append("unparseable")
        return sorted(set(found))

    if message.get(3) is None:
        found.append("missing_processing_code")
    trace = message.get(11)
    if trace is not None and len(trace) != 6:
        found.append("trace_too_short")
    for de, value in message.fields.items():
        spec = fields.get(de)
        if spec.cls in {"n", "ns"} and not value.strip().isdigit():
            found.append("currency_has_letters" if de == 49 else "amount_is_not_numeric")
    # Only the single-message checks. The pair rules compare a request with its
    # response, so on a one-message flow they fire by construction - which labelled
    # every short trace number as a request/response mismatch.
    for finding in lifecycle._check_every_message([message]):
        detail = finding.detail.lower()
        if "check digit" in detail or "luhn" in detail:
            found.append("pan_fails_its_check_digit")
        if "9f02" in detail or "chip" in detail:
            found.append("chip_disagrees_with_the_amount")
    if not is_request(message.mti) and message.get(39) is None:
        found.append("response_mti_is_a_request")
    return sorted(set(found))


def _present(text: str) -> bool:
    try:
        unpack(text)
    except CodecError:
        return False
    return True


def available_elements() -> int:
    return len(fields.ALL) if hasattr(fields, "ALL") else 0


def framing_of(text: str) -> str:
    return str(binary.detect(text).get("framing", "?"))


def response_for(mti: str) -> str:
    return response_mti(mti)
