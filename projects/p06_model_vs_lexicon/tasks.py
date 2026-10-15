"""The prompts. The cases are project 03's test set, unchanged.

Reusing the same corpus is the whole point: a model evaluated on cases written for a model
cannot be compared with rules evaluated on cases written for rules. Nothing is added or
dropped here, and the grader is project 03's own notion of a correct action.

The two arms differ only in what the prompt tells the model about the task:

* ``bare`` - the task, the contact list, and the output shape. No vocabulary.
* ``taught`` - the same, plus the conventions the rules encode: that ``hazaar`` is a
  thousand and ``lakh`` a hundred thousand, that ``sawa``/``paune``/``saadhe`` shift the
  number that follows, that ``saat`` and ``saath`` differ by a factor of ten, and that an
  ambiguous beneficiary must be asked about rather than chosen.

That second arm exists because of a result from earlier work: a zero-shot rung can score
near zero for the plain reason that the task's own vocabulary was never stated, and the
fair question is whether a model that is *told* the conventions can then apply them.
"""

from __future__ import annotations

from dataclasses import dataclass

from projects.p03_guardian import nlu, testset

AMOUNT = "amount"
ACTION = "action"
COERCION = "coercion"

BARE = "bare"
TAUGHT = "taught"

SYSTEM = (
    "You are the language layer of a banking assistant used in Pakistan. Customers write "
    "in Roman Urdu, Urdu or a mixture with English. You reply with only the JSON asked "
    "for, never an explanation."
)


@dataclass(frozen=True)
class Case:
    key: str
    family: str
    text: str
    #: The deterministic expectation, taken from project 03's test set.
    expected: object
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.family}:{self.key}"


def cases(amount_limit: int | None = 12) -> list[Case]:
    """Project 03's cases, as prompts.

    ``amount_limit`` trims the amount family only: there are 48 of them and they are the
    cheapest to run, but a local model at thirty seconds a call makes the full set an hour
    on its own. The count actually used is reported with the results.
    """
    out: list[Case] = []
    for text, expected in testset.NUMERAL_CASES[:amount_limit]:
        out.append(Case(key=text, family=AMOUNT, text=text, expected=expected))
    for text, action, issue in testset.ACTION_CASES:
        out.append(
            Case(
                key=text[:40],
                family=ACTION,
                text=text,
                expected=action if action is not None else {"ask": issue},
                note="must ask" if action is None else "",
            )
        )
    for text, verdict, _families, expect_miss in testset.COERCION_CASES:
        out.append(
            Case(
                key=text[:40],
                family=COERCION,
                text=text,
                expected=verdict,
                note="the lexicon misses this one" if expect_miss else "",
            )
        )
    return out


CONTACTS = "\n".join(
    f"  {contact.name}  ({contact.bank}, account ending {contact.account_tail})"
    for contact in nlu.CONTACTS
)

VOCABULARY = """The conventions these customers use:

  hazaar = 1,000          lakh = 100,000          crore = 10,000,000
  sau = 100

  sawa X   = X + 0.25     (sawa lakh = 125,000)
  saadhe X = X + 0.5      (saadhe teen hazaar = 3,500)
  paune X  = X - 0.25     (paune do lakh = 175,000)
  dedh     = 1.5          (dedh hazaar = 1,500)
  dhai     = 2.5          (dhai lakh = 250,000)
  aadha    = 0.5

  saat  = 7   so saat hazaar  = 7,000
  saath = 60  so saath hazaar = 60,000
  These two differ by a factor of ten and are one letter apart.

  A phrase may add two scales: do hazaar paanch sau = 2,500.
  Two amounts at the same scale are two amounts, not a sum: "5 hazaar aur 7 hazaar" is
  two separate figures.

  A correction mid-sentence replaces what came before: "5 hazaar nahi nahi, pandrah
  hazaar" means 15,000."""

SAFETY = """When more than one contact matches a name, or an amount is missing or
ambiguous, do not choose. Say what has to be asked. Guessing a beneficiary or an amount is
the worst available answer."""


def prompt(case: Case, arm: str) -> str:
    if case.family == AMOUNT:
        head = (
            f'A customer wrote: "{case.text}"\n\n'
            "What amount of money is that, in rupees? Reply with only a JSON object: "
            '{"amount": <number>} - or {"amount": null} if there is no amount in it.'
        )
        if arm == TAUGHT:
            head += "\n\n" + VOCABULARY
        return head

    if case.family == ACTION:
        head = (
            f'A customer wrote: "{case.text}"\n\n'
            "Their contacts are:\n"
            f"{CONTACTS}\n\n"
            "Decide what single action this asks for. Reply with only a JSON object:\n"
            '  {"intent": "...", "amount": <number or null>, '
            '"beneficiary": "<full contact name or null>", '
            '"target_number": "<mobile number or null>", '
            '"biller": "<biller or null>", "ask": "<what must be asked, or null>"}\n'
            "The intent is one of: send_money, balance, bill_payment, mobile_topup, "
            "cash_withdrawal, mini_statement, block_card, unknown.\n"
            'Set "ask" when the message does not determine the action, and leave the other '
            "fields null in that case."
        )
        if arm == TAUGHT:
            head += "\n\n" + VOCABULARY + "\n\n" + SAFETY
        return head

    head = (
        f'A customer received this message: "{case.text}"\n\n'
        "Is someone trying to pressure or trick them into moving money? Reply with only a "
        'JSON object: {"verdict": "block" | "warn" | "allow", "why": "<a few words>"}\n'
        '  "block" - the message shows a scam or coercion pattern; nothing should be sent.\n'
        '  "warn"  - one pressure signal, often innocent; show it but proceed.\n'
        '  "allow" - ordinary traffic.'
    )
    if arm == TAUGHT:
        head += (
            "\n\nThe patterns that matter: asking for an OTP, PIN or password; claiming to "
            "be the bank, the regulator or an agent; manufacturing urgency; threatening a "
            "consequence; asking for remote access or an app install; offering a prize, "
            "refund or reward; redirecting money to an account the customer did not name. "
            "Two or more of those together is a script, not a coincidence."
        )
    return head
