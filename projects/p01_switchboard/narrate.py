"""Turn a parsed message into the sentence an engineer would write in a ticket.

This is the part of the workbench that saves the most time: a decoded field table still
leaves you looking up what processing code ``011000`` means and whether ``51`` was the
decline you expected. The lookups live here, in one place, with the gaps admitted rather
than guessed - an unknown code is reported as unknown and never narrated as something
plausible.
"""

from __future__ import annotations

from . import fields as F
from .codec import Message, describe_mti

TRANSACTION_TYPES = {
    "00": "purchase",
    "01": "cash withdrawal",
    "02": "debit adjustment",
    "09": "purchase with cash back",
    "10": "account funding",
    "17": "cash advance",
    "20": "refund",
    "21": "deposit",
    "26": "credit adjustment",
    "28": "payment",
    "30": "available funds inquiry",
    "31": "balance inquiry",
    "38": "PIN change",
    "40": "cardholder transfer",
    "50": "bill payment",
}

ACCOUNT_TYPES = {
    "00": "unspecified account",
    "10": "savings account",
    "20": "current account",
    "30": "credit facility",
    "38": "prepaid account",
    "60": "e-money wallet",
}

RESPONSE_CODES = {
    "00": ("approved", "good"),
    "01": ("refer to card issuer", "warning"),
    "03": ("invalid merchant", "serious"),
    "04": ("pick up card", "critical"),
    "05": ("do not honour", "serious"),
    "10": ("partial approval", "warning"),
    "12": ("invalid transaction", "serious"),
    "13": ("invalid amount", "serious"),
    "14": ("invalid card number", "serious"),
    "30": ("format error", "critical"),
    "41": ("lost card, pick up", "critical"),
    "43": ("stolen card, pick up", "critical"),
    "51": ("insufficient funds", "warning"),
    "54": ("expired card", "warning"),
    "55": ("incorrect PIN", "warning"),
    "57": ("transaction not permitted to cardholder", "serious"),
    "58": ("transaction not permitted at terminal", "serious"),
    "61": ("exceeds withdrawal amount limit", "warning"),
    "65": ("exceeds withdrawal frequency limit", "warning"),
    "68": ("response received too late", "serious"),
    "75": ("PIN tries exceeded", "warning"),
    "91": ("issuer or switch inoperative", "critical"),
    "92": ("no routing available", "critical"),
    "96": ("system malfunction", "critical"),
}

PAN_ENTRY_MODES = {
    "00": "entry mode unknown",
    "01": "PAN keyed manually",
    "02": "magnetic stripe read",
    "05": "chip read",
    "07": "contactless chip read",
    "80": "chip failed, magnetic stripe fallback",
    "90": "magnetic stripe, full track read",
    "91": "contactless magnetic stripe",
}

PIN_CAPABILITY = {
    "0": "PIN capability unknown",
    "1": "terminal can accept a PIN",
    "2": "terminal cannot accept a PIN",
}

CONDITION_CODES = {
    "00": "normal presentment",
    "01": "cardholder not present",
    "02": "unattended terminal, cardholder operated",
    "03": "merchant suspicious",
    "05": "cardholder present, card not present",
    "08": "mail or telephone order",
    "59": "electronic commerce",
}

NETWORK_CODES = {
    "001": "sign-on",
    "002": "sign-off",
    "161": "key change",
    "201": "cut-off / cutover",
    "270": "status request",
    "301": "echo test",
}

#: Currency code -> (alpha code, minor-unit exponent).
CURRENCIES = {
    "356": ("INR", 2),
    "392": ("JPY", 0),
    "586": ("PKR", 2),
    "682": ("SAR", 2),
    "784": ("AED", 2),
    "826": ("GBP", 2),
    "840": ("USD", 2),
    "978": ("EUR", 2),
}


def format_amount(raw: str, currency: str | None) -> str:
    """Render a DE 4 style amount using the currency's own exponent."""
    digits = raw.strip() or "0"
    if not digits.isdigit():
        return f"{raw!r} (not a numeric amount)"
    alpha, exponent = CURRENCIES.get(currency or "", ("minor units", 2))
    value = int(digits)
    if exponent == 0:
        return f"{value:,} {alpha}"
    major, minor = divmod(value, 10**exponent)
    return f"{major:,}.{str(minor).zfill(exponent)} {alpha}"


def decode_processing_code(code: str) -> dict[str, str]:
    """Split DE 3 into its transaction type and the two account types."""
    if len(code) != 6 or not code.isdigit():
        return {"transaction": f"unreadable processing code {code!r}"}
    return {
        "transaction": TRANSACTION_TYPES.get(code[0:2], f"unknown transaction type {code[0:2]}"),
        "from_account": ACCOUNT_TYPES.get(code[2:4], f"unknown account type {code[2:4]}"),
        "to_account": ACCOUNT_TYPES.get(code[4:6], f"unknown account type {code[4:6]}"),
    }


def decode_original_data_elements(raw: str) -> dict[str, str]:
    """Split DE 90, the 42 digits a reversal uses to name the message it reverses."""
    if len(raw) != 42:
        return {"error": f"DE 90 must be 42 characters, got {len(raw)}"}
    return {
        "original_mti": raw[0:4],
        "original_stan": raw[4:10],
        "original_transmitted": raw[10:20],
        "original_acquirer": raw[20:31].lstrip("0") or "0",
        "original_forwarder": raw[31:42].lstrip("0") or "0",
    }


def mask_pan(pan: str) -> str:
    """First six and last four, the only parts that may be shown or logged."""
    digits = pan.strip()
    if len(digits) < 10:
        return "*" * len(digits)
    return f"{digits[:6]}{'*' * (len(digits) - 10)}{digits[-4:]}"


def decode_transmission(raw: str) -> str:
    """DE 7 is MMDDhhmmss - no year, which is why reversals carry it rather than infer it."""
    if len(raw) != 10 or not raw.isdigit():
        return f"unreadable transmission stamp {raw!r}"
    return f"{raw[0:2]}-{raw[2:4]} at {raw[4:6]}:{raw[6:8]}:{raw[8:10]} UTC"


def field_rows(message: Message) -> list[dict[str, str]]:
    """One row per present element: number, name, spec, value, and what it means."""
    rows: list[dict[str, str]] = []
    currency = message.get(49)
    for de in message.present:
        spec = F.get(de)
        value = message.fields[de]
        rows.append(
            {
                "de": str(de),
                "name": spec.name,
                "spec": spec.spec,
                "value": mask_pan(value) if de in {2, 35} else value,
                "meaning": _meaning(de, value, currency),
            }
        )
    return rows


def _meaning(de: int, value: str, currency: str | None) -> str:
    if de == 3:
        parts = decode_processing_code(value)
        if "from_account" not in parts:
            return parts["transaction"]
        return f"{parts['transaction']}, from {parts['from_account']} to {parts['to_account']}"
    if de in {4, 5, 6}:
        return format_amount(value, currency)
    if de == 7:
        return decode_transmission(value)
    if de == 11:
        return "trace number; the response must echo it unchanged"
    if de == 12:
        return f"local time {value[0:2]}:{value[2:4]}:{value[4:6]}" if len(value) == 6 else "?"
    if de == 13:
        return f"local date {value[0:2]}-{value[2:4]}" if len(value) == 4 else "?"
    if de == 14:
        return f"card expires {value[2:4]}/{value[0:2]}" if len(value) == 4 else "?"
    if de == 22:
        if len(value) != 3:
            return f"unreadable entry mode {value!r}"
        pan_mode = PAN_ENTRY_MODES.get(value[0:2], f"unknown entry mode {value[0:2]}")
        return f"{pan_mode}; {PIN_CAPABILITY.get(value[2], 'PIN capability unknown')}"
    if de == 25:
        return CONDITION_CODES.get(value, f"unknown condition code {value}")
    if de == 39:
        text, _ = RESPONSE_CODES.get(value, (f"unknown response code {value}", "neutral"))
        return text
    if de == 49:
        alpha, _ = CURRENCIES.get(value, ("unknown currency", 2))
        return alpha
    if de == 52:
        return "encrypted PIN block; never logged in the clear"
    if de == 70:
        return NETWORK_CODES.get(value, f"unknown network code {value}")
    if de == 90:
        parts = decode_original_data_elements(value)
        if "error" in parts:
            return parts["error"]
        return (
            f"reverses MTI {parts['original_mti']} trace {parts['original_stan']} "
            f"sent {parts['original_transmitted']}"
        )
    if de == 2:
        return "primary account number; shown masked"
    return ""


def severity(message: Message) -> str:
    """The status colour this message deserves, from its response code."""
    code = message.get(39)
    if code is None:
        return "neutral"
    _, level = RESPONSE_CODES.get(code, ("", "neutral"))
    return level


def narrate(message: Message) -> str:
    """One sentence describing what the message is."""
    mti = describe_mti(message.mti)
    # Position 4 names where the *transaction* originated, not who sent this particular
    # message, which is why a response keeps the request's origin digit.
    bits = [f"{mti['class'].capitalize()} {mti['function']}, originated at the {mti['origin']}"]
    code = message.get(3)
    if code:
        parts = decode_processing_code(code)
        if "from_account" in parts:
            bits.append(f"a {parts['transaction']} on the cardholder's {parts['from_account']}")
        else:
            bits.append(parts["transaction"])
    amount = message.get(4)
    if amount:
        bits.append(f"for {format_amount(amount, message.get(49))}")
    terminal = message.get(41)
    if terminal:
        acquirer = message.get(32)
        where = f"at terminal {terminal.strip()}"
        if acquirer:
            where += f" of acquirer {acquirer.lstrip('0') or acquirer}"
        bits.append(where)
    network = message.get(70)
    if network:
        bits.append(f"carrying network code {network} ({NETWORK_CODES.get(network, 'unknown')})")
    result = message.get(39)
    if result is not None:
        text, _ = RESPONSE_CODES.get(result, (f"unknown response code {result}", ""))
        bits.append(f"answered {result} - {text}")
    stan = message.get(11)
    if stan:
        bits.append(f"trace {stan}")
    return ", ".join(bits) + "."
