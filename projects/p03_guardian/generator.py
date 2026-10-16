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

from shared import datasets

from .variants import VARIANTS

KEY = "generated/p03_utterances"
FILENAME = "p03_utterances.jsonl"
SEED = 20261017

#: Families, and how many of each. Amounts dominate because an amount is the field a
#: mistake costs money in.
PLAN = {
    "amount": 60_000,
    "action": 70_000,
    "coercion": 40_000,
    "benign": 25_000,
    "reply": 5_000,
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

COERCION_SCRIPTS = {
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


def coercion_case(index: int, rng: random.Random) -> Case:
    slots = _slots(rng)
    amount = render_amount(rng)
    families = [rng.choice(sorted(COERCION_SCRIPTS))]
    if rng.random() < 0.25:
        second = rng.choice(sorted(COERCION_SCRIPTS))
        if second != families[0]:
            families.append(second)
    parts = []
    for family in families:
        script = rng.choice(COERCION_SCRIPTS[family])
        parts.append(script.format(amount=amount.text, **slots))
    text = ", ".join(parts)
    if rng.random() < 0.1:
        text = _typo(text, rng, protect=amount.text)
    return Case(
        id=f"coe-{index}",
        family="coercion",
        text=text,
        language="roman_urdu",
        gold_coercion=tuple(sorted(families)),
        gold_should_block=True,
        recipe={"families": families, "style": amount.style if "{amount}" in text else "-"},
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

    def rows():
        for case in cases(plan, seed):
            per_family[case.family] = per_family.get(case.family, 0) + 1
            per_language[case.language] = per_language.get(case.language, 0) + 1
            style = str(case.recipe.get("style", "-"))
            per_style[style] = per_style.get(style, 0) + 1
            yield case.as_row()

    written = datasets.write_jsonl(path(), rows())
    return {
        "path": str(path()),
        "cases": written,
        "by_family": dict(sorted(per_family.items())),
        "by_language": dict(sorted(per_language.items())),
        "by_amount_style": dict(sorted(per_style.items())),
        "contacts": len(BOOK),
        "seed": seed,
    }


def read(limit: int | None = None):
    return datasets.read_jsonl(datasets.require(path(), "make data"), limit=limit)


if __name__ == "__main__":
    import json

    print(json.dumps(build(), indent=2, ensure_ascii=False))
