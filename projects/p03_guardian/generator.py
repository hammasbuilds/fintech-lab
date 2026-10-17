"""A generated test set of 200,000 labelled wallet utterances.

The shipped test set is 140 cases written by hand. That is enough to show the assistant
works on the cases somebody thought of, and it is the wrong size for the question a
product has to answer: how often is an amount wrong, over traffic nobody curated.

So this composes utterances from parts and keeps the answer from the construction rather
than from the product:

* an **amount** is chosen first and then rendered - as digits, with separators, with a
  currency word, as Urdu digits, as ``5k``, as Roman Urdu words with a scale, or with a
  fractional modifier such as ``sawa`` or ``dedh`` - so the gold value is known before
  any text exists
* a **payee** is drawn from a generated contact book of 60 names, built as a world mix
  rather than one regional pool, and seeded with deliberate collisions: two people who
  share a first name, two whose names differ by one letter, a business whose name
  contains a person's name
* an **intent** is rendered from one of several templates per intent, in Roman Urdu,
  Urdu script, English, or code-mixed
* **coercion** scripts are composed from the seven families the product checks for, and
  from combinations of two, so a message can be a lure *and* an OTP request
* **benign** traffic is generated to the same length and urgency distribution as the
  coercion traffic, which is the only way a false-block rate means anything

Spellings come from :mod:`variants`, which keeps only the spellings that appear in
37,220 real Roman Urdu sentences, with their counts. A handful of banking words - ``otp``,
``atm``, ``urgent`` - are not in those corpora, because the corpora are product reviews
and film talk rather than payment messages; they are listed separately in
:data:`CURATED_BANKING` and marked as unattested, so nobody reads the attested list as
"the words people use" when it is "the words these two corpora happened to contain".

Nothing in here imports the product. The generator cannot be wrong in the same direction
as the parser, which is the only way a test set is worth 200,000 rows.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from shared import datasets

from .variants import VARIANTS

KEY = "generated/p03_utterances"
FILENAME = "p03_utterances.jsonl"
SEED = 20261017

#: Families, and how many of each. Amounts dominate because an amount is the field a
#: mistake costs money in.
PLAN = {
    "amount": 55_000,
    "action": 60_000,
    "coercion": 45_000,
    "benign": 25_000,
    "reply": 5_000,
    #: Transfers split to stay under a wallet's daily cap. Structuring is the oldest
    #: evasion there is and the set had no account level, no balance and no sequence, so
    #: it could not contain one.
    "structuring": 5_000,
    #: Whole conversations rather than single sentences: a request, a question about the
    #: payee, an answer, a confirmation. The confirmation spine is built for exactly this
    #: and was being tested one sentence at a time.
    "thread": 5_000,
}

#: Banking words the two Roman Urdu corpora do not contain. Kept apart from the attested
#: list so the provenance claim stays true.
CURATED_BANKING = ("otp", "atm", "urgent", "cvv", "ibft", "easypaisa", "jazzcash", "raast")


def attested(meaning: str) -> tuple[str, ...]:
    return tuple(VARIANTS.get(meaning, {}))


# --------------------------------------------------------------------------- contacts

#: A world mix on purpose: a wallet's contact book is whoever the owner knows, and a
#: resolver tested only on names from one place is tested on the easy case. The
#: collisions at the end are the point of the list.
CONTACT_NAMES = (
    "Rashid Ali",
    "Rashida Bibi",
    "Imran Shah",
    "Ayesha Noor",
    "Zubair Khan",
    "Nasreen Akhtar",
    "Fatima Siddiqui",
    "Bilal Traders",
    "Mariam Yusuf",
    "Omar Haddad",
    "Leila Mansour",
    "Yara Khalil",
    "Chen Wei",
    "Li Mei",
    "Hiroshi Tanaka",
    "Yuki Sato",
    "Min-jun Park",
    "Seo-yeon Kim",
    "Priya Nair",
    "Arjun Mehta",
    "Lakshmi Iyer",
    "Dilshan Perera",
    "Nadia Rahman",
    "Tariq Aziz",
    "Grace Okafor",
    "Chidi Obi",
    "Amara Nwosu",
    "Kwame Mensah",
    "Thabo Nkosi",
    "Zanele Dlamini",
    "Elena Petrova",
    "Dmitri Volkov",
    "Katarzyna Nowak",
    "Piotr Kowalski",
    "Mateo Garcia",
    "Sofia Ramirez",
    "Lucia Fernandez",
    "Diego Morales",
    "Emma Johansson",
    "Lars Andersen",
    "Freya Nilsson",
    "Sean O'Brien",
    "Niamh Murphy",
    "James Whitfield",
    "Charlotte Hughes",
    "Marie Dubois",
    "Luc Moreau",
    "Giulia Rossi",
    "Marco Bianchi",
    "Ahmet Yilmaz",
    "Zeynep Demir",
    "Farid Hosseini",
    "Shirin Tehrani",
    "Aisha Mohamed",
    "Ibrahim Keita",
    # The collisions. These are why the resolver has anything to do.
    "Imran Qureshi",  # shares a first name with Imran Shah
    "Ayesha Siddiqa",  # shares a first name with Ayesha Noor
    "Rashid Ahmad",  # shares a first name with Rashid Ali and looks like Rashida
    "Bilal Khan",  # a person whose name is inside the business "Bilal Traders"
    "Chen Wei Ltd",  # a business whose name is a person's
)

RELATIONS = ("brother", "sister", "mother", "father", "uncle")

#: Branchless banking tiers and their caps. Reported figures for the 2016 revision of the
#: State Bank's regulations: Level 0 at Rs 25,000 a day and Rs 40,000 a month, Level 1 at
#: Rs 50,000 and Rs 80,000. A bank's own page lists a different monthly figure for Level 0,
#: so these are quoted as *reported* and dated rather than as current law.
#:
#: https://propakistani.pk/2016/07/14/sbp-revises-transactions-limits-for-branchless-banking-accounts/
WALLET_LEVELS = {
    "level_0": {"daily": 25_000, "monthly": 40_000, "yearly": 200_000},
    "level_1": {"daily": 50_000, "monthly": 80_000, "yearly": 800_000},
}

BANKS = ("Meezan", "HBL", "UBL", "Alfalah", "Askari", "Faysal", "Bank Islami", "JS Bank")


@dataclass(frozen=True)
class Contact:
    name: str
    last4: str
    bank: str


def contact_book(seed: int = SEED) -> tuple[Contact, ...]:
    rng = random.Random(seed)
    used: set[str] = set()
    book = []
    for name in CONTACT_NAMES:
        while True:
            last4 = f"{rng.randrange(10000):04d}"
            if last4 not in used:
                used.add(last4)
                break
        book.append(Contact(name=name, last4=last4, bank=rng.choice(BANKS)))
    return tuple(book)


BOOK = contact_book()
FIRST_NAMES: dict[str, list[str]] = {}
for _contact in BOOK:
    FIRST_NAMES.setdefault(_contact.name.split()[0].lower(), []).append(_contact.name)


# ---------------------------------------------------------------------------- amounts

#: Fractional modifiers, and what they **add to** the number that follows them.
#:
#: They are additive and not multiplicative, which is the single arithmetic mistake this
#: generator is most able to make: ``saadhe teen hazaar`` is three and a half thousand,
#: 3,500 - not three times one and a half. Writing it as a multiplier gives 4,500, and
#: a test set with that in it would mark the parser wrong for being right. The first
#: version of this file had exactly that bug, and ``saadhe do crore`` came out as
#: 30,000,000 instead of 25,000,000.
FRACTIONS = (
    ("sawa", 0.25),
    ("paune", -0.25),
    ("saadhe", 0.5),
    ("sadhe", 0.5),
)
#: ``dedh`` and ``dhai`` are whole words rather than modifiers: one and a half, two and
#: a half. ``sawa`` on its own means one and a quarter.
STANDALONE = (("dedh", 1.5), ("dhai", 2.5), ("sawa", 1.25))

UNIT_WORDS = {
    1: ("aik", "ek", "one"),
    2: ("do", "two"),
    3: ("teen", "three"),
    4: ("char", "chaar", "four"),
    5: ("panch", "paanch", "five"),
    6: ("che", "chay", "six"),
    7: ("saat", "seven"),
    8: ("aath", "eight"),
    9: ("nau", "nine"),
    10: ("das", "ten"),
    15: ("pandrah", "pandra", "fifteen"),
    20: ("bees", "twenty"),
    25: ("pachees", "twenty five"),
    50: ("pachaas", "fifty"),
}

#: Spellings that are attested but are not usable as a *word* scale. ``k`` appears in the
#: corpora overwhelmingly as the shortened conjunction "ke", not as a thousand, so
#: "chaar k paanch sau" is not an amount anybody would write - it was in the first
#: version of this generator and produced 177 unparseable cases that looked like parser
#: defects.
SCALE_WORDS_EXCLUDED = {"k", "no", "do"}


def _scale_words(meaning: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    words = tuple(word for word in attested(meaning) if word not in SCALE_WORDS_EXCLUDED)
    return words or fallback


SCALE_WORDS = {
    100: ("sau", "hundred"),
    1_000: _scale_words("thousand", ("hazaar", "hazar", "thousand")),
    100_000: _scale_words("lakh", ("lakh",)),
    10_000_000: _scale_words("crore", ("crore", "karor")),
}

URDU_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

CURRENCY_PREFIX = ("Rs", "Rs.", "PKR", "rupay", "rupee")


@dataclass
class Rendered:
    text: str
    value: float
    style: str


def render_amount(rng: random.Random) -> Rendered:
    """Pick an amount, then say it. The value is known before the words exist."""
    style = rng.choices(
        (
            "digits",
            "digits_grouped",
            "digits_currency",
            "digits_slash",
            "k_suffix",
            "urdu_digits",
            "word_scale",
            "word_fraction",
            "word_standalone",
            "word_two_scales",
        ),
        weights=(16, 14, 10, 5, 8, 6, 18, 10, 7, 6),
    )[0]

    if style.startswith("digits") or style == "urdu_digits":
        value = float(rng.choice((rng.randrange(50, 1000) * 10, rng.randrange(1, 500) * 1000)))
        plain = f"{int(value)}"
        if style == "digits":
            text = plain
        elif style == "digits_grouped":
            text = f"{int(value):,}"
        elif style == "digits_currency":
            text = f"{rng.choice(CURRENCY_PREFIX)} {plain}"
        elif style == "digits_slash":
            text = f"{plain}/-"
        else:
            text = plain.translate(URDU_DIGITS)
        return Rendered(text=text, value=value, style=style)

    if style == "k_suffix":
        units = rng.randrange(1, 300)
        return Rendered(text=f"{units}k", value=float(units * 1000), style=style)

    scale, scale_words = rng.choice(list(SCALE_WORDS.items()))
    scale_word = rng.choice(tuple(scale_words))

    if style == "word_scale":
        unit, words = rng.choice(list(UNIT_WORDS.items()))
        if rng.random() < 0.3:
            # A digit with a word scale, which is how most people write it.
            return Rendered(
                text=f"{unit} {scale_word}", value=float(unit * scale), style="digit_word_scale"
            )
        return Rendered(
            text=f"{rng.choice(words)} {scale_word}", value=float(unit * scale), style=style
        )

    if style == "word_fraction":
        word, offset = rng.choice(FRACTIONS)
        # paune subtracts a quarter, so a unit of one would give three quarters of a
        # scale - legal Urdu, but "paune aik lakh" is not something anybody says.
        lowest = 2 if offset < 0 else 1
        unit, words = rng.choice([(u, w) for u, w in UNIT_WORDS.items() if lowest <= u <= 9])
        return Rendered(
            text=f"{word} {rng.choice(words)} {scale_word}",
            value=float((unit + offset) * scale),
            style=style,
        )

    if style == "word_standalone":
        word, multiplier = rng.choice(STANDALONE)
        return Rendered(text=f"{word} {scale_word}", value=float(multiplier * scale), style=style)

    # two scales added: "do hazaar paanch sau"
    big_unit, big_words = rng.choice([(u, w) for u, w in UNIT_WORDS.items() if u <= 9])
    small_unit, small_words = rng.choice([(u, w) for u, w in UNIT_WORDS.items() if u <= 9])
    big_scale_word = rng.choice(tuple(SCALE_WORDS[1_000]))
    small_scale_word = rng.choice(tuple(SCALE_WORDS[100]))
    return Rendered(
        text=(
            f"{rng.choice(big_words)} {big_scale_word} {rng.choice(small_words)} {small_scale_word}"
        ),
        value=float(big_unit * 1_000 + small_unit * 100),
        style="word_two_scales",
    )


# --------------------------------------------------------------------------- the cases


@dataclass
class Case:
    """One generated utterance and everything known about it by construction."""

    id: str
    family: str
    text: str
    language: str
    gold_amount: float | None = None
    gold_intent: str | None = None
    gold_payee: str | None = None
    gold_resolution: str = "none"
    gold_coercion: tuple[str, ...] = ()
    gold_should_block: bool = False
    #: True when the scam type has no vocabulary in the product at all. These are the
    #: blind spot at scale: the four hand-written cases could not measure a rate.
    outside_the_vocabulary: bool = False
    #: Which obfuscation was applied to a security word, if any.
    obfuscation: str = ""
    #: The wallet tier this case belongs to, and the cap it is being measured against.
    wallet_level: str = ""
    daily_cap: int = 0
    #: For a thread, which turn this is and what the whole conversation is.
    turn: int = 0
    thread_id: str = ""
    recipe: dict[str, object] = field(default_factory=dict)

    def as_row(self) -> dict[str, object]:
        row = asdict(self)
        row["gold_coercion"] = list(self.gold_coercion)
        return row


SEND_TEMPLATES = (
    ("{payee} ko {amount} {send}", "roman_urdu"),
    ("{amount} {payee} ko {send} {do}", "roman_urdu"),
    ("{send} {do} {amount} {payee} ko", "roman_urdu"),
    ("{payee} ko {amount} transfer {karo}", "code_mixed"),
    ("send {amount} to {payee}", "english"),
    ("please {send} {amount} {payee} ko {quickly}", "code_mixed"),
    ("{payee} ke account me {amount} {do}", "roman_urdu"),
    ("مجھے {payee} کو {amount} بھیجنے ہیں", "urdu"),
)

BILLERS = ("K-Electric", "SSGC", "PTCL", "LESCO", "SNGPL", "Wi-Tribe")
BILL_TEMPLATES = (
    ("{biller} ka bill {amount} {do}", "roman_urdu"),
    ("pay {amount} for {biller}", "english"),
    ("{biller} bill {amount} {karo}", "code_mixed"),
)
#: Every top-up template has to name the top-up: "03xx par 500 bhej" is a transfer to a
#: number, and labelling it mobile_topup marked the assistant wrong for reading it as a
#: transfer, which is what it is. Only an explicit load or top-up word makes it one.
TOPUP_TEMPLATES = (
    ("{number} pe {amount} ka load {do}", "roman_urdu"),
    ("top up {number} with {amount}", "english"),
    ("{number} par {amount} ka easyload {karo}", "roman_urdu"),
    ("{number} pe {amount} recharge {karo}", "code_mixed"),
)
BALANCE_TEMPLATES = (
    ("mera balance {batao}", "roman_urdu"),
    ("what is my balance", "english"),
    ("balance check {karo}", "code_mixed"),
)

#: Families the product has no vocabulary for. They are generated on purpose and labelled
#: as outside it, so the blind spot is measured at scale instead of being four hand-written
#: cases. A generator cannot be asked to invent the words a lexicon is missing - it would
#: be writing the answer key - but it can carry whole scam *types* nobody has encoded.
OUTSIDE_THE_VOCABULARY = {
    "job_scam": (
        "aap ki job confirm ho gayi hai, uniform aur training ka kharcha jama karwa dein",
        "online data entry ka kaam hai, registration ke baad pehli salary mil jaye gi",
        "company ne aap ko select kar liya hai, security deposit ke baad joining letter",
    ),
    "qr_swap": (
        "ye wala qr scan karein, dukan wala qr kharab ho gaya hai",
        "meter pe laga qr purana hai, main apna bhej raha hoon",
        "scan karke bata dein kitna aya, qr apne aap amount le lega",
    ),
    "sim_swap": (
        "aap ka number band ho raha hai, naya sim lene ke liye verification karwayein",
        "franchise se bol raha hoon, sim upgrade ke liye form bhar dein",
        "aap ke number pe doosra sim issue hua hai, roknay ke liye tasdeeq karein",
    ),
    "refund_overpayment": (
        "ghalti se aap ko zyada paisay chale gaye, extra wapas kar dein",
        "double payment ho gayi hai, jo ziyada aya wo laut a dein",
        "refund process ho gaya tha, ab aap ko wapas bhejna hai",
    ),
}

COERCION_SCRIPTS = {
    # The five families the published breakdowns put first. Purchase scams are the single
    # largest category of authorised push payment fraud in the UK regulator's data, and
    # the set had none at all.
    "purchase_scam": (
        "advance payment bhej dein, phir courier kar deta hoon",
        "token amount bhej dein warna deal nikal jaye gi",
        "olx pe dekha tha na, booking amount aaj bhej dein",
        "last piece bacha hai, pehle paisay bhej dein to rakh leta hoon",
        "parcel chhurwane ke liye delivery charges bhej dein",
    ),
    "investment_scam": (
        "paisa double karwa dunga, monthly profit guaranteed",
        "forex trading account khol dete hain, guaranteed return milta hai",
        "crypto invest kar lein, 50 percent profit teen mahine me",
    ),
    "romance_scam": (
        "mujhse shadi karni hai, visa ke paisay bhej do",
        "hospital me hoon, mera phone band ho raha hai, madad karo",
        "gift bheja hai customs clearance ke paisay chahiye",
    ),
    "advance_fee": (
        "loan approve ho gaya hai, processing fee bhej dein",
        "aap ne ticket jeeta hai, clearance fee jama karwa dein",
        "warasat ka case hai, registration fee bhej dein",
    ),
    "invoice_redirect": (
        "invoice ka account badal gaya hai, naya invoice bhej raha hoon",
        "supplier ka naya account hai, is bar is account me bhej dein",
        "updated bank details attach hain, iban change ho gaya hai",
    ),
    "otp_solicitation": (
        "aap ka otp batao warna account band ho jaye ga",
        "code bhejo verification ke liye",
        "pin batao, bank se confirm karna hai",
        "one time password share {karo} {quickly}",
    ),
    "staff_impersonation": (
        "main bank se baat kar raha hoon, {quickly} {send} {do}",
        "customer care se bol raha hoon, account verify karna hai",
        "state bank ka officer hoon, paise transfer {karo}",
    ),
    "urgency": (
        "{quickly} {send} {do} warna der ho jaye gi",
        "foran transfer {karo}, waqt nahi hai",
        "{quickly} {quickly} paise {send}",
    ),
    "threat": (
        "account band ho jaye ga agar {quickly} nahi kiya",
        "fir case ho jaye ga, {send} {do}",
        "police aa jaye gi, paise {do}",
    ),
    "remote_access": (
        "anydesk download {karo} aur id batao",
        "screen share {karo}, main theek kar deta hoon",
        "team viewer install {karo}",
    ),
    "lure": (
        "aap ne lucky draw jeeta hai, fees {send} {do}",
        "prize claim karne ke liye {amount} {send}",
        "government scheme ka paisa mila hai, account batao",
    ),
    "account_redirect": (
        "mera account badal gaya hai, naye number pe {send}",
        "is dafa doosre account me {send} {do}",
        "purana account band hai, naya note {karo}",
    ),
}

#: Ordinary traffic, with the intent each script means. A script that names a relation
#: rather than a contact - "ammi ko" - is marked as naming nobody in the contact book,
#: because that is what it does: an earlier version pasted a random contact's name into
#: the gold for those rows, so the resolver was marked wrong for asking who "ammi" is.
BENIGN_SCRIPTS = (
    ("{payee} ko {amount} {send} {do} jaise har mahine", "send_money", True),
    ("bhai {amount} {send} {do} kal wapas kar dunga", "send_money", False),
    ("{payee} ko {amount} bhej dena, dawai ke liye hai", "send_money", True),
    ("salary aa gayi, {payee} ko {amount} {send}", "send_money", True),
    ("{amount} ka bill {do} is mahine ka", "bill_payment", False),
    ("{payee} ko {amount} {send} {do} - school fees", "send_money", True),
    ("ammi ko {amount} {send}, {quickly} nahi koi baat nahi", "send_money", False),
)

REPLIES = (
    ("haan", True),
    ("haan bhej do", True),
    ("yes", True),
    ("confirm", True),
    ("theek hai", True),
    ("nahi", False),
    ("no", False),
    ("cancel", False),
    ("rok do", False),
    ("mat bhejo", False),
)


def _word(rng: random.Random, meaning: str, fallback: str) -> str:
    options = attested(meaning)
    return rng.choice(options) if options else fallback


#: Imperative forms only. ``bheja``, ``bheji`` and ``bheje`` are attested spellings of
#: the *past tense* - "I sent" - and putting them in a request slot produces sentences
#: nobody would say ("please bheja 9000 Luc ko"), which then score as parser misses.
#: Attested is not the same as grammatical in this slot.
IMPERATIVE_SEND = ("bhej", "bhejo", "bhejna", "bhej do", "send")
IMPERATIVE_GIVE = ("do", "dedo", "dein", "den", "dena")
#: ``do`` is both "give" and the numeral two. Next to an amount it is genuinely
#: ambiguous - "eight lakh do" is either "give eight lakh" or "eight lakh two", and a
#: human reading it cold would have to ask. The generator therefore keeps ``do`` for
#: positions where nothing numeric touches it and uses an unambiguous form elsewhere,
#: rather than writing a gold value for a sentence that does not have one.
UNAMBIGUOUS_GIVE = ("dedo", "dein", "den", "dena")


def _slots(rng: random.Random, template: str = "") -> dict[str, str]:
    touching_an_amount = "{amount} {do}" in template or "{do} {amount}" in template
    give = UNAMBIGUOUS_GIVE if touching_an_amount else IMPERATIVE_GIVE
    send = IMPERATIVE_SEND
    if "{send} {do}" in template and touching_an_amount:
        send = tuple(word for word in IMPERATIVE_SEND if word != "bhej do")
    return {
        "send": rng.choice(send),
        "do": rng.choice(give),
        "karo": _word(rng, "do", "karo"),
        "quickly": _word(rng, "quickly", "jaldi"),
        "batao": "batao",
    }


def _payee_mention(rng: random.Random) -> tuple[str, str | None, str]:
    """A way of naming a payee, the contact it means, and what should happen.

    Returns ``(mention, contact name or None, resolution)`` where resolution is
    ``resolved`` when exactly one contact matches, ``ask`` when more than one does or
    none does, and the gold is the construction rather than the resolver's opinion.
    """
    roll = rng.random()
    if roll < 0.45:
        contact = rng.choice(BOOK)
        return contact.name, contact.name, "resolved"
    if roll < 0.70:
        first = rng.choice(sorted(FIRST_NAMES))
        matches = FIRST_NAMES[first]
        mention = first.capitalize()
        if len(matches) == 1:
            return mention, matches[0], "resolved"
        return mention, None, "ask"
    if roll < 0.82:
        relation = rng.choice(RELATIONS)
        return _word(rng, relation, relation), None, "ask"
    if roll < 0.92:
        return f"03{rng.randrange(10)}{rng.randrange(10_000_000, 99_999_999)}", None, "resolved"
    unknown = rng.choice(("Hamza", "Sadia", "Wei Lin", "Okonkwo", "Berenice"))
    return unknown, None, "ask"


#: How a scammer hides a security word once it has been blocked once. The dataset records
#: which technique was used, so a miss rate can be reported per technique rather than as
#: one number that averages "0tp" with "o t p".
OBFUSCATIONS = ("leetspeak", "dots", "spaces", "dashes", "mixed_case")
_LEET = {"o": "0", "i": "1", "e": "3", "a": "4", "s": "5", "t": "7"}
SECURITY_WORDS = ("otp", "pin", "code", "cvv", "password")

#: Arabic codepoints that arrive where an Urdu one is meant. Identical in most fonts,
#: different bytes, and a lexicon written in one never matches text written in the other.
URDU_TO_ARABIC = {"ی": "ي", "ک": "ك"}


def obfuscate(text: str, rng: random.Random) -> tuple[str, str]:
    """Hide a security word in ``text``. Returns the text and the technique used."""
    word = next((w for w in SECURITY_WORDS if w in text.lower()), "")
    if not word:
        return text, ""
    technique = rng.choice(OBFUSCATIONS)
    at = text.lower().index(word)
    original = text[at : at + len(word)]
    if technique == "leetspeak":
        hidden = "".join(_LEET.get(char.lower(), char) for char in original)
    elif technique == "dots":
        hidden = ".".join(original)
    elif technique == "spaces":
        hidden = " ".join(original)
    elif technique == "dashes":
        hidden = "-".join(original)
    else:
        hidden = "".join(
            char.upper() if index % 2 == 0 else char.lower() for index, char in enumerate(original)
        )
    return text[:at] + hidden + text[at + len(original) :], technique


def arabise(text: str) -> str:
    """Write the Urdu in a message with Arabic codepoints, as a pasted message arrives."""
    for urdu, arabic in URDU_TO_ARABIC.items():
        text = text.replace(urdu, arabic)
    return text


def _typo(text: str, rng: random.Random, protect: str = "") -> str:
    """Drop, double or swap one letter. People type on phones.

    ``protect`` is a span that must come out unchanged - the amount, and the currency
    word in front of it. A typo inside the amount turns "dhai karor" into "adhai karor"
    and "Rs 5770" into "R s5770": the sentence no longer contains the gold value, so
    scoring the parser against it measures the typo rather than the parser.
    """
    letters = [index for index, char in enumerate(text) if char.isalpha()]
    if protect:
        at = text.find(protect)
        if at >= 0:
            # Also protect the two characters in front, which is where a currency word
            # ends up.
            start, end = max(0, at - 3), at + len(protect)
            letters = [index for index in letters if not start <= index < end]
    if not letters:
        return text
    at = rng.choice(letters)
    kind = rng.choice(("drop", "double", "swap"))
    if kind == "drop":
        return text[:at] + text[at + 1 :]
    if kind == "double":
        return text[:at] + text[at] * 2 + text[at + 1 :]
    if at + 1 < len(text):
        return text[:at] + text[at + 1] + text[at] + text[at + 2 :]
    return text


def amount_case(index: int, rng: random.Random, family: str = "amount") -> Case:
    """A transfer with an amount in it.

    ``family`` is which slot of the plan asked for this case. The action family delegates
    here for its send-money cases, and without this it reported them as amount cases -
    which made the family counts in the manifest add up to the right total and describe
    the wrong thing.
    """
    amount = render_amount(rng)
    mention, payee, resolution = _payee_mention(rng)
    template, language = rng.choice(SEND_TEMPLATES)
    slots = _slots(rng, template)
    text = template.format(payee=mention, amount=amount.text, **slots)
    if rng.random() < 0.12:
        text = _typo(text, rng, protect=amount.text)
    return Case(
        id=f"{'amt' if family == 'amount' else 'act'}-{index}",
        family=family,
        text=text,
        language=language,
        gold_amount=amount.value,
        gold_intent="send_money",
        gold_payee=payee,
        gold_resolution=resolution,
        recipe={"style": amount.style, "template": template, "mention": mention},
    )


def action_case(index: int, rng: random.Random) -> Case:
    slots = _slots(rng)
    kind = rng.choices(("send", "bill", "topup", "balance"), weights=(60, 18, 14, 8))[0]
    if kind == "send":
        return amount_case(index, rng, family="action")
    if kind == "bill":
        amount = render_amount(rng)
        template, language = rng.choice(BILL_TEMPLATES)
        slots = _slots(rng, template)
        biller = rng.choice(BILLERS)
        return Case(
            id=f"act-{index}",
            family="action",
            text=template.format(biller=biller, amount=amount.text, **slots),
            language=language,
            gold_amount=amount.value,
            gold_intent="bill_payment",
            recipe={"biller": biller, "style": amount.style},
        )
    if kind == "topup":
        amount = render_amount(rng)
        template, language = rng.choice(TOPUP_TEMPLATES)
        slots = _slots(rng, template)
        number = f"03{rng.randrange(10)}{rng.randrange(10_000_000, 99_999_999)}"
        return Case(
            id=f"act-{index}",
            family="action",
            text=template.format(number=number, amount=amount.text, **slots),
            language=language,
            gold_amount=amount.value,
            gold_intent="mobile_topup",
            recipe={"number": number, "style": amount.style},
        )
    template, language = rng.choice(BALANCE_TEMPLATES)
    return Case(
        id=f"act-{index}",
        family="action",
        text=template.format(**slots),
        language=language,
        gold_intent="balance",
        recipe={"template": template},
    )


#: Scam scripts in Urdu script rather than Roman. The set had eleven of them, which is
#: not enough to measure anything - and the whole point of folding Arabic codepoints to
#: Urdu ones is that pasted messages arrive in the wrong one. These are written in
#: ordinary Urdu and half of them are rewritten with Arabic codepoints by the generator,
#: so the fix is measured on both spellings of the same sentence.
URDU_SCRIPT_SCRIPTS = {
    "otp_solicitation": (
        "اپنا کوڈ بتاؤ ورنہ اکاؤنٹ بند ہو جائے گا",
        "او ٹی پی شیئر کریں تصدیق کے لیے",
    ),
    "threat": (
        "اکاؤنٹ بند ہو جائے گا اگر ابھی نہیں کیا",
        "قانونی کارروائی ہو گی پیسے بھیج دیں",
    ),
    "urgency": (
        "ابھی بھیجو دیر ہو جائے گی",
        "فوراً کریں وقت نہیں ہے",
    ),
    "account_redirect": (
        "اس اکاؤنٹ میں بھیج دیں پرانا بند ہے",
        "میرا اکاؤنٹ بدل گیا ہے نئے نمبر پہ بھیجیں",
    ),
}


#: How often each family is drawn. Weighted towards purchase scams because that is what
#: the published breakdowns show - one summary of the UK regulator's figures puts them at
#: 68% of cases, impersonation at 10%, advance fee at 9% and investment at 6%. A test set
#: with seven families in equal proportions measures a product against a fraud mix that
#: does not exist.
FAMILY_WEIGHTS = {
    "purchase_scam": 34,
    "staff_impersonation": 9,
    "advance_fee": 8,
    "investment_scam": 7,
    "otp_solicitation": 7,
    "account_redirect": 6,
    "romance_scam": 5,
    "invoice_redirect": 5,
    "remote_access": 4,
    "threat": 4,
    "urgency": 4,
    "lure": 3,
}


def coercion_case(index: int, rng: random.Random) -> Case:
    slots = _slots(rng)
    amount = render_amount(rng)

    # One case in eight is a scam type the product has no words for at all.
    if rng.random() < 0.125:
        kind = rng.choice(sorted(OUTSIDE_THE_VOCABULARY))
        text = rng.choice(OUTSIDE_THE_VOCABULARY[kind]).format(amount=amount.text, **slots)
        return Case(
            id=f"coe-{index}",
            family="coercion",
            text=text,
            language="roman_urdu",
            gold_coercion=(),
            gold_should_block=True,
            outside_the_vocabulary=True,
            recipe={"scam_type": kind, "style": "-"},
        )

    names = sorted(FAMILY_WEIGHTS)
    weights = [FAMILY_WEIGHTS[name] for name in names]

    # One in eight is written in Urdu script, and half of those with Arabic codepoints -
    # which is how a forwarded message arrives and which the lexicon, written in Urdu,
    # never matched until the normaliser folded them.
    if rng.random() < 0.125:
        family = rng.choice(sorted(URDU_SCRIPT_SCRIPTS))
        text = rng.choice(URDU_SCRIPT_SCRIPTS[family])
        language = "urdu"
        if rng.random() < 0.5:
            text = arabise(text)
            language = "urdu_arabic_codepoints"
        return Case(
            id=f"coe-{index}",
            family="coercion",
            text=text,
            language=language,
            gold_coercion=(family,),
            gold_should_block=True,
            recipe={"families": [family], "style": "-", "script": language},
        )

    families = [rng.choices(names, weights=weights)[0]]
    if rng.random() < 0.25:
        second = rng.choices(names, weights=weights)[0]
        if second != families[0]:
            families.append(second)
    parts = []
    for family in families:
        script = rng.choice(COERCION_SCRIPTS[family])
        parts.append(script.format(amount=amount.text, **slots))
    text = ", ".join(parts)

    # Obfuscation only does anything when a security word is there to hide, and a
    # scammer who has been blocked once hides it most of the time - so the rate is high
    # among the scripts that have one rather than low across all of them. At 18% of every
    # script it produced about 500 obfuscated cases in 200,000, which is too few to
    # report a rate per technique.
    obfuscation = ""
    if rng.random() < 0.6:
        text, obfuscation = obfuscate(text, rng)
    language = "roman_urdu"
    if any(ord(char) > 0x0600 for char in text) and rng.random() < 0.5:
        text = arabise(text)
        language = "urdu_arabic_codepoints"
    if rng.random() < 0.1:
        text = _typo(text, rng, protect=amount.text)
    return Case(
        id=f"coe-{index}",
        family="coercion",
        text=text,
        language=language,
        gold_coercion=tuple(sorted(families)),
        gold_should_block=True,
        obfuscation=obfuscation,
        recipe={"families": families, "style": amount.style if "{amount}" in text else "-"},
    )


@lru_cache(maxsize=4)
def _structuring_threads(upto: int) -> tuple[tuple[int, int, int, str, int, str, int], ...]:
    """Partition the case indices into splits of two to four legs.

    Threads have different lengths, so the index cannot be divided by a constant to find
    which thread it belongs to - an earlier version did exactly that and produced
    "threads" whose legs overlapped and whose turn numbers repeated. The partition is
    computed once from the seed and every leg reads its own entry out of it.

    Each entry is ``(thread, leg, legs, wallet level, amount per leg, pattern, days
    between legs)``.
    """
    rng = random.Random(f"{SEED}-structuring")
    out: list[tuple[int, int, int, str, int, str, int]] = []
    thread = 0
    while len(out) < upto:
        level = rng.choice(sorted(WALLET_LEVELS))
        caps = WALLET_LEVELS[level]
        # Two ways to split, and the difference between them is the whole point.
        #
        #   same_day    every leg on one day, and the day's total crosses the daily cap.
        #               A daily-cap signal sees this the moment it happens.
        #   across_days one leg a day for several days, no day anywhere near the cap, and
        #               the *month* over the monthly limit. Nothing that looks at a
        #               single day can see it, which is exactly why people do it.
        pattern = "same_day" if rng.random() < 0.5 else "across_days"
        if pattern == "same_day":
            legs = rng.choice((2, 3, 4))
            total = caps["daily"] * rng.uniform(1.2, 2.5)
            per_leg = min(int(total / legs) // 500 * 500, caps["daily"] - 500)
            spacing = 0
        else:
            legs = rng.choice((5, 6, 7, 8))
            total = caps["monthly"] * rng.uniform(1.1, 1.6)
            per_leg = min(int(total / legs) // 500 * 500, caps["daily"] - 500)
            spacing = rng.choice((1, 2, 3))
        for leg in range(legs):
            out.append((thread, leg, legs, level, per_leg, pattern, spacing))
        thread += 1
    return tuple(out[:upto])


def structuring_case(index: int, rng: random.Random) -> Case:
    """One leg of a transfer split to stay under a wallet's daily cap.

    Each leg is an ordinary transfer and legal on its own, which is the whole point: the
    pattern is only visible across the sequence, and a set of single utterances cannot
    hold it. The gold says which cap is being evaded, how many legs there are and what
    they add up to.
    """
    del rng  # the thread owns the random state, not the case
    plan = _structuring_threads(max(1024, index + 1))
    thread, leg, legs, level, per_leg, pattern, spacing = plan[index]
    caps = WALLET_LEVELS[level]
    cap = caps["daily"]
    thread_rng = random.Random(f"{SEED}-structuring-thread-{thread}")
    slots = _slots(thread_rng)
    mention, payee, resolution = _payee_mention(thread_rng)
    # One verb, not two: "bhej do do" is what two slots next to each other produce.
    text = f"{mention} ko {per_leg} {slots['send']}"
    return Case(
        id=f"str-{index}",
        family="structuring",
        text=" ".join(text.split()),
        language="roman_urdu",
        gold_amount=float(per_leg),
        gold_intent="send_money",
        gold_payee=payee,
        gold_resolution=resolution,
        gold_should_block=False,
        wallet_level=level,
        daily_cap=cap,
        thread_id=f"str-{thread}",
        turn=leg + 1,
        recipe={
            "legs": legs,
            "per_leg": per_leg,
            "total": per_leg * legs,
            "pattern": pattern,
            "days_between_legs": spacing,
            "daily_cap": cap,
            "monthly_cap": caps["monthly"],
            "evades": "the daily cap" if pattern == "same_day" else "the monthly cap",
            "stays_under_the_daily_cap_per_leg": per_leg < cap,
            "crosses_the_daily_cap": pattern == "same_day" and per_leg * legs > cap,
            "crosses_the_monthly_cap": per_leg * legs > caps["monthly"],
            "style": "digits",
            "mention": mention,
        },
    )


def benign_case(index: int, rng: random.Random) -> Case:
    slots = _slots(rng)
    amount = render_amount(rng)
    script, intent, names_a_payee = rng.choice(BENIGN_SCRIPTS)
    slots = _slots(rng, script)
    if names_a_payee:
        mention, payee, resolution = _payee_mention(rng)
    else:
        mention, payee, resolution = "", None, "ask" if intent == "send_money" else "none"
    text = script.format(payee=mention, amount=amount.text, **slots)
    return Case(
        id=f"ben-{index}",
        family="benign",
        text=text,
        language="roman_urdu",
        gold_amount=amount.value,
        gold_intent=intent,
        gold_payee=payee,
        gold_resolution=resolution,
        gold_should_block=False,
        recipe={
            "script": script,
            "mention": mention,
            "names_a_payee": names_a_payee,
            "style": amount.style,
        },
    )


#: Whether a thread can end in a transfer at all, which is part of the gold rather than
#: something to be inferred. A conversation about a payee who is not in the contact book
#: cannot complete, and scoring it as a failure to complete would be marking the assistant
#: down for refusing to pay a stranger.
THREAD_SHAPES = ("plain", "clarify", "unknown_payee")


def thread_case(index: int, rng: random.Random) -> Case:
    """One turn of a conversation, with the thread it belongs to.

    Three shapes, because three things happen in life:

    ``plain``          a complete request, then a yes. Two turns.
    ``clarify``        a request naming someone ambiguous, the assistant asks which, an
                       answer - by ordinal, bank or account tail, which is how people
                       answer - and then a yes. Four turns.
    ``unknown_payee``  a request naming somebody who is not in the book. It **cannot**
                       complete, and the gold says so.
    """
    del rng
    # Three turns for a clarification, not four. A fourth turn was a second confirmation
    # word, which arrives *after* the transfer has already executed and is then read as a
    # fresh instruction - so the thread ended in needs_information and the scorer counted
    # a completed conversation as a miss. The step-up case needs no extra turn: the
    # scorer already treats "the layer asked for a stronger word" as handled.
    turns_for = {"plain": 2, "clarify": 3, "unknown_payee": 2}
    # The shape is fixed by the thread, not drawn per turn, so every turn of a thread
    # agrees about what conversation it is in.
    plan: list[tuple[int, int, str]] = []
    thread = 0
    while len(plan) <= index:
        shape = THREAD_SHAPES[thread % len(THREAD_SHAPES)]
        for turn in range(turns_for[shape]):
            plan.append((thread, turn, shape))
        thread += 1
    thread, turn, shape = plan[index]

    rng = random.Random(f"{SEED}-thread-{thread}")
    amount = render_amount(rng)
    slots = _slots(rng)
    affirmative = rng.random() < 0.72
    yes_word, says_yes = rng.choice([pair for pair in REPLIES if pair[1] == affirmative])

    if shape == "clarify":
        shared = sorted(name for name, holders in FIRST_NAMES.items() if len(holders) > 1)
        first = rng.choice(shared)
        holders = FIRST_NAMES[first]
        pick = rng.randrange(len(holders))
        payee = holders[pick]
        mention = first.capitalize()
        # How the answer is given: by position, by bank, or by the account's last four.
        contact = next(item for item in BOOK if item.name == payee)
        # Answering "which one?" by bank only works when that bank picks out exactly one
        # of the candidates. Both Imrans are at JS Bank, so "jo JS Bank wala hai" is not
        # an answer - and the product is right to refuse to guess. Generating it anyway
        # produced 53 conversations that counted as product misses and were mine.
        same_first = [item for item in BOOK if item.name.split()[0] == payee.split()[0]]
        banks = [item.bank for item in same_first]
        kinds = ["ordinal", "tail"]
        if banks.count(contact.bank) == 1:
            kinds.append("bank")
        answer_kind = rng.choice(kinds)
        answer = {
            "ordinal": ("pehla wala", "doosra", "teesra", "chautha")[min(pick, 3)],
            "bank": f"jo {contact.bank} wala hai",
            "tail": contact.last4,
        }[answer_kind]
        turns = [
            (f"{mention} ko {amount.text} {slots['send']}", "request"),
            (answer, "answer"),
            (yes_word, "confirmation"),
        ]
        text, role = turns[turn]
        return Case(
            id=f"thr-{index}",
            family="thread",
            text=" ".join(text.split()),
            language="roman_urdu",
            gold_amount=amount.value if role == "request" else None,
            gold_intent=(
                "send_money"
                if role == "request"
                else ("affirmative" if says_yes else "negative")
                if role == "confirmation"
                else None
            ),
            gold_payee=payee if role in {"request", "answer"} else None,
            gold_resolution="ask" if role == "request" else "resolved",
            thread_id=f"thr-{thread}",
            turn=turn + 1,
            recipe={
                "shape": shape,
                "role": role,
                "style": amount.style if role == "request" else "-",
                "mention": mention,
                "answer_kind": answer_kind if role == "answer" else "",
                "affirmative": says_yes,
                "completes": says_yes,
                "turns": len(turns),
            },
        )

    if shape == "unknown_payee":
        stranger = rng.choice(("Hamza", "Sadia", "Wei Lin", "Okonkwo", "Berenice"))
        turns = [
            (f"{stranger} ko {amount.text} {slots['send']}", "request"),
            (yes_word, "confirmation"),
        ]
        text, role = turns[turn]
        return Case(
            id=f"thr-{index}",
            family="thread",
            text=" ".join(text.split()),
            language="roman_urdu",
            gold_amount=amount.value if role == "request" else None,
            gold_intent=(
                "send_money" if role == "request" else ("affirmative" if says_yes else "negative")
            ),
            gold_payee=None,
            gold_resolution="ask",
            thread_id=f"thr-{thread}",
            turn=turn + 1,
            recipe={
                "shape": shape,
                "role": role,
                "style": amount.style if role == "request" else "-",
                "mention": stranger,
                "affirmative": says_yes,
                # It cannot complete, and that is correct: the payee is not in the book.
                "completes": False,
                "turns": len(turns),
            },
        )

    mention, payee, resolution = _payee_mention(rng)
    while payee is None:
        mention, payee, resolution = _payee_mention(rng)
    turns = [
        (f"{mention} ko {amount.text} {slots['send']}", "request"),
        (yes_word, "confirmation"),
    ]
    text, role = turns[turn]
    return Case(
        id=f"thr-{index}",
        family="thread",
        text=" ".join(text.split()),
        language="roman_urdu",
        gold_amount=amount.value if role == "request" else None,
        gold_intent=(
            "send_money" if role == "request" else ("affirmative" if says_yes else "negative")
        ),
        gold_payee=payee if role == "request" else None,
        gold_resolution=resolution if role == "request" else "resolved",
        thread_id=f"thr-{thread}",
        turn=turn + 1,
        recipe={
            "shape": shape,
            "role": role,
            "style": amount.style if role == "request" else "-",
            "mention": mention,
            "affirmative": says_yes,
            "completes": says_yes,
            "turns": len(turns),
        },
    )


def reply_case(index: int, rng: random.Random) -> Case:
    text, affirmative = rng.choice(REPLIES)
    return Case(
        id=f"rep-{index}",
        family="reply",
        text=text,
        language="roman_urdu",
        gold_intent="affirmative" if affirmative else "negative",
        recipe={"affirmative": affirmative},
    )


BUILDERS = {
    "amount": amount_case,
    "action": action_case,
    "coercion": coercion_case,
    "benign": benign_case,
    "reply": reply_case,
    "structuring": structuring_case,
    "thread": thread_case,
}


def cases(plan: dict[str, int] | None = None, seed: int = SEED):
    """Generate every case in ``plan``, family by family, reproducibly."""
    plan = plan or PLAN
    for family, count in plan.items():
        rng = random.Random(f"{seed}-{family}")
        builder = BUILDERS[family]
        for index in range(count):
            yield builder(index, rng)


def path():
    return datasets.generated(FILENAME)


def build(plan: dict[str, int] | None = None, seed: int = SEED) -> dict[str, object]:
    plan = plan or PLAN
    per_family: dict[str, int] = {}
    per_style: dict[str, int] = {}
    per_language: dict[str, int] = {}
    per_obfuscation: dict[str, int] = {}
    outside = 0

    def rows():
        nonlocal outside
        for case in cases(plan, seed):
            per_family[case.family] = per_family.get(case.family, 0) + 1
            per_language[case.language] = per_language.get(case.language, 0) + 1
            style = str(case.recipe.get("style", "-"))
            per_style[style] = per_style.get(style, 0) + 1
            if case.obfuscation:
                per_obfuscation[case.obfuscation] = per_obfuscation.get(case.obfuscation, 0) + 1
            outside += int(case.outside_the_vocabulary)
            yield case.as_row()

    written = datasets.write_jsonl(path(), rows())
    return {
        "path": str(path()),
        "cases": written,
        "by_family": dict(sorted(per_family.items())),
        "by_language": dict(sorted(per_language.items())),
        "by_amount_style": dict(sorted(per_style.items())),
        "by_obfuscation": dict(sorted(per_obfuscation.items())),
        "outside_the_vocabulary": outside,
        "contacts": len(BOOK),
        "wallet_levels": WALLET_LEVELS,
        "seed": seed,
    }


def read(limit: int | None = None):
    return datasets.read_jsonl(datasets.require(path(), "make data"), limit=limit)


if __name__ == "__main__":
    import json

    print(json.dumps(build(), indent=2, ensure_ascii=False))
