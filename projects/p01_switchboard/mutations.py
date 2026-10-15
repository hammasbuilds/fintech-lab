"""Break valid flows on purpose and see whether the project notices.

A decoder and a rule list both look convincing while they are only ever shown correct
traffic. The honest question is how much broken traffic they reject, so this module
takes the fourteen scenarios, injects a catalogue of faults modelled on real production
defects, and reports the kill rate.

Two numbers come out of it, and the second is the one that matters:

* **killed** - the fault was rejected by something, anything.
* **killed by the intended rule** - the fault was rejected by the specific check written
  for it. A fault caught only by a different rule is a weaker result, because the rule
  it was meant to exercise is still unproven, so both figures are reported separately
  and the gap is never averaged away.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from . import lifecycle
from .codec import CodecError, Message, check_values, field_offsets, pack, unpack
from .scenarios import ACQ_TO_ISS, ISS_TO_ACQ, Context, Flow, Step, build

CODEC = "codec"
LIFECYCLE = "lifecycle"


@dataclass
class Mutant:
    mutator: str
    scenario: str
    layer: str
    expected: str
    description: str
    flow: Flow | None = None
    raw: str = ""


@dataclass
class Detection:
    killed: bool
    by: list[str]
    message: str

    @property
    def summary(self) -> str:
        return self.message if self.killed else "not detected"


# ----------------------------------------------------------------- flow helpers


def _clone(flow: Flow) -> Flow:
    return Flow(
        key=flow.key,
        title=flow.title,
        description=flow.description,
        steps=[
            replace(step, message=Message(step.message.mti, dict(step.message.fields)))
            for step in flow.steps
        ],
    )


def _first(flow: Flow, direction: str) -> int:
    for index, step in enumerate(flow.steps):
        if step.direction == direction:
            return index
    raise LookupError(f"flow {flow.key!r} has no {direction} step")


def _first_response(flow: Flow) -> int:
    return _first(flow, ISS_TO_ACQ)


def _original_data_elements(request: Message) -> str:
    return (
        request.mti
        + (request.get(11) or "000000")
        + (request.get(7) or "0000000000")
        + (request.get(32) or "0").zfill(11)
        + (request.get(33) or "0").zfill(11)
    )


# -------------------------------------------------------------- flow mutators


def _drop_response_code(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de39=None)
    return flow


def _alter_response_stan(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de11="999999")
    return flow


def _echo_request_mti(flow: Flow) -> Flow:
    i = _first_response(flow)
    request = flow.steps[_first(flow, ACQ_TO_ISS)].message
    flow.steps[i].message = Message(request.mti, dict(flow.steps[i].message.fields))
    return flow


def _change_response_amount(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de4="000000999999")
    return flow


def _full_partial_approval(flow: Flow) -> Flow:
    request = flow.steps[_first(flow, ACQ_TO_ISS)].message
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de4=request.fields[4])
    return flow


def _zero_partial_approval(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de4="000000000000")
    return flow


def _drop_original_data_elements(flow: Flow) -> Flow:
    for step in flow.steps:
        if step.message.mti.startswith("04"):
            step.message = step.message.replace(de90=None)
            break
    return flow


def _point_reversal_at_unknown_trace(flow: Flow) -> Flow:
    for step in flow.steps:
        if step.message.mti.startswith("04"):
            odes = step.message.fields[90]
            step.message = step.message.replace(de90=odes[:4] + "888888" + odes[10:])
            break
    return flow


def _drop_network_code(flow: Flow) -> Flow:
    for step in flow.steps:
        if step.message.mti.startswith("08"):
            step.message = step.message.replace(de70=None)
            break
    return flow


def _swap_response_pan(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de2="4111111111111129")
    return flow


def _alter_processing_code(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de3="012000")
    return flow


def _reverse_a_decline(flow: Flow) -> Flow:
    request = flow.steps[_first(flow, ACQ_TO_ISS)].message
    reversal = Message(
        "0400",
        {
            2: request.fields[2],
            3: request.fields[3],
            4: request.fields[4],
            7: request.fields[7],
            11: "005000",
            32: request.fields[32],
            33: request.fields[33],
            37: request.fields[37],
            41: request.fields[41],
            49: request.fields[49],
            90: _original_data_elements(request),
        },
    )
    flow.steps.append(Step(ACQ_TO_ISS, "reversal of a declined request", reversal))
    flow.steps.append(
        Step(
            ISS_TO_ACQ,
            "reversal accepted",
            Message(
                "0410",
                {
                    **{de: reversal.fields[de] for de in (2, 3, 4, 7, 11, 32, 33, 37, 41, 49)},
                    39: "00",
                },
            ),
        )
    )
    return flow


def _authorise_a_decline(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de38="A99999")
    return flow


def _switch_response_currency(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de49="840")
    return flow


def _reroute_response_terminal(flow: Flow) -> Flow:
    i = _first_response(flow)
    flow.steps[i].message = flow.steps[i].message.replace(de41="ATM99999")
    return flow


def _clone_request_trace(flow: Flow) -> Flow:
    i = _first(flow, ACQ_TO_ISS)
    original = flow.steps[i]
    flow.steps.append(
        Step(
            ACQ_TO_ISS,
            "duplicate of the first request",
            Message(original.message.mti, dict(original.message.fields)),
        )
    )
    return flow


def _strip_pin_block(flow: Flow) -> Flow:
    i = _first(flow, ACQ_TO_ISS)
    flow.steps[i].message = flow.steps[i].message.replace(de52=None)
    return flow


def _charge_an_inquiry(flow: Flow) -> Flow:
    i = _first(flow, ACQ_TO_ISS)
    flow.steps[i].message = flow.steps[i].message.replace(de4="000000250000")
    return flow


def _deliver_response_first(flow: Flow) -> Flow:
    flow.steps[0], flow.steps[1] = flow.steps[1], flow.steps[0]
    return flow


def _drop_the_response(flow: Flow) -> Flow:
    del flow.steps[_first_response(flow)]
    return flow


# ------------------------------------------------------------- codec mutators


def _corrupt_bitmap(raw: str) -> str:
    # Flip the last nibble of the primary bitmap, so the bitmap claims a field the
    # message does not carry.
    nibble = raw[19]
    flipped = format(int(nibble, 16) ^ 0x1, "X")
    return raw[:19] + flipped + raw[20:]


def _understate_llvar_length(raw: str) -> str:
    # DE 2 is the first variable field in these flows; its 2-digit length sits right
    # after the bitmap.
    start = 4 + (32 if raw[4].upper() in "89ABCDEF" else 16)
    return raw[:start] + "02" + raw[start + 2 :]


def _truncate_fixed_field(raw: str) -> str:
    return raw[:-7]


def _letter_in_numeric_field(raw: str) -> str:
    # Target DE 4 by name. An earlier version of this mutator corrupted a position near
    # the end of the message, which landed in DE 102 - an alphanumeric field where
    # letters are perfectly legal - so the mutant was valid traffic and survived for the
    # right reason. It was the mutator that was wrong, and the parser was wrong too:
    # it checked field widths and never field contents.
    offset, length = field_offsets(raw)[4]
    return raw[:offset] + "X" + raw[offset + 1 : offset + length] + raw[offset + length :]


def _overlong_llvar(raw: str) -> str:
    start = 4 + (32 if raw[4].upper() in "89ABCDEF" else 16)
    return raw[:start] + "99" + raw[start + 2 :]


def _trailing_garbage(raw: str) -> str:
    return raw + "0000"


# ------------------------------------------------------------------- catalogue


@dataclass(frozen=True)
class FlowMutator:
    name: str
    expected: str
    description: str
    scenarios: tuple[str, ...]
    apply: Callable[[Flow], Flow]


@dataclass(frozen=True)
class CodecMutator:
    name: str
    description: str
    scenarios: tuple[str, ...]
    apply: Callable[[str], str]


FLOW_MUTATORS: tuple[FlowMutator, ...] = (
    FlowMutator(
        "drop_response_code",
        "L03",
        "The issuer answers with no DE 39 at all.",
        ("atm_withdrawal_approved", "pos_purchase_approved"),
        _drop_response_code,
    ),
    FlowMutator(
        "alter_response_stan",
        "L02",
        "The response comes back on a different trace.",
        ("atm_withdrawal_approved",),
        _alter_response_stan,
    ),
    FlowMutator(
        "echo_request_mti",
        "L15",
        "The response repeats the request MTI unchanged. The intended rule is L15 "
        "rather than L02: a 0200 coming back is not a malformed response, it is a "
        "second request on a trace already in flight, which is what the checker "
        "says it is. The first run of this harness labelled it L02 and was wrong.",
        ("atm_withdrawal_approved",),
        _echo_request_mti,
    ),
    FlowMutator(
        "change_response_amount",
        "L04",
        "An approval silently changes DE 4.",
        ("atm_withdrawal_approved", "pos_purchase_approved"),
        _change_response_amount,
    ),
    FlowMutator(
        "full_partial_approval",
        "L05",
        "DE 39 10 approves the whole requested amount.",
        ("atm_partial_approval",),
        _full_partial_approval,
    ),
    FlowMutator(
        "zero_partial_approval",
        "L05",
        "DE 39 10 approves zero.",
        ("atm_partial_approval",),
        _zero_partial_approval,
    ),
    FlowMutator(
        "drop_original_data_elements",
        "L06",
        "A reversal arrives without DE 90.",
        ("atm_reversal_after_timeout", "atm_partial_reversal"),
        _drop_original_data_elements,
    ),
    FlowMutator(
        "point_reversal_at_unknown_trace",
        "L07",
        "DE 90 names a trace nothing in the flow sent.",
        ("atm_reversal_after_timeout",),
        _point_reversal_at_unknown_trace,
    ),
    FlowMutator(
        "drop_network_code",
        "L08",
        "A 0800 carries no DE 70.",
        ("network_echo_test", "network_sign_on"),
        _drop_network_code,
    ),
    FlowMutator(
        "swap_response_pan",
        "L09",
        "The response carries a different card number.",
        ("atm_withdrawal_approved",),
        _swap_response_pan,
    ),
    FlowMutator(
        "alter_processing_code",
        "L10",
        "The response changes DE 3.",
        ("atm_withdrawal_approved",),
        _alter_processing_code,
    ),
    FlowMutator(
        "reverse_a_decline",
        "L11",
        "A declined request is reversed anyway.",
        ("atm_insufficient_funds",),
        _reverse_a_decline,
    ),
    FlowMutator(
        "authorise_a_decline",
        "L12",
        "A decline carries an authorisation id.",
        ("atm_insufficient_funds", "atm_pin_tries_exceeded"),
        _authorise_a_decline,
    ),
    FlowMutator(
        "switch_response_currency",
        "L13",
        "The response answers in another currency.",
        ("atm_withdrawal_approved",),
        _switch_response_currency,
    ),
    FlowMutator(
        "reroute_response_terminal",
        "L14",
        "The response names a different terminal.",
        ("atm_withdrawal_approved",),
        _reroute_response_terminal,
    ),
    FlowMutator(
        "clone_request_trace",
        "L15",
        "The same request is sent twice on one trace.",
        ("atm_withdrawal_approved",),
        _clone_request_trace,
    ),
    FlowMutator(
        "strip_pin_block",
        "L16",
        "An unattended withdrawal carries no PIN block.",
        ("atm_withdrawal_approved",),
        _strip_pin_block,
    ),
    FlowMutator(
        "charge_an_inquiry",
        "L17",
        "A balance inquiry asks for a non-zero amount.",
        ("balance_inquiry",),
        _charge_an_inquiry,
    ),
    FlowMutator(
        "deliver_response_first",
        "L18",
        "The response is delivered before the request.",
        ("atm_withdrawal_approved",),
        _deliver_response_first,
    ),
    FlowMutator(
        "drop_the_response",
        "L01",
        "The request is never answered and never reversed.",
        ("atm_withdrawal_approved",),
        _drop_the_response,
    ),
)

CODEC_MUTATORS: tuple[CodecMutator, ...] = (
    CodecMutator(
        "corrupt_bitmap",
        "A bitmap bit is set for a field the message does not carry.",
        ("atm_withdrawal_approved",),
        _corrupt_bitmap,
    ),
    CodecMutator(
        "understate_llvar_length",
        "A variable field declares fewer characters than follow.",
        ("atm_withdrawal_approved",),
        _understate_llvar_length,
    ),
    CodecMutator(
        "truncate_fixed_field",
        "The message is cut off inside a fixed-width field.",
        ("atm_withdrawal_approved",),
        _truncate_fixed_field,
    ),
    CodecMutator(
        "letter_in_numeric_field",
        "Letters appear in a numeric data element.",
        ("atm_withdrawal_approved",),
        _letter_in_numeric_field,
    ),
    CodecMutator(
        "overlong_llvar",
        "A variable field declares more characters than its maximum.",
        ("atm_withdrawal_approved",),
        _overlong_llvar,
    ),
    CodecMutator(
        "trailing_garbage",
        "Characters follow the last data element.",
        ("atm_withdrawal_approved",),
        _trailing_garbage,
    ),
)


def generate() -> list[Mutant]:
    """Every mutator applied to every scenario it targets."""
    mutants: list[Mutant] = []
    for mutator in FLOW_MUTATORS:
        for scenario in mutator.scenarios:
            flow = mutator.apply(_clone(build(scenario, Context())))
            mutants.append(
                Mutant(
                    mutator=mutator.name,
                    scenario=scenario,
                    layer=LIFECYCLE,
                    expected=mutator.expected,
                    description=mutator.description,
                    flow=flow,
                )
            )
    for codec_mutator in CODEC_MUTATORS:
        for scenario in codec_mutator.scenarios:
            raw = build(scenario, Context()).steps[0].raw
            mutants.append(
                Mutant(
                    mutator=codec_mutator.name,
                    scenario=scenario,
                    layer=CODEC,
                    expected="codec",
                    description=codec_mutator.description,
                    raw=codec_mutator.apply(raw),
                )
            )
    return mutants


def detect(mutant: Mutant) -> Detection:
    """Run the project's own checks against one mutant and report what caught it."""
    if mutant.layer == CODEC:
        try:
            unpack(mutant.raw)
        except CodecError as exc:
            return Detection(True, ["codec"], f"codec rejected it: {exc}")
        return Detection(False, [], "the codec parsed it without complaint")

    assert mutant.flow is not None
    caught: list[str] = []
    notes: list[str] = []
    for index, step in enumerate(mutant.flow.steps, start=1):
        problems = check_values(step.message.fields)
        if problems:
            caught.append("codec")
            notes.append(f"step {index}: {problems[0]}")
            continue
        try:
            pack(step.message)
        except CodecError as exc:
            caught.append("codec")
            notes.append(f"step {index}: {exc}")
    findings = lifecycle.check(mutant.flow)
    for finding in findings:
        caught.append(finding.rule)
        notes.append(f"{finding.rule} {finding.detail}")
    if not caught:
        return Detection(False, [], "every check passed on a flow that is broken")
    return Detection(True, sorted(set(caught)), "; ".join(notes[:3]))


def fuzz(trials: int = 2000, seed: int = 7, scenario: str = "atm_withdrawal_approved") -> dict:
    """Corrupt one random character of a valid message, ``trials`` times, and count rejects.

    The catalogue above is a list of faults chosen because they are known to happen,
    which makes its kill rate a statement about coverage of *known* faults and nothing
    more. This is the complement: a fault family nobody curated. It does not reach 100%
    and it should not - a random character landing in a free-text field such as the card
    acceptor name produces a message that is unusual but entirely legal, and a parser
    that rejected it would be wrong.

    The headline rejection rate is the least useful figure it produces, because the
    trials are two different populations mixed together. Swapping one digit of an amount
    for another digit yields a different but perfectly valid message; putting a letter
    there does not. So the trials are split:

    * **class-violating** - the new character is illegal for the field it landed in.
      Every one of these must be rejected; anything else is a hole in the parser.
    * **class-legal** - the new character is allowed there. Most of these should be
      accepted, and the ones that are still rejected are rejected by the framing, not
      the character: a legal digit inside a length prefix or a bitmap relocates every
      field after it.
    """
    import random

    from . import fields as F
    from .codec import permits

    rng = random.Random(seed)
    raw = build(scenario, Context()).steps[0].raw
    spans = field_offsets(raw)
    owner: dict[int, int] = {}
    for de, (offset, length) in spans.items():
        for position in range(offset, offset + length):
            owner[position] = de

    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ "
    groups = {
        "class_violating": {"trials": 0, "rejected": 0},
        "class_legal": {"trials": 0, "rejected": 0},
        "framing": {"trials": 0, "rejected": 0},
    }
    accepted_by_field: dict[str, int] = {}
    leaks: list[dict[str, object]] = []
    attempted = 0
    while attempted < trials:
        position = rng.randrange(len(raw))
        replacement = rng.choice(alphabet)
        if raw[position] == replacement:
            continue
        attempted += 1
        candidate = raw[:position] + replacement + raw[position + 1 :]
        de = owner.get(position)
        if de is None:
            group, where = "framing", "framing (MTI, bitmap or a length prefix)"
        else:
            spec = F.get(de)
            legal = permits(spec.cls, replacement)
            group = "class_legal" if legal else "class_violating"
            where = f"DE {de} ({spec.name}), class {spec.cls}"
        groups[group]["trials"] += 1
        try:
            unpack(candidate)
        except CodecError:
            groups[group]["rejected"] += 1
            continue
        accepted_by_field[where] = accepted_by_field.get(where, 0) + 1
        if group == "class_violating" and len(leaks) < 10:
            leaks.append({"position": position, "character": replacement, "field": where})

    rejected = sum(g["rejected"] for g in groups.values())
    for group in groups.values():
        group["rejection_rate"] = (
            round(group["rejected"] / group["trials"], 4) if group["trials"] else None
        )
    return {
        "trials": attempted,
        "rejected": rejected,
        "accepted": attempted - rejected,
        "rejection_rate": round(rejected / attempted, 4) if attempted else 0.0,
        "groups": groups,
        "illegal_characters_accepted": leaks,
        "accepted_by_field": dict(sorted(accepted_by_field.items(), key=lambda kv: -kv[1])),
    }


@dataclass
class Row:
    mutator: str
    scenario: str
    layer: str
    expected: str
    killed: bool
    by_intended: bool
    caught_by: list[str]
    detail: str


def run() -> dict[str, object]:
    """The whole mutation report, ready to print or serve."""
    rows: list[Row] = []
    for mutant in generate():
        detection = detect(mutant)
        rows.append(
            Row(
                mutator=mutant.mutator,
                scenario=mutant.scenario,
                layer=mutant.layer,
                expected=mutant.expected,
                killed=detection.killed,
                by_intended=mutant.expected in detection.by,
                caught_by=detection.by,
                detail=detection.summary,
            )
        )
    total = len(rows)
    killed = sum(1 for row in rows if row.killed)
    intended = sum(1 for row in rows if row.by_intended)
    rules_exercised = sorted(
        {row.expected for row in rows if row.by_intended and row.expected != "codec"}
    )
    return {
        "total": total,
        "killed": killed,
        "killed_by_intended_rule": intended,
        "survived": [row.mutator for row in rows if not row.killed],
        "rules_exercised": rules_exercised,
        "rules_declared": [rule.rule for rule in lifecycle.RULES],
        "rules_never_exercised": [
            rule.rule for rule in lifecycle.RULES if rule.rule not in rules_exercised
        ],
        "rows": [row.__dict__ for row in rows],
    }
