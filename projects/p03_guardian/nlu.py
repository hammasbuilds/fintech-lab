"""Turn an utterance into one concrete action, or say why it cannot.

The design rule throughout: **refuse to guess.** Where an utterance is ambiguous the
parser returns the ambiguity rather than picking the likelier reading. "Rashid" matching
both *Rashid Ali* and *Rashida Bibi* is not a 60/40 call to be resolved quietly; it is a
question that has to be asked, because the cost of being wrong is someone else's money.

Self-correction is handled explicitly. People change their minds mid-sentence -
"nahi nahi, pandrah hazaar" - and a parser that takes the first number it sees gets that
exactly backwards. When a correction marker appears and more than one amount is present,
the amount after the last marker wins.

Known limitation, stated rather than hidden: a bare negation ("mujhe nahi bhejna",
I don't want to send) is not recognised as a refusal here. Nothing is lost by that,
because nothing executes without an explicit confirmation - the user simply does not
give one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import numerals

SEND = "send_money"
BALANCE = "balance"
BILL = "bill_payment"
TOPUP = "mobile_topup"
WITHDRAW = "cash_withdrawal"
STATEMENT = "mini_statement"
BLOCK_CARD = "block_card"
UNKNOWN = "unknown"

#: Intent keywords. Longer, more specific phrases are matched first, so "load karo"
#: reaches mobile top-up before "karo" reaches anything else.
LEXICON: dict[str, tuple[str, ...]] = {
    TOPUP: (
        "easyload",
        "easy load",
        "load karwa",
        "load karo",
        "load kar",
        # "250 ka load do" and "ka load dedo" are how this is actually asked for, and
        # none of the entries above match them: every one needs the verb "kar". Found by
        # scoring the generated set, where 14% of top-up requests read as no intent.
        "ka load",
        "load do",
        "load de",
        "balance load",
        "recharge",
        "top up",
        "topup",
        "card load",
        "لوڈ",
    ),
    BILL: (
        "bill",
        "bijli",
        "gas ka",
        "sui gas",
        "k-electric",
        "kelectric",
        "lesco",
        "wapda",
        "sngpl",
        "ssgc",
        "ptcl",
        "bill jama",
        "bill pay",
        "بل",
    ),
    STATEMENT: (
        "mini statement",
        "statement",
        "transactions dikha",
        "history",
        "guzishta",
        "سٹیٹمنٹ",
    ),
    BALANCE: (
        "balance kitna",
        "balance batao",
        "balance check",
        "kitne paise",
        "kitna paisa",
        # "mera account me kitna hai" names no balance word at all.
        "account me kitna",
        "account mein kitna",
        "khate me kitna",
        "balance",
        "بیلنس",
    ),
    WITHDRAW: (
        "cash nikal",
        "paise nikal",
        "nikalna",
        "withdraw",
        "withdrawal",
        "cash chahiye",
        "نکال",
    ),
    BLOCK_CARD: (
        "card band",
        "card block",
        "block kar",
        "card khoya",
        "card gum",
        "کارڈ بند",
    ),
    SEND: (
        "transfer kar",
        "paise bhej",
        "paisay bhej",
        # The bare stem, which covers bhej, bhejo, bhej do, bhej dedo, bhej dein,
        # bhejna and bheje in one entry. It was missing, and a corpus of 200,000
        # utterances put 429 plain "Omar ko 4 hzar bhej" sentences in front of this
        # function that it read as no intent at all - the single commonest way of
        # saying it. The cost is that the past tense ("kal 5000 bheja tha") now reads
        # as a request; the confirmation spine turns that into a question rather than a
        # transfer, which is the right way round for this trade.
        "bhej",
        "send money",
        "send",
        "transfer",
        "raast",
        "بھیج",
    ),
}

#: The order intents are tested in. A single utterance can contain several keywords -
#: "balance se 5 hazaar bhejo" has both - so the more specific action wins over a query.
PRIORITY = (BLOCK_CARD, TOPUP, BILL, STATEMENT, WITHDRAW, SEND, BALANCE)

CORRECTION_MARKERS = (
    "nahi nahi",
    "nahin nahin",
    "no no",
    "arre nahi",
    "arre nahin",
    "galat",
    "ghalat",
    "sorry",
    "maaf",
    "actually",
    "nahi,",
    "nahin,",
    "نہیں نہیں",
)

NO_AMOUNT = "no_amount"
MULTIPLE_AMOUNTS = "multiple_amounts"
NO_BENEFICIARY = "no_beneficiary"
AMBIGUOUS_BENEFICIARY = "ambiguous_beneficiary"
UNKNOWN_BENEFICIARY = "unknown_beneficiary"
UNKNOWN_INTENT = "unknown_intent"
NO_TARGET_NUMBER = "no_target_number"
NO_BILLER = "no_biller"

#: Intents that move money and therefore need the full confirmation spine.
MOVES_MONEY = {SEND, TOPUP, BILL, WITHDRAW}

BILLERS = {
    "k-electric": ("K-Electric", "electricity"),
    "kelectric": ("K-Electric", "electricity"),
    "lesco": ("LESCO", "electricity"),
    "wapda": ("WAPDA", "electricity"),
    "sngpl": ("SNGPL", "gas"),
    "ssgc": ("SSGC", "gas"),
    "ptcl": ("PTCL", "telephone"),
    "bijli": ("the electricity board", "electricity"),
    "sui gas": ("the gas utility", "gas"),
    "gas ka": ("the gas utility", "gas"),
}

_MOBILE = re.compile(r"(?<!\d)(03\d{2}[\s-]?\d{7})(?!\d)")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class Contact:
    name: str
    account_tail: str
    bank: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


#: The demo contact book. Two entries deliberately share a prefix: a mention of
#: "Rashid" is genuinely ambiguous between them, and the assistant has to say so.
CONTACTS: tuple[Contact, ...] = (
    Contact("Rashid Ali", "4417", "Meezan"),
    Contact("Rashida Bibi", "9082", "HBL"),
    Contact("Imran Shah", "3310", "UBL"),
    Contact("Ayesha Noor", "7725", "Alfalah"),
    Contact("Bilal Traders", "1194", "Meezan"),
    Contact("Zubair Khan", "6628", "Askari"),
    Contact("Nasreen Akhtar", "2051", "HBL"),
)


@dataclass
class Utterance:
    raw: str
    intent: str
    amount: numerals.Amount | None = None
    all_amounts: list[numerals.Amount] = field(default_factory=list)
    beneficiary: Contact | None = None
    beneficiary_mention: str = ""
    candidates: list[Contact] = field(default_factory=list)
    near_misses: list[Contact] = field(default_factory=list)
    target_number: str = ""
    biller: str = ""
    issues: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    corrected: bool = False

    @property
    def moves_money(self) -> bool:
        return self.intent in MOVES_MONEY

    @property
    def complete(self) -> bool:
        return not self.issues

    def action(self) -> dict[str, object] | None:
        """The action this utterance resolves to, or ``None`` while anything is missing."""
        if not self.complete or self.intent == UNKNOWN:
            return None
        out: dict[str, object] = {"intent": self.intent}
        if self.amount is not None:
            out["amount"] = self.amount.value
        if self.beneficiary is not None:
            out["beneficiary"] = self.beneficiary.name
            out["account_tail"] = self.beneficiary.account_tail
        if self.target_number:
            out["target_number"] = self.target_number
        if self.biller:
            out["biller"] = self.biller
        return out

    def as_dict(self) -> dict[str, object]:
        return {
            "raw": self.raw,
            "intent": self.intent,
            "amount": self.amount.as_dict() if self.amount else None,
            "all_amounts": [amount.as_dict() for amount in self.all_amounts],
            "beneficiary": self.beneficiary.name if self.beneficiary else None,
            "beneficiary_mention": self.beneficiary_mention,
            "candidates": [
                {"name": c.name, "account_tail": c.account_tail, "bank": c.bank}
                for c in self.candidates
            ],
            "near_misses": [
                {"name": c.name, "account_tail": c.account_tail, "bank": c.bank}
                for c in self.near_misses
            ],
            "target_number": self.target_number,
            "biller": self.biller,
            "issues": self.issues,
            "matched": self.matched,
            "corrected": self.corrected,
            "moves_money": self.moves_money,
            "complete": self.complete,
            "action": self.action(),
        }


#: Phrasings a substring cannot catch, because the words that make the intent sit on
#: either side of the amount: "Dilshan ke account me 154,000 dein". There is one entry
#: and it is deliberately narrow - it needs an explicit recipient marker (``ko`` or
#: ``account me``) *and* an unambiguous giving verb. The bare ``do`` is left out: next to
#: a number it is as likely to be the numeral two as the verb.
PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    SEND: (re.compile(r"\b(?:ko|account\s+me(?:in)?)\b[^.?!]*\b(?:dedo|dein|den|dena|deni)\b"),),
}


def detect_intent(text: str) -> tuple[str, list[str]]:
    lowered = numerals.normalise(text).lower()
    for intent in PRIORITY:
        hits = [phrase for phrase in LEXICON[intent] if phrase in lowered]
        for pattern in PATTERNS.get(intent, ()):
            found = pattern.search(lowered)
            if found:
                hits.append(found.group(0))
        if hits:
            return intent, sorted(hits, key=len, reverse=True)
    return UNKNOWN, []


def _last_correction(text: str) -> int:
    lowered = text.lower()
    positions = [lowered.rfind(marker) for marker in CORRECTION_MARKERS]
    return max(positions)


def choose_amount(
    text: str,
) -> tuple[numerals.Amount | None, list[numerals.Amount], bool, list[str]]:
    """Pick the amount the speaker settled on, and say when that was a correction."""
    amounts = numerals.find_all(text)
    issues: list[str] = []
    if not amounts:
        return None, [], False, [NO_AMOUNT]
    if len(amounts) == 1:
        return amounts[0], amounts, False, issues
    marker = _last_correction(text)
    if marker >= 0:
        after = [amount for amount in amounts if amount.start > marker]
        if len(after) == 1:
            return after[0], amounts, True, issues
    # Either nothing marks a correction, or more than one amount still follows the last
    # marker - "nahi nahi, pandrah hazaar bhejo, 5 hazaar nahi" is a sentence two
    # readings fit. Taking the last one would be picking a number the speaker did not
    # clearly settle on, so this is asked instead.
    return None, amounts, False, [MULTIPLE_AMOUNTS]


@dataclass
class Resolution:
    contact: Contact | None
    candidates: list[Contact]
    mention: str
    issues: list[str]
    #: Contacts whose names also begin with the mention. The decision is not changed by
    #: these, but the confirmation names them, so a user who said "Rashid" and meant
    #: Rashida sees the difference before anything moves.
    near_misses: list[Contact] = field(default_factory=list)


def resolve_beneficiary(text: str, contacts: tuple[Contact, ...] = CONTACTS) -> Resolution:
    """Find the contact in ``text``.

    The contact book is searched *first*, by longest name, rather than extracting a name
    from the sentence and then looking it up. Grammar-based extraction kept tripping over
    Urdu copulas - "bhejna hai Imran ko" yields the mention "hai Imran" - and a lookup
    that starts from the names cannot make that mistake.
    """
    folded = numerals.normalise(text)
    lowered = folded.lower()

    # The amount's own words are not available to be read as a name.
    #
    # "Okonkwo ko char sau send" - four hundred to somebody not in the book - resolved to
    # **Charlotte Hughes**, because step 3 below takes any word of three letters or more
    # that prefixes a contact name, and "char" prefixes "Charlotte". The confirmation then
    # read back the wrong payee, and a user who says "theek hai" to it has paid a stranger.
    # Generated multi-turn conversations found it; no hand-written case had a number word
    # that collided with a contact.
    #
    # The amount parser already reports where each amount sits, so the span it claimed is
    # withheld here - the same reserved-span idea that stopped the amount parser reading a
    # phone number as money.
    reserved = [(amount.start, amount.end) for amount in numerals.find_all(folded)]

    def claimed(start: int, end: int) -> bool:
        return any(start < stop and end > begin for begin, stop in reserved)

    # 1. A full name present in the text wins outright. A full name is specific enough
    #    that an overlap with an amount would be a coincidence worth honouring, so this
    #    step is left alone.
    full = [c for c in contacts if c.name.lower() in lowered]
    if len(full) == 1:
        return Resolution(full[0], full, full[0].name, [])
    if len(full) > 1:
        # One name inside another is not an ambiguity. "Chen Wei Ltd ko bhejo" matches the
        # company *and* the person called Chen Wei, and the longer name is what the text
        # actually says - so the specific one wins. Two unrelated names in one sentence
        # stay ambiguous, which is what the next branch is for.
        longest = max(full, key=lambda contact: len(contact.name))
        others = [contact for contact in full if contact is not longest]
        if all(contact.name.lower() in longest.name.lower() for contact in others):
            return Resolution(longest, [longest], longest.name, [])
        return Resolution(None, full, ", ".join(c.name for c in full), [AMBIGUOUS_BENEFICIARY])

    # 2. Otherwise match whole words in the text against first names.
    words = {
        match.group(0).lower(): match.group(0)
        for match in _WORD.finditer(folded)
        if not claimed(match.start(), match.end())
    }
    by_first: list[Contact] = [c for c in contacts if c.first_name.lower() in words]
    if len(by_first) == 1:
        chosen = by_first[0]
        mention = words[chosen.first_name.lower()]
        near = [
            c for c in contacts if c is not chosen and c.name.lower().startswith(mention.lower())
        ]
        return Resolution(chosen, [chosen], mention, [], near)
    if len(by_first) > 1:
        return Resolution(
            None, by_first, ", ".join(c.first_name for c in by_first), [AMBIGUOUS_BENEFICIARY]
        )

    # 3. A word that is a prefix of one or more contact names.
    for word in words.values():
        if len(word) < 3:
            continue
        starts = [c for c in contacts if c.name.lower().startswith(word.lower())]
        if len(starts) == 1:
            return Resolution(starts[0], starts, word, [])
        if len(starts) > 1:
            return Resolution(None, starts, word, [AMBIGUOUS_BENEFICIARY])

    # 4. Nothing matched. Report the grammatical mention so the question can quote it.
    mention = _beneficiary_mention(text)
    if not mention:
        return Resolution(None, [], "", [NO_BENEFICIARY])
    return Resolution(None, [], mention, [UNKNOWN_BENEFICIARY])


def _beneficiary_mention(text: str) -> str:
    """The name in the utterance: words before a Roman Urdu ``ko``, or after ``to``."""
    folded = numerals.normalise(text)
    tokens = [(match.group(0), match.start()) for match in _WORD.finditer(folded)]
    words = [token.lower() for token, _ in tokens]

    for index, word in enumerate(words):
        if word in {"ko", "کو"} and index > 0:
            taken: list[str] = []
            back = index - 1
            while back >= 0 and len(taken) < 3:
                candidate = tokens[back][0]
                if candidate.lower() in _NOT_A_NAME:
                    break
                taken.insert(0, candidate)
                back -= 1
            if taken:
                return " ".join(taken)
    for index, word in enumerate(words):
        if word == "to" and index + 1 < len(tokens):
            taken = []
            forward = index + 1
            while forward < len(tokens) and len(taken) < 3:
                candidate = tokens[forward][0]
                if candidate.lower() in _NOT_A_NAME:
                    break
                taken.append(candidate)
                forward += 1
            if taken:
                return " ".join(taken)
    return ""


#: Words that can sit next to "ko" or "to" without being part of a name.
_NOT_A_NAME = (
    set(numerals.UNITS)
    | set(numerals.SCALES)
    | set(numerals.MODIFIERS)
    | set(numerals.STANDALONE)
    | set(numerals.CURRENCY_WORDS)
    | {
        "bhej",
        "bhejo",
        "bhejna",
        "bhej do",
        "send",
        "transfer",
        "karo",
        "kar",
        "do",
        "please",
        "plz",
        "mujhe",
        "mera",
        "meri",
        "apne",
        "iss",
        "is",
        "us",
        "uss",
        "nahi",
        "nahin",
        "sorry",
        "actually",
        "galat",
        "ghalat",
        "aur",
        "and",
        "phir",
        "ab",
        "abhi",
        "foran",
        "jaldi",
        "account",
        "khata",
        "wallet",
        "number",
        "ye",
        "yeh",
        "woh",
        "wo",
        "kisi",
        "kis",
        "paise",
        "paisay",
        "rupay",
        "rupees",
        "bill",
        "load",
        "balance",
        "cash",
        "card",
    }
)


def extract_target_number(text: str) -> str:
    match = _MOBILE.search(numerals.normalise(text))
    return re.sub(r"[\s-]", "", match.group(1)) if match else ""


def extract_biller(text: str) -> str:
    lowered = numerals.normalise(text).lower()
    for key, (name, _kind) in BILLERS.items():
        if key in lowered:
            return name
    return ""


def parse(text: str, contacts: tuple[Contact, ...] = CONTACTS) -> Utterance:
    """Parse one utterance into an action, or into the questions that block it."""
    intent, matched = detect_intent(text)
    utterance = Utterance(raw=text, intent=intent, matched=matched)
    if intent == UNKNOWN:
        utterance.issues.append(UNKNOWN_INTENT)
        return utterance

    if intent in {BALANCE, STATEMENT, BLOCK_CARD}:
        return utterance  # nothing to fill in; these read or freeze, they do not pay

    amount, all_amounts, corrected, amount_issues = choose_amount(text)
    utterance.amount = amount
    utterance.all_amounts = all_amounts
    utterance.corrected = corrected
    utterance.issues.extend(amount_issues)

    if intent == SEND:
        resolution = resolve_beneficiary(text, contacts)
        utterance.beneficiary = resolution.contact
        utterance.candidates = resolution.candidates
        utterance.beneficiary_mention = resolution.mention
        utterance.near_misses = resolution.near_misses
        utterance.issues.extend(resolution.issues)
    elif intent == TOPUP:
        utterance.target_number = extract_target_number(text)
        if not utterance.target_number:
            utterance.issues.append(NO_TARGET_NUMBER)
    elif intent == BILL:
        utterance.biller = extract_biller(text)
        if not utterance.biller:
            utterance.issues.append(NO_BILLER)

    return utterance


QUESTIONS = {
    NO_AMOUNT: ("How much?", "Kitne paise?"),
    MULTIPLE_AMOUNTS: (
        "I heard more than one amount and I will not guess. Which one?",
        "Mujhe ek se zyada raqam sunai di, main andaza nahi lagaunga. Kaun si?",
    ),
    NO_BENEFICIARY: ("Who should this go to?", "Yeh kis ko bhejna hai?"),
    AMBIGUOUS_BENEFICIARY: (
        "More than one contact matches that name. Which one?",
        "Is naam se ek se zyada contact milte hain. Kaun sa?",
    ),
    UNKNOWN_BENEFICIARY: (
        "That name is not in your contacts.",
        "Yeh naam aap ke contacts mein nahi hai.",
    ),
    NO_TARGET_NUMBER: (
        "Which mobile number should be loaded?",
        "Kis mobile number par load karna hai?",
    ),
    NO_BILLER: ("Which bill?", "Kaun sa bill?"),
    UNKNOWN_INTENT: (
        "I did not understand that. You can say: send money, balance, pay a bill, load a "
        "number, or block my card.",
        "Main samajh nahi saka. Aap keh sakte hain: paise bhejo, balance, bill jama karo, "
        "load karo, ya card band karo.",
    ),
}


def question(issue: str, language: str = "en") -> str:
    english, urdu = QUESTIONS.get(issue, (issue, issue))
    return english if language == "en" else urdu
