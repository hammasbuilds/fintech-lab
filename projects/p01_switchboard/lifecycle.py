"""Check a flow against the invariants a switch depends on.

Eighteen rules. They are not a style guide - each one is a thing that, when it breaks in
production, produces either a customer debited for cash they never received or a
reconciliation break someone has to unpick by hand.

Two of them exist because the obvious version of the rule is wrong:

* **L01** does not say every request has a response. An unanswered request is correct
  when the acquirer went on to reverse it, which is exactly what a timeout looks like.
  The rule is that a request is answered *or* reversed.
* **L04** does not say the response amount always equals the request amount. Partial
  approval is the one response allowed to change it, so the amount rule and the partial
  rule (L05) have to be written as a pair or one of them is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import emv
from .codec import Message, is_request, luhn_ok, response_mti
from .narrate import RESPONSE_CODES, decode_original_data_elements, mask_pan
from .scenarios import Flow

APPROVED = {"00", "10"}


@dataclass(frozen=True)
class Finding:
    rule: str
    title: str
    severity: str
    detail: str
    step: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "title": self.title,
            "severity": self.severity,
            "detail": self.detail,
            "step": self.step,
        }


@dataclass(frozen=True)
class Rule:
    rule: str
    title: str
    severity: str
    why: str


RULES: tuple[Rule, ...] = (
    Rule(
        "L01",
        "A request is answered or reversed",
        "critical",
        "An unanswered, unreversed financial request is money in an unknown state.",
    ),
    Rule(
        "L02",
        "A response matches a request trace",
        "critical",
        "A response whose DE 11 matches no request cannot be applied to anything.",
    ),
    Rule(
        "L03",
        "A response carries a response code",
        "critical",
        "Without DE 39 the acquirer cannot tell approval from decline.",
    ),
    Rule(
        "L04",
        "The response amount echoes the request",
        "critical",
        "Changing DE 4 outside partial approval silently debits a different amount.",
    ),
    Rule(
        "L05",
        "A partial approval is lower and above zero",
        "critical",
        "DE 39 10 with the full amount is an approval mislabelled, and zero is a decline.",
    ),
    Rule(
        "L06",
        "A reversal carries original data elements",
        "critical",
        "Without DE 90 there is nothing to reverse against.",
    ),
    Rule(
        "L07",
        "DE 90 names a request in this flow",
        "critical",
        "A reversal pointing at an unknown trace reverses nothing and leaves the hold.",
    ),
    Rule(
        "L08",
        "A network message carries DE 70",
        "serious",
        "Sign-on, echo and cutover are told apart only by DE 70.",
    ),
    Rule(
        "L09",
        "The response echoes the account number",
        "critical",
        "A response carrying a different PAN would post to the wrong cardholder.",
    ),
    Rule(
        "L10",
        "The response echoes the processing code",
        "serious",
        "DE 3 decides which account the amount moves from.",
    ),
    Rule(
        "L11",
        "Nothing is reversed after a decline",
        "serious",
        "A decline moved no money, so reversing it can double-credit the cardholder.",
    ),
    Rule(
        "L12",
        "Authorisation approvals carry an id, declines do not",
        "warning",
        "DE 38 is the handle used in disputes; a decline that carries one is ambiguous. "
        "Scoped to MTI classes 1 and 2: reversal and network approvals carry no id.",
    ),
    Rule(
        "L13",
        "The response echoes the currency",
        "critical",
        "An amount without its original currency is a settlement break.",
    ),
    Rule(
        "L14",
        "The response echoes terminal and acquirer",
        "serious",
        "These route the response back to the device that is waiting for it.",
    ),
    Rule(
        "L15",
        "No two requests reuse a trace number",
        "serious",
        "A reused DE 11 makes the response ambiguous and breaks reversal matching.",
    ),
    Rule(
        "L16",
        "A cash withdrawal carries a PIN block, a refund does not",
        "serious",
        "Cardholder verification is required one way and forbidden the other.",
    ),
    Rule(
        "L17",
        "An inquiry requests a zero amount",
        "warning",
        "A balance inquiry with a non-zero DE 4 can be posted as a withdrawal.",
    ),
    Rule(
        "L18",
        "A response does not precede its request",
        "critical",
        "Out-of-order traffic means the two were matched by luck, not by trace.",
    ),
    Rule(
        "L19",
        "The account number passes the Luhn check",
        "serious",
        "Every real card number does; one that fails was mistyped or invented.",
    ),
    Rule(
        "L20",
        "Chip tag 9F02 matches the transaction amount",
        "critical",
        "A mismatch debits the cardholder an amount the chip never authorised, and every "
        "field is individually well-formed, so nothing else catches it.",
    ),
    Rule(
        "L21",
        "Chip tag 5F2A matches the transaction currency",
        "serious",
        "The chip and the message must agree on what currency was approved.",
    ),
    Rule(
        "L22",
        "Chip data is well-formed and carries the mandatory tags",
        "serious",
        "An authorisation cannot be assessed without the cryptogram, its information "
        "data, the counter, the interchange profile and the issuer application data.",
    ),
)

BY_RULE: dict[str, Rule] = {r.rule: r for r in RULES}


def _finding(rule_id: str, detail: str, step: int | None = None) -> Finding:
    rule = BY_RULE[rule_id]
    return Finding(rule.rule, rule.title, rule.severity, detail, step)


def _stan(message: Message) -> str:
    return message.get(11) or ""


def check(flow: Flow) -> list[Finding]:
    """Every invariant this flow breaks, worst first."""
    steps = flow.steps
    messages = [step.message for step in steps]
    findings: list[Finding] = []

    requests: list[tuple[int, Message]] = []
    responses: list[tuple[int, Message]] = []
    for index, message in enumerate(messages):
        try:
            request_like = is_request(message.mti)
        except Exception as exc:  # a bad MTI is a codec problem, reported as L02
            findings.append(_finding("L02", f"step {index + 1}: {exc}", index + 1))
            continue
        (requests if request_like else responses).append((index, message))

    reversals = [(i, m) for i, m in requests if m.mti.startswith("04")]
    reversed_stans: set[str] = set()
    for index, reversal in reversals:
        odes = reversal.get(90)
        if odes is None:
            findings.append(
                _finding("L06", f"step {index + 1}: MTI {reversal.mti} has no DE 90", index + 1)
            )
            continue
        parts = decode_original_data_elements(odes)
        if "error" in parts:
            findings.append(_finding("L06", f"step {index + 1}: {parts['error']}", index + 1))
            continue
        reversed_stans.add(parts["original_stan"])
        targets = [m for i, m in requests if i != index and _stan(m) == parts["original_stan"]]
        if not targets:
            findings.append(
                _finding(
                    "L07",
                    f"step {index + 1}: DE 90 names trace {parts['original_stan']}, which no "
                    f"request in this flow carries",
                    index + 1,
                )
            )
        elif targets[0].mti != parts["original_mti"]:
            findings.append(
                _finding(
                    "L07",
                    f"step {index + 1}: DE 90 says the original was MTI "
                    f"{parts['original_mti']}, but trace {parts['original_stan']} was sent as "
                    f"MTI {targets[0].mti}",
                    index + 1,
                )
            )

    # -- pair requests to responses -----------------------------------------
    matched: dict[int, int] = {}  # request index -> response index
    for response_index, response in responses:
        partner = None
        for request_index, request in requests:
            try:
                expected = response_mti(request.mti)
            except Exception:
                continue
            if expected == response.mti and _stan(request) == _stan(response):
                partner = request_index
                break
        if partner is None:
            findings.append(
                _finding(
                    "L02",
                    f"step {response_index + 1}: MTI {response.mti} trace "
                    f"{_stan(response) or 'absent'} matches no request in this flow",
                    response_index + 1,
                )
            )
            continue
        if partner in matched:
            findings.append(
                _finding(
                    "L15",
                    f"step {response_index + 1}: a second response claims trace {_stan(response)}",
                    response_index + 1,
                )
            )
        matched[partner] = response_index
        if response_index < partner:
            findings.append(
                _finding(
                    "L18",
                    f"step {response_index + 1}: the response arrives before its request at "
                    f"step {partner + 1}",
                    response_index + 1,
                )
            )
        findings.extend(_check_pair(partner, requests, response_index, response))

    for request_index, request in requests:
        if request_index in matched:
            continue
        if _stan(request) in reversed_stans:
            continue
        findings.append(
            _finding(
                "L01",
                f"step {request_index + 1}: MTI {request.mti} trace "
                f"{_stan(request) or 'absent'} is neither answered nor reversed",
                request_index + 1,
            )
        )

    findings.extend(_check_requests(requests))
    findings.extend(_check_declines(requests, responses, matched, reversals))
    findings.extend(_check_every_message(messages))
    return _sorted(findings)


def _check_pair(
    request_index: int,
    requests: list[tuple[int, Message]],
    response_index: int,
    response: Message,
) -> list[Finding]:
    request = next(m for i, m in requests if i == request_index)
    step = response_index + 1
    out: list[Finding] = []
    code = response.get(39)
    if code is None:
        out.append(_finding("L03", f"step {step}: MTI {response.mti} has no DE 39", step))
    elif code not in RESPONSE_CODES:
        out.append(
            _finding("L03", f"step {step}: DE 39 {code!r} is not a known response code", step)
        )

    request_amount = request.get(4)
    response_amount = response.get(4)
    if request_amount is not None and response_amount is not None:
        if code == "10":
            try:
                approved, asked = int(response_amount), int(request_amount)
            except ValueError:
                out.append(
                    _finding("L05", f"step {step}: partial approval amounts are not numeric", step)
                )
            else:
                if approved >= asked:
                    out.append(
                        _finding(
                            "L05",
                            f"step {step}: DE 39 10 approved {approved} against a request for "
                            f"{asked}; a partial approval must be lower",
                            step,
                        )
                    )
                elif approved == 0:
                    out.append(
                        _finding(
                            "L05",
                            f"step {step}: DE 39 10 approved zero, which is a decline and "
                            f"should carry its own response code",
                            step,
                        )
                    )
        elif response_amount != request_amount:
            out.append(
                _finding(
                    "L04",
                    f"step {step}: DE 4 came back as {response_amount} against a request for "
                    f"{request_amount}, and DE 39 {code} does not permit a change",
                    step,
                )
            )

    for de, rule_id, label in (
        (2, "L09", "account number"),
        (3, "L10", "processing code"),
        (49, "L13", "currency"),
        (41, "L14", "terminal"),
        (32, "L14", "acquirer"),
    ):
        asked, answered = request.get(de), response.get(de)
        if asked is None or answered is None:
            continue
        if asked != answered:
            out.append(
                _finding(
                    rule_id,
                    f"step {step}: DE {de} ({label}) came back as {answered!r} against "
                    f"{asked!r} in the request",
                    step,
                )
            )

    # DE 38 is an authorisation handle, so it only means anything on an authorisation
    # (MTI class 1) or financial (class 2) message. A reversal or a network response
    # approves without authorising anything and correctly carries none, which is what
    # the first run of this checker over its own valid flows demonstrated.
    if code is not None and request.mti[1] in {"1", "2"}:
        auth = response.get(38)
        if code in APPROVED and auth is None:
            out.append(
                _finding("L12", f"step {step}: DE 39 {code} approved but carries no DE 38", step)
            )
        if code not in APPROVED and auth is not None:
            out.append(
                _finding(
                    "L12",
                    f"step {step}: DE 39 {code} is a decline but carries authorisation id {auth!r}",
                    step,
                )
            )
    return out


def _check_every_message(messages: list[Message]) -> list[Finding]:
    """Checks that look inside one message: the account number and the chip data.

    These run on responses as well as requests. A response echoes DE 2, and an echo of a
    PAN that fails its own check digit is still a PAN that cannot exist.
    """
    out: list[Finding] = []
    for index, message in enumerate(messages, start=1):
        pan = message.get(2)
        if pan is not None and not luhn_ok(pan):
            out.append(
                _finding(
                    "L19",
                    f"step {index}: DE 2 {mask_pan(pan)} fails the Luhn check digit",
                    index,
                )
            )

        chip = message.get(55)
        if chip is None:
            continue
        try:
            emv.parse(chip)
        except emv.EmvError as exc:
            out.append(_finding("L22", f"step {index}: DE 55 does not decode - {exc}", index))
            continue
        missing = emv.missing_tags(chip)
        # Only an authorisation or financial *request* must carry the full set; a
        # response or a reversal echoing part of the chip data is not incomplete.
        if missing and message.mti[1] in {"1", "2"} and message.mti[2] in {"0", "2"}:
            out.append(
                _finding(
                    "L22",
                    f"step {index}: DE 55 is missing mandatory tag(s) {', '.join(missing)}",
                    index,
                )
            )
        mismatch = emv.amount_mismatch(chip, message.get(4))
        if mismatch:
            out.append(_finding("L20", f"step {index}: {mismatch}", index))
        currency = emv.currency_mismatch(chip, message.get(49))
        if currency:
            out.append(_finding("L21", f"step {index}: {currency}", index))
    return out


def _check_requests(requests: list[tuple[int, Message]]) -> list[Finding]:
    out: list[Finding] = []
    seen: dict[tuple[str, str], int] = {}
    for index, message in requests:
        step = index + 1
        key = (message.mti, _stan(message))
        if _stan(message) and key in seen:
            out.append(
                _finding(
                    "L15",
                    f"step {step}: MTI {message.mti} reuses trace {_stan(message)} from step "
                    f"{seen[key] + 1}",
                    step,
                )
            )
        seen[key] = index

        if message.mti.startswith("08") and message.get(70) is None:
            out.append(_finding("L08", f"step {step}: MTI {message.mti} has no DE 70", step))

        code = message.get(3) or ""
        if code.startswith("01") and message.get(25) == "02" and message.get(52) is None:
            out.append(
                _finding(
                    "L16",
                    f"step {step}: a cash withdrawal at an unattended terminal carries no "
                    f"DE 52 PIN block",
                    step,
                )
            )
        if code.startswith("20") and message.get(52) is not None:
            out.append(_finding("L16", f"step {step}: a refund carries a DE 52 PIN block", step))
        if code.startswith(("30", "31")):
            amount = message.get(4)
            if amount is not None and amount.strip("0") != "":
                out.append(
                    _finding(
                        "L17",
                        f"step {step}: an inquiry requests DE 4 {amount}, which is not zero",
                        step,
                    )
                )
    return out


def _check_declines(
    requests: list[tuple[int, Message]],
    responses: list[tuple[int, Message]],
    matched: dict[int, int],
    reversals: list[tuple[int, Message]],
) -> list[Finding]:
    out: list[Finding] = []
    declined: dict[str, str] = {}
    for request_index, request in requests:
        response_index = matched.get(request_index)
        if response_index is None:
            continue
        response = next(m for i, m in responses if i == response_index)
        code = response.get(39)
        if code is not None and code not in APPROVED:
            declined[_stan(request)] = code
    for index, reversal in reversals:
        odes = reversal.get(90)
        if odes is None:
            continue
        parts = decode_original_data_elements(odes)
        original = parts.get("original_stan")
        if original in declined:
            out.append(
                _finding(
                    "L11",
                    f"step {index + 1}: reverses trace {original}, which was declined with "
                    f"DE 39 {declined[original]} and therefore moved no money",
                    index + 1,
                )
            )
    return out


_ORDER = {"critical": 0, "serious": 1, "warning": 2}


def _sorted(findings: list[Finding]) -> list[Finding]:
    unique = {(f.rule, f.detail): f for f in findings}
    return sorted(unique.values(), key=lambda f: (_ORDER.get(f.severity, 9), f.rule, f.step or 0))


def is_clean(flow: Flow) -> bool:
    return not check(flow)
