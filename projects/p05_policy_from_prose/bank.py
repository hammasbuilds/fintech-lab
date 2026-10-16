"""60,000 credit policies, each one written out in prose and in the DSL.

Project 05 ships five policies in prose. Five is enough to ask a model to compile and
far too few to say anything about the language itself, so this samples the grammar:
gates, score rules, thresholds, limits and charges are drawn at random, the tree is
rendered to policy text, and the same tree is rendered to English.

Two things come out of it.

**A pair for every policy.** Prose in, reference policy out, with the tree that produced
both - which is what makes a model's answer gradeable at any size, not just on the five
that were written by hand.

**A grammar fuzzer.** 60,000 generated policies, each one compiled, printed back from the
tree and compiled again, then evaluated against real borrowers. Anything that does not
survive that is a defect in the language rather than a hard case: the text came out of
the printer, so the parser has to be able to read it.

What this does **not** check is whether a policy makes credit sense. The sampler will
happily award points for keeping a low balance, and the prose will faithfully say so.
That is the right trade for a language test - a grammar fuzzer that only emitted sensible
policies would exercise the sensible corner of the grammar - and it is why the sampled
policies are never used as a lending recommendation anywhere in this repo.
"""

from __future__ import annotations

import random

from shared import datasets

from ..p02_policy_bench import cohort

KEY = "generated/p05_policies"
FILENAME = "p05_policies.jsonl"
SEED = 20261019
COUNT = 60_000

#: Fields the synthetic cohort carries. Each one lists the thresholds it is worth setting
#: and **one phrase per comparator**, because the English has to say what the rule says.
#:
#: The first version of this file kept two phrases per field - one for declining, one for
#: awarding points - and used them wherever a rule of that kind appeared. It produced
#: pairs whose prose and policy were opposites: "someone who supports fewer than 6
#: dependants is worth 5 points" next to ``score +5 if dependants >= 6``. A bank of
#: 60,000 of those would have graded every correct answer wrong.
NUMERIC_FIELDS = (
    (
        "age",
        (18, 65),
        {"<": "is under {value}", ">=": "is {value} or older"},
    ),
    (
        "monthly_income",
        (10_000, 60_000),
        {
            "<": "earns less than {value} a month",
            ">=": "earns {value} or more a month",
        },
    ),
    (
        "wallet_months",
        (1, 36),
        {
            "<": "has less than {value} months of wallet history",
            ">=": "has at least {value} months of wallet history",
        },
    ),
    (
        "txn_velocity",
        (40, 200),
        {
            ">": "moves more than {value} transactions a month",
            "<": "moves fewer than {value} transactions a month",
        },
    ),
    (
        "prior_defaults",
        (1, 3),
        {
            ">=": "already has {value} or more defaults on file",
            "<": "has fewer than {value} defaults on file",
        },
    ),
    (
        "on_time_repayments",
        (1, 6),
        {
            "<": "has repaid on time fewer than {value} times",
            ">=": "has repaid on time at least {value} times",
        },
    ),
    (
        "dependants",
        (3, 8),
        {
            ">=": "supports {value} or more dependants",
            "<": "supports fewer than {value} dependants",
        },
    ),
    (
        "avg_balance",
        (1_000, 20_000),
        {
            "<": "keeps less than {value} on average",
            ">=": "keeps {value} or more on average",
        },
    ),
)

TEXT_FIELDS = (
    ("employment", cohort.EMPLOYMENT, "is {value}"),
    ("region", cohort.REGIONS, "lives in {value}"),
)

LIMIT_SHAPES = (
    ("min({share} * monthly_income, {cap})", "{percent} of monthly income, capped at {cap}"),
    ("{share} * monthly_income", "{percent} of monthly income"),
    ("min({share} * avg_balance, {cap})", "{percent} of the average balance, capped at {cap}"),
)


def _round_to(value: int, step: int) -> int:
    return max(step, (value // step) * step)


def policy_tree(rng: random.Random, index: int) -> dict:
    """Pick the parts of one policy. Nothing here knows how to print anything."""
    gate_fields = rng.sample(NUMERIC_FIELDS, rng.randint(1, 3))
    gates = []
    for name, (low, high), phrases in gate_fields:
        value = _round_to(rng.randint(low, high), 1 if high <= 40 else 1000)
        # A gate turns somebody away, so it takes whichever comparator reads as the bad
        # side of the threshold for that field - and the phrase that goes with it.
        comparator = ">=" if name in {"dependants", "prior_defaults"} else "<"
        if name == "txn_velocity":
            comparator = ">"
        gates.append(
            {
                "outcome": "refer" if rng.random() < 0.2 else "decline",
                "field": name,
                "comparator": comparator,
                "value": value,
                "phrase": phrases[comparator].format(value=f"{value:,}"),
            }
        )

    rules = []
    used: set[str] = set()
    for _ in range(rng.randint(2, 5)):
        name, (low, high), phrases = rng.choice(NUMERIC_FIELDS)
        if name in used:
            continue
        used.add(name)
        value = _round_to(rng.randint(low, high), 1 if high <= 40 else 1000)
        points = rng.choice((5, 10, 15, 20, 25, 30, -10, -15))
        comparator = rng.choice(sorted(phrases))
        rules.append(
            {
                "field": name,
                "comparator": comparator,
                "value": value,
                "points": points,
                "phrase": phrases[comparator].format(value=f"{value:,}"),
            }
        )
    if rng.random() < 0.4:
        name, options, phrase = rng.choice(TEXT_FIELDS)
        value = rng.choice(options)
        rules.append(
            {
                "field": name,
                "comparator": "==",
                "value": value,
                "points": rng.choice((5, 10, 15)),
                "phrase": phrase.format(value=value),
            }
        )

    if not any(rule["points"] > 0 for rule in rules):
        # A policy whose every rule takes points away can never approve anybody, and the
        # compiler says so. 9 in 400 came out that way before this line existed.
        name, (low, high), phrases = rng.choice(NUMERIC_FIELDS)
        comparator = rng.choice(sorted(phrases))
        value = _round_to(rng.randint(low, high), 1 if high <= 40 else 1000)
        rules.append(
            {
                "field": name,
                "comparator": comparator,
                "value": value,
                "points": rng.choice((10, 15, 20, 25)),
                "phrase": phrases[comparator].format(value=f"{value:,}"),
            }
        )

    reachable = sum(rule["points"] for rule in rules if rule["points"] > 0)
    threshold = rng.choice(
        [value for value in (5, 10, 15, 20, 25, 30, 35, 40, 45) if value <= reachable]
    )
    shape, shape_prose = rng.choice(LIMIT_SHAPES)
    share = rng.choice((0.1, 0.15, 0.2, 0.25, 0.3, 0.5))
    cap = rng.choice((5_000, 10_000, 15_000, 20_000, 25_000, 50_000))
    fee = rng.choice((1, 1.5, 2, 2.5, 3, 5))

    return {
        "id": f"pol-{index}",
        "name": f"Generated policy {index}",
        "version": rng.randint(1, 4),
        "currency": "PKR",
        "gates": gates,
        "rules": rules,
        "threshold": threshold,
        "limit": shape.format(share=share, cap=cap),
        "limit_prose": shape_prose.format(percent=f"{int(share * 100)} percent", cap=f"{cap:,}"),
        "fee_percent": fee,
    }


def render_policy(tree: dict) -> str:
    """The tree as policy text. This is the reference answer."""
    lines = [
        f'policy "{tree["name"]}"',
        f"version {tree['version']}",
        f"currency {tree['currency']}",
        "",
    ]
    for gate in tree["gates"]:
        value = gate["value"]
        shown = f'"{value}"' if isinstance(value, str) else value
        lines.append(
            f"{gate['outcome']} if {gate['field']} {gate['comparator']} {shown} "
            f'because "{gate["phrase"]}"'
        )
    lines.append("")
    for rule in tree["rules"]:
        value = rule["value"]
        shown = f'"{value}"' if isinstance(value, str) else value
        points = f"+{rule['points']}" if rule["points"] > 0 else str(rule["points"])
        lines.append(
            f"score {points} if {rule['field']} {rule['comparator']} {shown} "
            f'because "{rule["phrase"]}"'
        )
    lines.append("")
    lines.append(f"approve if score >= {tree['threshold']}")
    lines.append(f"limit {tree['limit']}")
    fee = tree["fee_percent"]
    lines.append(f"fee {fee:g}% of limit")
    return "\n".join(lines) + "\n"


def render_prose(tree: dict) -> str:
    """The same tree as a product owner would write it in a document."""
    parts = []
    declines = [gate for gate in tree["gates"] if gate["outcome"] == "decline"]
    refers = [gate for gate in tree["gates"] if gate["outcome"] == "refer"]
    if declines:
        parts.append(
            "We turn down anyone who " + ", or who ".join(gate["phrase"] for gate in declines) + "."
        )
    if refers:
        parts.append(
            "Send to an officer rather than declining anyone who "
            + ", or who ".join(gate["phrase"] for gate in refers)
            + "."
        )
    scoring = []
    for rule in tree["rules"]:
        points = rule["points"]
        verb = "is worth" if points > 0 else "costs"
        scoring.append(f"someone who {rule['phrase']} {verb} {abs(points)} points")
    if scoring:
        parts.append("For scoring: " + "; ".join(scoring) + ".")
    parts.append(f"We approve from {tree['threshold']} points.")
    parts.append(f"The limit is {tree['limit_prose']}.")
    parts.append(f"There is a one-off {tree['fee_percent']:g}% service fee on the limit.")
    return " ".join(parts)


def path():
    return datasets.generated(FILENAME)


def build(count: int = COUNT, seed: int = SEED) -> dict[str, object]:
    rng = random.Random(seed)

    def rows():
        for index in range(count):
            tree = policy_tree(rng, index)
            yield {
                "id": tree["id"],
                "prose": render_prose(tree),
                "reference": render_policy(tree),
                "tree": tree,
            }

    written = datasets.write_jsonl(path(), rows())
    return {"path": str(path()), "policies": written, "seed": seed}


def read(limit: int | None = None):
    return datasets.read_jsonl(datasets.require(path(), "make data"), limit=limit)
