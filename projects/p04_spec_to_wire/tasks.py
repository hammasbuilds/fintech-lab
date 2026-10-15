"""The cases, and the prompts that put them to a model.

Each case names a scenario project 01 can build, so the deterministic answer is always
available to grade against. Nothing here is a judgement call: the grader reads the
project's own codec and invariant checker.

The two arms differ only in what the prompt contains:

* ``bare`` - the task and nothing else. What a model knows about ISO 8583 unaided.
* ``spec`` - the same task with the relevant data element dictionary and the rules
  pasted in. The question an engineer actually cares about is whether handing it the
  specification closes the gap, so the two arms have to be identical apart from that.
"""

from __future__ import annotations

from dataclasses import dataclass

from projects.p01_switchboard import fields as F
from projects.p01_switchboard import lifecycle, narrate, scenarios

DECODE = "decode"
FIELDS = "fields"
WIRE = "wire"
NEGATIVE = "negative"

BARE = "bare"
SPEC = "spec"

#: Scenarios used for the generation tasks, chosen to span the message classes rather
#: than to flatter: a withdrawal, a partial approval, a decline, an inquiry, a transfer,
#: a refund, a reversal and a network message.
CASE_SCENARIOS = (
    "atm_withdrawal_approved",
    "atm_partial_approval",
    "atm_insufficient_funds",
    "balance_inquiry",
    "cardholder_transfer",
    "pos_purchase_approved",
    "pos_refund",
    "network_echo_test",
)

#: Invariants the model is asked to break on purpose. Each is one a test suite would
#: want a case for, and each is checkable: the rule fires or it does not.
NEGATIVE_RULES = ("L03", "L04", "L06", "L09", "L13")


@dataclass(frozen=True)
class Case:
    key: str
    family: str
    scenario: str
    instruction: str
    #: Set for the negative family: the rule the produced traffic must break.
    rule: str = ""

    @property
    def label(self) -> str:
        return f"{self.family}:{self.key}"


def describe(scenario_key: str) -> str:
    """The English description of a scenario, taken from project 01's own catalogue."""
    scenario = scenarios.BY_KEY[scenario_key]
    context = scenarios.Context()
    flow = scenarios.build(scenario_key, context)
    request = flow.steps[0].message
    bits = [scenario.title + ". " + scenario.description]
    code = request.get(3)
    if code:
        parts = narrate.decode_processing_code(code)
        bits.append(
            f"It is a {parts['transaction']} from the cardholder's "
            f"{parts.get('from_account', 'account')}."
        )
    amount = request.get(4)
    if amount and amount.strip("0"):
        bits.append(f"The amount is {narrate.format_amount(amount, request.get(49))}.")
    elif amount:
        bits.append("The requested amount is zero.")
    bits.append(
        f"The card number is {request.get(2)}, the terminal is "
        f"{(request.get(41) or '').strip()}, the acquirer is {request.get(32)}, "
        f"the currency code is {request.get(49)}, and the trace number is {request.get(11)}."
    )
    bits.append(f"The transmission date and time is {request.get(7)} (MMDDhhmmss).")
    if request.get(102):
        bits.append(f"The account identification is {request.get(102)}.")
    if request.get(103):
        bits.append(f"The destination account identification is {request.get(103)}.")
    return " ".join(bits)


def cases() -> list[Case]:
    out: list[Case] = []
    for scenario in CASE_SCENARIOS:
        out.append(
            Case(
                key=scenario,
                family=DECODE,
                scenario=scenario,
                instruction="Say what this message is.",
            )
        )
        out.append(
            Case(
                key=scenario,
                family=FIELDS,
                scenario=scenario,
                instruction="Give the data elements for the request.",
            )
        )
        out.append(
            Case(
                key=scenario,
                family=WIRE,
                scenario=scenario,
                instruction="Give the whole request message.",
            )
        )
    for rule in NEGATIVE_RULES:
        out.append(
            Case(
                key=rule,
                family=NEGATIVE,
                scenario="atm_withdrawal_approved",
                instruction=f"Produce a two-message flow that breaks {rule}.",
                rule=rule,
            )
        )
    return out


# ------------------------------------------------------------------- the prompts

SYSTEM = (
    "You answer about ISO 8583, the card payment message format. You are precise and you "
    "do not explain unless asked. When a format is requested, reply with only that."
)


def dictionary_text(elements: tuple[int, ...]) -> str:
    rows = []
    for de in elements:
        field = F.get(de)
        kind = {
            "FIXED": "fixed",
            "LLVAR": "2-digit length prefix",
            "LLLVAR": "3-digit length prefix",
        }[field.var]
        rows.append(f"  DE {de:<3} {field.name:<42} {field.spec:<8} {kind}")
    return "\n".join(rows)


#: The elements the tasks actually need. A dictionary of all 128 would bury the relevant
#: ones and make the spec arm a test of retrieval rather than of the specification.
RELEVANT = (
    2,
    3,
    4,
    7,
    11,
    12,
    13,
    14,
    18,
    22,
    25,
    32,
    33,
    37,
    38,
    39,
    41,
    42,
    43,
    49,
    52,
    54,
    55,
    70,
    90,
    102,
    103,
)

RULES_TEXT = "\n".join(f"  {rule.rule}  {rule.title}. {rule.why}" for rule in lifecycle.RULES)

FORMAT_NOTES = """The ASCII framing is: a 4-character message type indicator, then a
16-hex-character primary bitmap (and a further 16 when bit 1 is set, which it must be if
any data element above 64 is present), then every present data element in ascending order.
Fixed-length numeric elements are right-justified and zero-filled; fixed-length text
elements are left-justified and space-filled. A variable-length element is preceded by its
length as 2 or 3 digits."""


def prompt(case: Case, arm: str) -> str:
    """The prompt for one case under one arm."""
    if case.family == DECODE:
        raw = scenarios.build(case.scenario, scenarios.Context()).steps[0].raw
        head = (
            "Here is one ISO 8583 message in ASCII framing:\n\n"
            f"{raw}\n\n"
            "Reply with a JSON object and nothing else, with these keys:\n"
            '  "mti": the 4-character message type indicator\n'
            '  "transaction": what kind of transaction it is, in two or three words\n'
            '  "amount_minor_units": the transaction amount as an integer in minor units\n'
            '  "currency": the 3-digit currency code\n'
            '  "trace": the system trace audit number\n'
        )
        if arm == SPEC:
            head += "\nThe data elements you may need:\n" + dictionary_text(RELEVANT) + "\n"
            head += "\n" + FORMAT_NOTES + "\n"
        return head

    description = describe(case.scenario)

    if case.family == FIELDS:
        head = (
            f"{description}\n\n"
            "Give the data elements of the *request* message as a JSON object and nothing "
            'else, of the form {"mti": "0200", "fields": {"2": "...", "3": "..."}}. '
            "Keys of 'fields' are data element numbers as strings; values are the element "
            "contents as strings, already padded to their fixed width where they have one. "
            "Do not include a bitmap - it is computed from which elements are present."
        )
        if arm == SPEC:
            head += "\n\nThe data elements you may need:\n" + dictionary_text(RELEVANT)
            head += "\n\nThe invariants this message will be checked against:\n" + RULES_TEXT
        return head

    if case.family == WIRE:
        head = (
            f"{description}\n\n"
            "Give the complete request message in ASCII framing, as one line, with no "
            "explanation and no code fence."
        )
        if arm == SPEC:
            head += "\n\n" + FORMAT_NOTES
            head += "\n\nThe data elements you may need:\n" + dictionary_text(RELEVANT)
        return head

    rule = lifecycle.BY_RULE[case.rule]
    head = (
        "A payment switch checks a two-message flow against this invariant:\n\n"
        f"  {rule.rule}  {rule.title}\n  {rule.why}\n\n"
        "Produce a request and a response that break it, so that a test suite would catch "
        "a switch that does not enforce it. Break that invariant and no other.\n\n"
        "Reply with a JSON object and nothing else, of the form\n"
        '{"request": {"mti": "0200", "fields": {"2": "..."}}, '
        '"response": {"mti": "0210", "fields": {"2": "..."}}}\n'
        "Values are element contents as strings, padded to their fixed width. Do not "
        "include a bitmap."
    )
    if arm == SPEC:
        head += "\n\nThe data elements you may need:\n" + dictionary_text(RELEVANT)
        head += "\n\nAll the invariants, so you can avoid breaking the others:\n" + RULES_TEXT
        head += (
            "\n\nFor reference, here is a valid flow of this kind, as JSON:\n"
            + _valid_flow_json(case.scenario)
        )
    return head


def _valid_flow_json(scenario_key: str) -> str:
    import json

    flow = scenarios.build(scenario_key, scenarios.Context())
    return json.dumps(
        {
            "request": {
                "mti": flow.steps[0].message.mti,
                "fields": {str(de): v for de, v in flow.steps[0].message.fields.items()},
            },
            "response": {
                "mti": flow.steps[1].message.mti,
                "fields": {str(de): v for de, v in flow.steps[1].message.fields.items()},
            },
        },
        indent=2,
    )
