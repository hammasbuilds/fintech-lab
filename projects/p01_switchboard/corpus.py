"""A corpus of ISO 8583 traffic: 120,000 messages over 50,960 transaction lifecycles.

There is no public corpus of real card traffic and there is not going to be one - a
switch log is full of PANs, and PCI DSS exists. So this generates one, and the generator
is the thing to read rather than trust: every message in it is assembled through the
same packer the product uses, re-parsed on the way out, and discarded if it does not
survive the trip. A corrupt message cannot get into the corpus by accident, which is
what makes the fuzz numbers measured against it mean anything.

**What varies.** One random context per lifecycle, seeded, so the whole corpus is
reproducible from a number:

* a Luhn-valid PAN on one of four BIN ranges, expiring 2027-2031
* an amount drawn log-uniformly between 100 and 500,000 minor units, so the corpus has
  both a 1.00 balance enquiry and a 5,000.00 withdrawal rather than one typical size
* one of five currencies (PKR, USD, AED, SAR, GBP) and an MCC drawn from eight
* a terminal, an acquirer, a merchant name and city, an account number
* a transmission date and time anywhere in a year, with the local date and time derived
  from it, and a trace number that does not collide inside a lifecycle

**Why 120,000.** The product's own benchmark checks 22 invariants over the 14 scenarios
it ships, which is 14 flows. That tells you the rules fire on the cases they were written
for and nothing about whether they fire on anything else. Over 50,960 flows, a rule that
is accidentally specific to one amount, one currency or one hour of the day shows up as a
finding on a flow that is valid by construction - which is exactly how the first two
defects in this file's history were found.

Written as JSON lines to ``data/generated/p01_messages.jsonl``: one object per message,
carrying its flow, its step, its direction, its data elements, and both framings as hex.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass

from shared import datasets

from . import binary, scenarios
from .codec import Message, luhn_ok, pack

KEY = "generated/p01_messages"
FILENAME = "p01_messages.jsonl"

#: Enough lifecycles to pass 120,000 messages. 14 scenarios a lifecycle, and the
#: scenarios average just over two messages each.
DEFAULT_FLOWS = 3_640
SEED = 20261016

BINS = ("411111", "521234", "627654", "558011")
#: Currencies with three different minor-unit exponents, on purpose: PKR, USD, AED and
#: GBP have two decimal places, JPY and KRW have none, and KWD and BHD have three. Every
#: message in the first version of this corpus was in a 2-decimal currency, so the whole
#: class of exponent bugs was invisible to it.
CURRENCIES = ("586", "840", "784", "826", "392", "410", "414", "048")

#: Minor-unit exponents, so a generated amount is sensible in its own currency: 500,000
#: minor units is 5,000.00 in rupees and 500 dinars, which are not the same transaction.
EXPONENTS = {"392": 0, "410": 0, "414": 3, "048": 3}
MCCS = ("6011", "5411", "5812", "5541", "4111", "5912", "7011", "5999")
CITIES = (
    ("LAHORE", "PK"),
    ("KARACHI", "PK"),
    ("ISLAMABAD", "PK"),
    ("DUBAI", "AE"),
    ("RIYADH", "SA"),
    ("LONDON", "GB"),
)
MERCHANTS = (
    "1LINK ATM GULBERG",
    "HBL ATM DHA PHASE 5",
    "IMTIAZ SUPERMARKET",
    "AL FATAH DEPARTMENT",
    "PSO FUEL STATION",
    "SHAHEEN CHEMIST",
    "SERENA HOTEL",
    "DARAZ ONLINE",
)
ACQUIRERS = ("0037101", "0037102", "0048220", "0051900")


def _luhn_complete(prefix: str, rng: random.Random) -> str:
    """A 16-digit PAN on ``prefix`` whose check digit is right.

    Generated rather than taken from a list because an invariant in this project checks
    the check digit, and a corpus full of PANs that fail it would make that invariant
    look broken on valid traffic.
    """
    body = prefix + "".join(str(rng.randrange(10)) for _ in range(15 - len(prefix)))
    for digit in range(10):
        candidate = body + str(digit)
        if luhn_ok(candidate):
            return candidate
    raise AssertionError("no check digit completes this PAN, which cannot happen")


def random_context(rng: random.Random) -> scenarios.Context:
    city, country = rng.choice(CITIES)
    merchant = rng.choice(MERCHANTS)
    # Log-uniform so the corpus is not all mid-sized amounts, then scaled into the
    # currency's own minor units: the same purchase is 500,000 minor units in rupees and
    # 5,000 in yen.
    currency = rng.choice(CURRENCIES)
    exponent = EXPONENTS.get(currency, 2)
    major = math.exp(rng.uniform(math.log(1), math.log(5_000)))
    amount = max(1, int(major * (10**exponent)))
    month = rng.randrange(1, 13)
    day = rng.randrange(1, 29)
    hour, minute, second = rng.randrange(24), rng.randrange(60), rng.randrange(60)
    local_date = f"{month:02d}{day:02d}"
    local_time = f"{hour:02d}{minute:02d}{second:02d}"
    acquirer = rng.choice(ACQUIRERS)
    terminal = f"ATM{rng.randrange(10_000, 99_999)}"[:8]
    name = f"{merchant} {city}".ljust(36)[:36] + ""
    return scenarios.Context(
        pan=_luhn_complete(rng.choice(BINS), rng),
        expiry=f"{rng.randrange(27, 32)}{rng.randrange(1, 13):02d}",
        amount=amount,
        currency=currency,
        stan=rng.randrange(1, 900_000),
        terminal=terminal,
        acquirer=acquirer,
        forwarder=acquirer,
        merchant_id=f"MCC{rng.choice(MCCS)}{country}{rng.randrange(10_000, 99_999)}"[:15],
        merchant=(name[:33] + country).ljust(40)[:40],
        mcc=rng.choice(MCCS),
        transmitted=local_date + local_time[:4] + f"{second:02d}",
        year=f"{rng.randrange(26, 31)}",
        local_date=local_date,
        local_time=local_time,
        rrn=f"{rng.randrange(100_000_000_000, 999_999_999_999)}",
        account=f"PK{rng.randrange(10, 99)}ABCD{rng.randrange(10**11, 10**12 - 1)}"[:18],
        pin_block="".join(rng.choice("0123456789ABCDEF") for _ in range(16)),
    )


@dataclass
class CorpusFlow:
    flow_id: int
    context_seed: int
    flow: scenarios.Flow


def flows(count: int = DEFAULT_FLOWS, seed: int = SEED) -> Iterator[CorpusFlow]:
    """Every scenario, ``count`` times, each repetition from its own random context."""
    rng = random.Random(seed)
    flow_id = 0
    for repetition in range(count):
        context = random_context(rng)
        for scenario in scenarios.SCENARIOS:
            # A fresh copy per scenario so trace numbers do not interleave, exactly as
            # scenarios.build_all does for the shipped fourteen.
            per_scenario = scenarios.Context(**vars(context))
            built = scenarios.build(scenario.key, per_scenario)
            yield CorpusFlow(flow_id=flow_id, context_seed=repetition, flow=built)
            flow_id += 1


def message_rows(flow: CorpusFlow) -> Iterator[dict]:
    for index, step in enumerate(flow.flow.steps):
        message: Message = step.message
        yield {
            "flow_id": flow.flow_id,
            "scenario": flow.flow.key,
            "step": index,
            "direction": step.direction,
            "label": step.label,
            "mti": message.mti,
            "fields": {str(de): value for de, value in sorted(message.fields.items())},
            "ascii": pack(message),
            "binary_hex": binary.pack_binary(message).hex().upper(),
        }


def path():
    return datasets.generated(FILENAME)


def build(count: int = DEFAULT_FLOWS, seed: int = SEED) -> dict[str, object]:
    """Write the corpus and return what was written."""
    target = path()
    scenario_counts: dict[str, int] = {}
    mti_counts: dict[str, int] = {}
    flow_total = 0

    def rows() -> Iterator[dict]:
        nonlocal flow_total
        for item in flows(count, seed):
            flow_total += 1
            scenario_counts[item.flow.key] = scenario_counts.get(item.flow.key, 0) + 1
            for row in message_rows(item):
                mti_counts[row["mti"]] = mti_counts.get(row["mti"], 0) + 1
                yield row

    written = datasets.write_jsonl(target, rows())
    return {
        "path": str(target),
        "messages": written,
        "flows": flow_total,
        "scenarios": dict(sorted(scenario_counts.items())),
        "mti": dict(sorted(mti_counts.items())),
        "seed": seed,
    }


def read(limit: int | None = None) -> Iterator[dict]:
    return datasets.read_jsonl(datasets.require(path(), "make data"), limit=limit)


def message_from_row(row: dict) -> Message:
    return Message(
        mti=row["mti"],
        fields={int(de): value for de, value in row["fields"].items()},
    )


if __name__ == "__main__":
    import json

    print(json.dumps(build(), indent=2)[:600])
