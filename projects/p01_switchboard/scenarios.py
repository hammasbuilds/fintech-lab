"""Build complete transaction lifecycles from a named scenario.

Fourteen scenarios, each a list of steps in the order they cross the wire. They are
built from one context object so an operator can change the card, the amount, the
currency or the terminal and get the same lifecycle with their own values in it.

Nothing is hand-written as a hex string. Every step is assembled as a ``Message``,
packed, and re-parsed by :func:`build` before the flow is returned, so a scenario that
would emit something the codec rejects fails here rather than in whatever it is
pointed at.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import emv
from .codec import Message, pack, response_mti, unpack

ACQ_TO_ISS = "acquirer -> issuer"
ISS_TO_ACQ = "issuer -> acquirer"


@dataclass
class Context:
    """Everything a scenario needs that an operator might want to change."""

    pan: str = "4111111111111111"
    expiry: str = "2809"
    amount: int = 500000  # minor units: 5,000.00 PKR
    currency: str = "586"
    stan: int = 4711
    terminal: str = "ATM00417"
    acquirer: str = "0037101"
    forwarder: str = "0037101"
    merchant_id: str = "MCC6011PK00417"
    merchant: str = "1LINK ATM GULBERG LAHORE          PK"
    mcc: str = "6011"
    transmitted: str = "1015093012"
    # DE 7 is MMDDhhmmss and carries no year, which is why a reversal has to quote it
    # rather than infer it. EMV tag 9A does carry one, so it is stated here.
    year: str = "26"
    local_date: str = "1015"
    local_time: str = "093012"
    rrn: str = "415093000417"
    account: str = "PK12ABCD0000001234"
    pin_block: str = "A1B2C3D4E5F60718"

    def next_stan(self) -> str:
        self.stan += 1
        return str(self.stan).zfill(6)


@dataclass
class Step:
    direction: str
    label: str
    message: Message
    note: str = ""
    raw: str = ""


@dataclass
class Flow:
    key: str
    title: str
    description: str
    steps: list[Step] = field(default_factory=list)

    @property
    def messages(self) -> list[Message]:
        return [step.message for step in self.steps]


def amount_field(minor_units: int) -> str:
    if minor_units < 0:
        raise ValueError("an ISO 8583 amount is unsigned; use a reversal, not a negative amount")
    return str(minor_units).zfill(12)


def _financial_request(
    ctx: Context,
    processing_code: str,
    *,
    amount: int | None = None,
    mti: str = "0200",
    entry_mode: str = "051",
    condition: str = "02",
    with_pin: bool = True,
    extra: dict[int, str] | None = None,
) -> Message:
    stan = ctx.next_stan()
    values: dict[int, str] = {
        2: ctx.pan,
        3: processing_code,
        4: amount_field(ctx.amount if amount is None else amount),
        7: ctx.transmitted,
        11: stan,
        12: ctx.local_time,
        13: ctx.local_date,
        14: ctx.expiry,
        18: ctx.mcc,
        22: entry_mode,
        25: condition,
        32: ctx.acquirer,
        33: ctx.forwarder,
        37: ctx.rrn,
        41: ctx.terminal,
        42: ctx.merchant_id,
        43: ctx.merchant,
        49: ctx.currency,
        102: ctx.account,
    }
    if with_pin:
        values[52] = ctx.pin_block
    # A chip or contactless-chip read carries DE 55. Magnetic stripe and fallback reads
    # do not, and inventing chip data for them would make the fallback scenario lie about
    # what the terminal actually had.
    if entry_mode[:2] in {"05", "07"}:
        values[55] = chip_data(ctx, int(values[4]), processing_code[:2])
    if extra:
        values.update(extra)
    return Message(mti=mti, fields=values)


#: EMV transaction types, which are not the same codes as DE 3's first two digits.
_CHIP_TYPES = {"00": "00", "01": "01", "09": "09", "20": "20", "31": "30", "40": "40"}


def chip_data(ctx: Context, minor_units: int, transaction_type: str) -> str:
    """Build a DE 55 that agrees with the message it is going into.

    Tag 9F02 is the amount the chip authorised and 5F2A the currency it saw. They have to
    be built from the same values as DE 4 and DE 49 or the scenario would ship the very
    defect the invariants are there to catch.
    """
    return emv.build(
        {
            "82": "3C00",
            "95": "0000000000",
            "9A": ctx.year + ctx.transmitted[:4],
            "9C": _CHIP_TYPES.get(transaction_type, "00"),
            "5F2A": ctx.currency.zfill(4),
            "9F02": amount_field(minor_units),
            "9F10": "06010A03A00000",
            "9F26": ctx.pin_block,
            "9F27": "80",
            "9F36": "0021",
            "9F37": "1A2B3C4D",
        }
    )


#: The elements an issuer response echoes back unchanged.
ECHOED = (2, 3, 4, 7, 11, 12, 13, 32, 33, 37, 41, 49, 102)


def _response(
    request: Message,
    code: str,
    *,
    amount: str | None = None,
    auth_id: str | None = "A41700",
    extra: dict[int, str] | None = None,
) -> Message:
    values = {de: request.fields[de] for de in ECHOED if de in request.fields}
    if amount is not None:
        values[4] = amount
    values[39] = code
    if code in {"00", "10"} and auth_id:
        values[38] = auth_id
    if extra:
        values.update(extra)
    return Message(mti=response_mti(request.mti), fields=values)


def _original_data_elements(request: Message, ctx: Context) -> str:
    """DE 90: the original MTI, trace, stamp, acquirer and forwarder, 42 digits."""
    return (
        request.mti
        + (request.get(11) or "000000")
        + (request.get(7) or "0000000000")
        + (request.get(32) or ctx.acquirer).zfill(11)
        + (request.get(33) or ctx.forwarder).zfill(11)
    )


def _reversal(
    ctx: Context,
    original: Message,
    *,
    replacement_amount: int | None = None,
    reason: str = "0400",
) -> Message:
    values: dict[int, str] = {
        2: original.fields[2],
        3: original.fields[3],
        4: original.fields[4],
        7: ctx.transmitted,
        11: ctx.next_stan(),
        12: ctx.local_time,
        13: ctx.local_date,
        32: original.fields[32],
        33: original.fields[33],
        37: original.fields[37],
        41: original.fields[41],
        49: original.fields[49],
        90: _original_data_elements(original, ctx),
    }
    if replacement_amount is not None:
        # DE 95: the amount that should have applied, then settlement, billing and fee
        # slots this flow does not use. 12 + 12 + 12 + 6 = 42.
        values[95] = amount_field(replacement_amount) + "0" * 12 + "0" * 12 + "0" * 6
    return Message(mti=reason, fields=values)


# ----------------------------------------------------------------- the scenarios


def _atm_withdrawal_approved(ctx: Context) -> list[Step]:
    req = _financial_request(ctx, "011000")
    return [
        Step(ACQ_TO_ISS, "withdrawal request", req, "DE 52 carries the encrypted PIN block"),
        Step(
            ISS_TO_ACQ,
            "approval",
            _response(req, "00"),
            "DE 39 00 with an authorisation id in DE 38; the amount is echoed exactly",
        ),
    ]


def _atm_partial_approval(ctx: Context) -> list[Step]:
    req = _financial_request(ctx, "011000", amount=1000000)
    return [
        Step(ACQ_TO_ISS, "withdrawal request for 10,000.00", req),
        Step(
            ISS_TO_ACQ,
            "partial approval for 6,000.00",
            _response(req, "10", amount=amount_field(600000)),
            "DE 39 10 is the one response where DE 4 may differ from the request, and it "
            "must be lower and above zero",
        ),
    ]


def _atm_insufficient_funds(ctx: Context) -> list[Step]:
    req = _financial_request(ctx, "011000")
    return [
        Step(ACQ_TO_ISS, "withdrawal request", req),
        Step(
            ISS_TO_ACQ,
            "decline 51",
            _response(req, "51", auth_id=None),
            "a decline carries no authorisation id, and nothing may be reversed afterwards",
        ),
    ]


def _atm_reversal_after_timeout(ctx: Context) -> list[Step]:
    req = _financial_request(ctx, "011000")
    rev = _reversal(ctx, req)
    return [
        Step(ACQ_TO_ISS, "withdrawal request", req),
        Step(
            ACQ_TO_ISS,
            "reversal request",
            rev,
            "no response arrived inside the timer, so the acquirer reverses; DE 90 names "
            "the message being reversed",
        ),
        Step(ISS_TO_ACQ, "reversal accepted", _response(rev, "00", auth_id=None)),
    ]


def _atm_partial_reversal(ctx: Context) -> list[Step]:
    req = _financial_request(ctx, "011000", amount=1000000)
    approval = _response(req, "00")
    rev = _reversal(ctx, req, replacement_amount=400000)
    return [
        Step(ACQ_TO_ISS, "withdrawal request for 10,000.00", req),
        Step(ISS_TO_ACQ, "approval", approval),
        Step(
            ACQ_TO_ISS,
            "partial reversal to 4,000.00",
            rev,
            "the dispenser jammed after four notes; DE 95 carries the amount that should "
            "have applied while DE 4 still carries the original",
        ),
        Step(ISS_TO_ACQ, "reversal accepted", _response(rev, "00", auth_id=None)),
    ]


def _atm_pin_tries_exceeded(ctx: Context) -> list[Step]:
    req = _financial_request(ctx, "011000")
    return [
        Step(ACQ_TO_ISS, "withdrawal request, third PIN attempt", req),
        Step(ISS_TO_ACQ, "decline 75", _response(req, "75", auth_id=None)),
    ]


def _atm_incorrect_pin_then_approved(ctx: Context) -> list[Step]:
    first = _financial_request(ctx, "011000")
    second = _financial_request(ctx, "011000")
    return [
        Step(ACQ_TO_ISS, "first attempt", first),
        Step(ISS_TO_ACQ, "decline 55", _response(first, "55", auth_id=None)),
        Step(
            ACQ_TO_ISS,
            "second attempt, new trace",
            second,
            "a retry is a new transaction with its own DE 11, not a repeat of the first",
        ),
        Step(ISS_TO_ACQ, "approval", _response(second, "00")),
    ]


def _pos_purchase_approved(ctx: Context) -> list[Step]:
    req = _financial_request(
        ctx,
        "002000",
        amount=249900,
        entry_mode="051",
        condition="00",
        extra={18: "5411", 41: "POS01993", 43: "IMTIAZ MART JOHAR TOWN LAHORE      PK"},
    )
    return [
        Step(ACQ_TO_ISS, "purchase request", req, "chip read, PIN verified at the terminal"),
        Step(ISS_TO_ACQ, "approval", _response(req, "00")),
    ]


def _pos_refund(ctx: Context) -> list[Step]:
    req = _financial_request(
        ctx,
        "202000",
        amount=249900,
        condition="00",
        with_pin=False,
        extra={18: "5411", 41: "POS01993", 43: "IMTIAZ MART JOHAR TOWN LAHORE      PK"},
    )
    return [
        Step(
            ACQ_TO_ISS,
            "refund request",
            req,
            "a refund moves value toward the cardholder, so no PIN block is present",
        ),
        Step(ISS_TO_ACQ, "approval", _response(req, "00")),
    ]


def _pos_chip_fallback_declined(ctx: Context) -> list[Step]:
    req = _financial_request(
        ctx,
        "002000",
        amount=1899900,
        entry_mode="801",
        condition="00",
        extra={18: "5732", 41: "POS02744", 43: "TECH BAZAAR HALL ROAD LAHORE      PK"},
    )
    return [
        Step(
            ACQ_TO_ISS,
            "purchase after chip failure",
            req,
            "DE 22 801 is magnetic stripe fallback after a failed chip read",
        ),
        Step(
            ISS_TO_ACQ,
            "decline 05",
            _response(req, "05", auth_id=None),
            "issuers commonly refuse fallback above a ceiling, which is a policy decline "
            "rather than a card problem",
        ),
    ]


def _balance_inquiry(ctx: Context) -> list[Step]:
    req = _financial_request(ctx, "311000", amount=0)

    def balance(amount_type: str, minor_units: int) -> str:
        # 20 characters: account type, amount type, currency, sign, amount.
        return "10" + amount_type + ctx.currency + "C" + amount_field(minor_units)

    balances = balance("01", 1842350) + balance("02", 1742350)
    return [
        Step(ACQ_TO_ISS, "balance inquiry", req, "DE 4 is zero on an inquiry"),
        Step(
            ISS_TO_ACQ,
            "balances returned",
            _response(req, "00", extra={54: balances}),
            "DE 54 carries 20 characters per balance: account type, amount type, currency, "
            "sign and amount - here ledger then available",
        ),
    ]


def _cardholder_transfer(ctx: Context) -> list[Step]:
    req = _financial_request(
        ctx,
        "402010",
        amount=750000,
        extra={103: "PK12ABCD0000009876"},
    )
    return [
        Step(
            ACQ_TO_ISS,
            "transfer request",
            req,
            "DE 3 402010 is a transfer from a savings account to a current account; DE 102 "
            "is the source and DE 103 the destination",
        ),
        Step(ISS_TO_ACQ, "approval", _response(req, "00")),
    ]


def _network_echo_test(ctx: Context) -> list[Step]:
    req = Message(
        mti="0800",
        fields={7: ctx.transmitted, 11: ctx.next_stan(), 70: "301"},
    )
    return [
        Step(ACQ_TO_ISS, "echo test", req, "DE 70 301 keeps an idle link up"),
        Step(ISS_TO_ACQ, "echo response", _response(req, "00", auth_id=None)),
    ]


def _network_sign_on(ctx: Context) -> list[Step]:
    req = Message(
        mti="0800",
        fields={7: ctx.transmitted, 11: ctx.next_stan(), 70: "001"},
    )
    return [
        Step(ACQ_TO_ISS, "sign-on", req, "DE 70 001 opens the session"),
        Step(ISS_TO_ACQ, "sign-on response", _response(req, "00", auth_id=None)),
    ]


# --------------------------------------------------------------- beyond authorisation
#
# Everything above is an authorisation, a response or a reversal: MTI classes 01xx, 02xx,
# 04xx and 08xx. A switch carries more than that, and the rest is where money goes quietly
# missing rather than loudly declining:
#
#   advice (x2x)           a transaction that already happened and the issuer has to be
#                          told about - a stand-in approval made while it was down
#   reconciliation (05xx)  totals for the window at cutover, where the acquirer's count
#                          and the issuer's are compared and the difference is the point
#   chargeback (04xx)      the dispute path, and the representment that answers it


def _stand_in_authorisation_advice(ctx: Context) -> list[Step]:
    """The issuer was down, the switch approved on its behalf, and now it has to know.

    An advice is not a request: the money has moved. The issuer cannot decline it, only
    acknowledge it, which is why the response carries an acknowledgement rather than an
    approval code.
    """
    request = _financial_request(ctx, "010000")
    advice = Message(
        mti="0120",
        fields={
            2: request.fields[2],
            3: request.fields[3],
            4: request.fields[4],
            7: ctx.transmitted,
            11: ctx.next_stan(),
            12: ctx.local_time,
            13: ctx.local_date,
            32: ctx.acquirer,
            37: ctx.rrn,
            38: "A41701",
            39: "00",
            41: ctx.terminal,
            49: ctx.currency,
            # DE 90 says which message this is about, exactly as a reversal does.
            90: _original_data_elements(request, ctx),
        },
    )
    return [
        Step(
            ACQ_TO_ISS,
            "authorisation request (issuer unreachable)",
            request,
            "The switch could not reach the issuer, so it decided on its own authority.",
        ),
        Step(
            ACQ_TO_ISS,
            "stand-in advice",
            advice,
            "DE 39 00 on an advice reports what was already approved; it does not ask.",
        ),
        Step(ISS_TO_ACQ, "advice response", _response(advice, "00", auth_id=None)),
    ]


def _end_of_day_reconciliation(ctx: Context) -> list[Step]:
    """Cutover. Both sides state their totals for the window and compare.

    DE 74 is the count of credits, DE 86 their value; DE 75 and DE 87 the debits. The
    response repeats them. A difference here is the whole purpose of the message.
    """
    request = Message(
        mti="0500",
        fields={
            7: ctx.transmitted,
            11: ctx.next_stan(),
            15: ctx.local_date,
            32: ctx.acquirer,
            33: ctx.forwarder,
            41: ctx.terminal,
            49: ctx.currency,
            # One withdrawal and one purchase in the window, which is what the other
            # scenarios in this corpus would have put through it.
            74: "000000002",
            75: "000000000",
            86: str(ctx.amount * 2).zfill(16),
            87: "0" * 16,
            # DE 60 carries the reconciliation's own reason code: 201 is cutover.
            60: "201",
        },
    )
    return [
        Step(ACQ_TO_ISS, "cutover totals", request, "DE 74/86 are the window's counts and value."),
        Step(
            ISS_TO_ACQ,
            "cutover response",
            _response(request, "00", auth_id=None),
            "A 00 here means both sides agree; 95 would mean they do not.",
        ),
    ]


def _chargeback_and_representment(ctx: Context) -> list[Step]:
    """The dispute path: the issuer takes the money back, the acquirer answers.

    A chargeback is a 0440 - the same class as a reversal and a different function - and
    it quotes the original transaction in DE 90 exactly as a reversal does. The
    representment is the acquirer putting it back with evidence.
    """
    original = _financial_request(ctx, "000000")
    approval = _response(original, "00")
    chargeback = Message(
        mti="0440",
        fields={
            2: original.fields[2],
            3: original.fields[3],
            4: original.fields[4],
            7: ctx.transmitted,
            11: ctx.next_stan(),
            32: ctx.acquirer,
            37: ctx.rrn,
            41: ctx.terminal,
            49: ctx.currency,
            # DE 25 4831 territory: the reason the cardholder gave, carried in DE 60.
            60: "4853",
            90: _original_data_elements(original, ctx),
        },
    )
    representment = Message(
        mti="0440",
        fields={
            **chargeback.fields,
            11: ctx.next_stan(),
            60: "2713",
        },
    )
    return [
        Step(ACQ_TO_ISS, "purchase", original),
        Step(ISS_TO_ACQ, "approval", approval),
        Step(
            ISS_TO_ACQ,
            "chargeback",
            chargeback,
            "DE 60 4853 is the cardholder's reason; DE 90 says which transaction.",
        ),
        Step(ACQ_TO_ISS, "chargeback response", _response(chargeback, "00", auth_id=None)),
        Step(
            ACQ_TO_ISS,
            "representment",
            representment,
            "The acquirer re-presents the same transaction under a different reason code.",
        ),
        Step(ISS_TO_ACQ, "representment response", _response(representment, "00", auth_id=None)),
    ]


def _duplicate_trace_in_one_window(ctx: Context) -> list[Step]:
    """The same trace number twice in one window, for two different transactions.

    This is valid on the wire and wrong in the ledger: DE 11 is how a reversal finds the
    transaction it reverses, so two live transactions sharing one makes the next reversal
    ambiguous. Nothing in the codec can refuse it - the invariant layer exists for cases
    like this one.
    """
    stan = ctx.next_stan()
    first = _financial_request(ctx, "010000")
    first = first.replace(de11=stan)
    second = _financial_request(ctx, "000000", amount=ctx.amount // 2)
    second = second.replace(de11=stan)
    return [
        Step(ACQ_TO_ISS, "withdrawal", first),
        Step(ISS_TO_ACQ, "approval", _response(first, "00")),
        Step(
            ACQ_TO_ISS,
            "purchase on the same trace",
            second,
            "Same DE 11, different transaction, inside one reconciliation window.",
        ),
        Step(ISS_TO_ACQ, "approval", _response(second, "00")),
    ]


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    build: Callable[[Context], list[Step]]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "atm_withdrawal_approved",
        "ATM withdrawal, approved",
        "The baseline every other ATM scenario is a deviation from.",
        _atm_withdrawal_approved,
    ),
    Scenario(
        "atm_partial_approval",
        "ATM withdrawal, partial approval",
        "The one case where the response amount is allowed to differ from the request.",
        _atm_partial_approval,
    ),
    Scenario(
        "atm_insufficient_funds",
        "ATM withdrawal, insufficient funds",
        "A decline, and therefore a flow in which a reversal would be a defect.",
        _atm_insufficient_funds,
    ),
    Scenario(
        "atm_reversal_after_timeout",
        "ATM withdrawal, reversed after a timeout",
        "No response inside the timer, so the acquirer reverses against DE 90.",
        _atm_reversal_after_timeout,
    ),
    Scenario(
        "atm_partial_reversal",
        "ATM withdrawal, partial reversal",
        "A dispense failure part way through, corrected with DE 95 rather than DE 4.",
        _atm_partial_reversal,
    ),
    Scenario(
        "atm_pin_tries_exceeded",
        "ATM withdrawal, PIN tries exceeded",
        "Response 75, which is a card-state decline rather than a funds decline.",
        _atm_pin_tries_exceeded,
    ),
    Scenario(
        "atm_incorrect_pin_then_approved",
        "ATM withdrawal, wrong PIN then approved",
        "Two requests in one session, each with its own trace number.",
        _atm_incorrect_pin_then_approved,
    ),
    Scenario(
        "pos_purchase_approved",
        "POS purchase, approved",
        "Chip read at an attended terminal.",
        _pos_purchase_approved,
    ),
    Scenario(
        "pos_refund",
        "POS refund, approved",
        "Value moving back to the cardholder, so no PIN block.",
        _pos_refund,
    ),
    Scenario(
        "pos_chip_fallback_declined",
        "POS purchase, fallback declined",
        "Magnetic stripe fallback after a chip failure, refused on policy.",
        _pos_chip_fallback_declined,
    ),
    Scenario(
        "balance_inquiry",
        "Balance inquiry",
        "A zero-amount financial request answered with DE 54.",
        _balance_inquiry,
    ),
    Scenario(
        "cardholder_transfer",
        "Cardholder transfer",
        "Savings to current, with both account identifications present.",
        _cardholder_transfer,
    ),
    Scenario(
        "network_echo_test",
        "Network echo test",
        "The keep-alive that proves the link rather than the card.",
        _network_echo_test,
    ),
    Scenario(
        "network_sign_on",
        "Network sign-on",
        "Opening the session before any financial traffic.",
        _network_sign_on,
    ),
    Scenario(
        "stand_in_authorisation_advice",
        "Stand-in authorisation, then the advice",
        "The switch approved while the issuer was down, and has to say so afterwards.",
        _stand_in_authorisation_advice,
    ),
    Scenario(
        "end_of_day_reconciliation",
        "Cutover reconciliation",
        "Both sides state the window's counts and value; a difference is the point.",
        _end_of_day_reconciliation,
    ),
    Scenario(
        "chargeback_and_representment",
        "Chargeback, then representment",
        "The dispute path: taken back with a reason, put back with another.",
        _chargeback_and_representment,
    ),
)

#: Not in SCENARIOS, on purpose. Every scenario above is valid by construction - that is
#: what lets the corpus report a finding as a false positive - and this one is a fault:
#: two live transactions sharing a trace number. It is built the same way so the
#: invariant layer can be shown catching it, and kept out of the corpus so the corpus
#: keeps meaning what it says.
KNOWN_INVALID: dict[str, Callable[[Context], list[Step]]] = {
    "duplicate_trace_in_one_window": _duplicate_trace_in_one_window,
}


def build_invalid(key: str, ctx: Context | None = None) -> Flow:
    """Build a flow that is wrong on purpose. Used by the tests, never by the corpus."""
    scenario = Scenario(
        key, "Known-invalid flow", "Built to be caught by the invariants.", KNOWN_INVALID[key]
    )
    context = ctx or Context()
    flow = Flow(key=scenario.key, title=scenario.title, description=scenario.description)
    flow.steps = scenario.build(context)
    return flow


BY_KEY: dict[str, Scenario] = {s.key: s for s in SCENARIOS}


def build(key: str, ctx: Context | None = None) -> Flow:
    """Build the named scenario, proving every step through the codec on the way out."""
    try:
        scenario = BY_KEY[key]
    except KeyError:
        raise KeyError(f"unknown scenario {key!r}; known: {', '.join(sorted(BY_KEY))}") from None
    context = ctx or Context()
    steps = scenario.build(context)
    for step in steps:
        raw = pack(step.message)
        reparsed = unpack(raw)
        if pack(reparsed) != raw:
            raise AssertionError(
                f"scenario {key!r} step {step.label!r} does not survive a round trip"
            )
        step.raw = raw
        step.message = reparsed
    return Flow(
        key=scenario.key, title=scenario.title, description=scenario.description, steps=steps
    )


def build_all(ctx: Context | None = None) -> list[Flow]:
    """Every scenario, each from its own fresh context so trace numbers do not interleave."""
    return [build(s.key, ctx or Context()) for s in SCENARIOS]
