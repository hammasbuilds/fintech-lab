"""The scored test set. Hand-written cases with the answer stated for each one.

Four groups, and one rule followed throughout: **the set contains cases the system is
expected to get wrong, and they are labelled as such.** A coercion detector evaluated
only on messages matching its own lexicon reports a perfect score and tells you nothing.
So ``COERCION_CASES`` includes real scam approaches that use none of the detector's
phrases - a relative in trouble, a soft-spoken stranger - marked ``expect_miss``. The
benchmark counts them separately and reports the miss rate as a headline, because that
number is the honest measure of what this layer does not cover.

Cases are written as plain tuples so the file reads as a corpus rather than as code.
"""

from __future__ import annotations

# ---------------------------------------------------------------- numerals
# (utterance, expected amount in rupees)
NUMERAL_CASES: tuple[tuple[str, float], ...] = (
    ("5000", 5000),
    ("15,000", 15000),
    ("Rs 7500", 7500),
    ("PKR 1200", 1200),
    ("5k", 5000),
    ("2.5k", 2500),
    ("۱۵۰۰۰", 15000),
    ("٥٠٠٠", 5000),
    ("paanch hazaar", 5000),
    ("panch hazar", 5000),
    ("panj hazaar", 5000),
    ("pandrah hazaar", 15000),
    ("pandra hazar", 15000),
    ("15 hazaar", 15000),
    ("bees hazaar", 20000),
    ("pachees hazaar", 25000),
    ("pachaas hazaar", 50000),
    ("saath hazaar", 60000),
    ("saat hazaar", 7000),
    ("nabbay hazaar", 90000),
    ("do lakh", 200000),
    ("teen lakh", 300000),
    ("aadha lakh", 50000),
    ("sawa lakh", 125000),
    ("dhai lakh", 250000),
    ("dedh lakh", 150000),
    ("dedh hazaar", 1500),
    ("dhai hazaar", 2500),
    ("saadhe teen hazaar", 3500),
    ("sarhe char hazaar", 4500),
    ("paune do lakh", 175000),
    ("paune teen hazaar", 2750),
    ("do hazaar paanch sau", 2500),
    ("char lakh pachaas hazaar", 450000),
    ("teen sau", 300),
    ("paanch sau", 500),
    ("aik hazaar", 1000),
    ("ek lakh", 100000),
    ("das hazaar rupay", 10000),
    ("پانچ ہزار", 5000),
    ("ڈیڑھ لاکھ", 150000),
    ("سوا لاکھ", 125000),
    ("بیس ہزار روپے", 20000),
    ("do karor", 20000000),
    ("solah hazaar", 16000),
    ("unnees hazaar", 19000),
    ("chaudah sau", 1400),
    ("saadhe paanch hazaar", 5500),
)

#: Text with no amount in it at all. Reading an amount out of any of these is a defect.
NO_AMOUNT_CASES: tuple[str, ...] = (
    "balance batao",
    "mera card band karo",
    "mini statement dikhao",
    "03001234567 pe load karo",
    "Imran ko bhejo",
    "statement chahiye",
)

# ---------------------------------------------------------------- actions
# (utterance, expected action or None, expected first issue or None)
ACTION_CASES: tuple[tuple[str, dict | None, str | None], ...] = (
    (
        "Rashid Ali ko paanch hazaar bhej do",
        {
            "intent": "send_money",
            "amount": 5000,
            "beneficiary": "Rashid Ali",
            "account_tail": "4417",
        },
        None,
    ),
    (
        "Imran ko 15 hazaar transfer karo",
        {
            "intent": "send_money",
            "amount": 15000,
            "beneficiary": "Imran Shah",
            "account_tail": "3310",
        },
        None,
    ),
    (
        "Ayesha Noor ko dedh hazaar bhejna hai",
        {
            "intent": "send_money",
            "amount": 1500,
            "beneficiary": "Ayesha Noor",
            "account_tail": "7725",
        },
        None,
    ),
    (
        "Bilal Traders ko sawa lakh bhejo",
        {
            "intent": "send_money",
            "amount": 125000,
            "beneficiary": "Bilal Traders",
            "account_tail": "1194",
        },
        None,
    ),
    (
        "bhejna hai Zubair ko teen hazaar",
        {
            "intent": "send_money",
            "amount": 3000,
            "beneficiary": "Zubair Khan",
            "account_tail": "6628",
        },
        None,
    ),
    (
        "send 2000 to Nasreen",
        {
            "intent": "send_money",
            "amount": 2000,
            "beneficiary": "Nasreen Akhtar",
            "account_tail": "2051",
        },
        None,
    ),
    (
        "5 hazaar nahi nahi, pandrah hazaar Imran ko bhejo",
        {
            "intent": "send_money",
            "amount": 15000,
            "beneficiary": "Imran Shah",
            "account_tail": "3310",
        },
        None,
    ),
    (
        "Ayesha ko 2 hazaar, sorry 4 hazaar bhejo",
        {
            "intent": "send_money",
            "amount": 4000,
            "beneficiary": "Ayesha Noor",
            "account_tail": "7725",
        },
        None,
    ),
    (
        "پانچ ہزار Imran کو بھیج دو",
        {
            "intent": "send_money",
            "amount": 5000,
            "beneficiary": "Imran Shah",
            "account_tail": "3310",
        },
        None,
    ),
    ("balance batao", {"intent": "balance"}, None),
    ("balance kitna hai", {"intent": "balance"}, None),
    ("بیلنس بتاؤ", {"intent": "balance"}, None),
    ("mini statement dikhao", {"intent": "mini_statement"}, None),
    ("mera card band karo", {"intent": "block_card"}, None),
    ("card khoya hai block kar do", {"intent": "block_card"}, None),
    (
        "03001234567 pe paanch sau ka load karo",
        {"intent": "mobile_topup", "amount": 500, "target_number": "03001234567"},
        None,
    ),
    (
        "0321-4455667 par 1000 easyload",
        {"intent": "mobile_topup", "amount": 1000, "target_number": "03214455667"},
        None,
    ),
    (
        "k-electric ka bill 3200 jama karo",
        {"intent": "bill_payment", "amount": 3200, "biller": "K-Electric"},
        None,
    ),
    (
        "sngpl bill do hazaar paanch sau pay karo",
        {"intent": "bill_payment", "amount": 2500, "biller": "SNGPL"},
        None,
    ),
    (
        "atm se das hazaar nikalna hai",
        {"intent": "cash_withdrawal", "amount": 10000},
        None,
    ),
    # Cases that must stop and ask rather than resolve.
    ("Zubair ko bhej do", None, "no_amount"),
    ("Imran ko bhejna hai", None, "no_amount"),
    ("Rashid ko 5 hazaar aur 7 hazaar bhejo", None, "multiple_amounts"),
    ("nahi nahi, Imran ko pandrah hazaar bhejo, 5 hazaar nahi", None, "multiple_amounts"),
    ("Kashif ko 2 hazaar bhejo", None, "unknown_beneficiary"),
    ("5 hazaar bhej do", None, "no_beneficiary"),
    ("500 ka load karo", None, "no_target_number"),
    ("bill 1200 jama karo", None, "no_biller"),
    ("kuch samajh nahi aya", None, "unknown_intent"),
    ("aaj mausam acha hai", None, "unknown_intent"),
)

#: Ambiguity that must be surfaced, not resolved. ``Rashid`` is an exact first name and
#: also the start of ``Rashida``: the expected behaviour is to act on the exact match but
#: name the near miss in the read-back.
NEAR_MISS_CASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Rashid ko 5 hazaar bhejo", "Rashid Ali", ("Rashida Bibi",)),
    ("Rashida ko 5 hazaar bhejo", "Rashida Bibi", ()),
    ("Imran ko 5 hazaar bhejo", "Imran Shah", ()),
)

# ---------------------------------------------------------------- coercion
# (message, expected verdict, families that must be found, expect_miss)
COERCION_CASES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    # Clear scripts: two or more families, must block.
    (
        "main bank se baat kar raha hoon, OTP batao foran",
        "block",
        ("staff_impersonation", "otp_solicitation", "urgency"),
        False,
    ),
    (
        "State Bank se bol raha hoon, abhi 50 hazaar mere account mein bhejo warna account band ho jayega",
        "block",
        ("staff_impersonation", "urgency", "threat", "account_redirect"),
        False,
    ),
    (
        "aap ne lucky draw mein inaam jeeta hai, processing fee abhi bhejo",
        "block",
        ("lure", "urgency"),
        False,
    ),
    (
        "AnyDesk install karo, main customer care se hoon",
        "block",
        ("remote_access", "staff_impersonation"),
        False,
    ),
    (
        "FIR ho jayegi, jaldi karo",
        "block",
        ("threat", "urgency"),
        False,
    ),
    (
        "refund mila hai, screen share karo aur pin batao",
        "block",
        ("lure", "remote_access", "otp_solicitation"),
        False,
    ),
    (
        "bank officer bol raha hoon, verification code share karein",
        "block",
        ("staff_impersonation", "otp_solicitation"),
        False,
    ),
    (
        "ehsaas program ka paisa aya hai, link par click karo",
        "block",
        ("lure", "remote_access"),
        False,
    ),
    (
        "اسٹیٹ بینک سے بات کر رہا ہوں، ابھی بھیجو",
        "block",
        ("staff_impersonation", "urgency"),
        False,
    ),
    # One family only: shown, not acted on.
    ("jaldi karo mujhe paise chahiye", "warn", ("urgency",), False),
    ("abhi bhejo please", "warn", ("urgency",), False),
    ("mujhe OTP nahi mila", "warn", ("otp_solicitation",), False),
    ("police station ja raha hoon", "warn", ("threat",), False),
    ("inaam ki scheme ke baare mein poocha tha", "warn", ("lure",), False),
    # Nothing at all: ordinary traffic.
    ("Imran ko 5 hazaar bhejo", "allow", (), False),
    ("balance batao", "allow", (), False),
    ("k-electric ka bill jama karo", "allow", (), False),
    ("ami ko mahine ka kharcha bhej raha hoon", "allow", (), False),
    ("salary aa gayi hai", "allow", (), False),
    # Real approaches that use none of the lexicon. These are expected misses and are
    # counted as such: the detector reads words, and these ones are not in it.
    (
        "beta main tumhari ammi ki saheli hoon, thori madad chahiye thi",
        "allow",
        (),
        True,
    ),
    (
        "main wohi hoon jisne kal tumhara kaam karwaya tha, account number note karo",
        "allow",
        (),
        True,
    ),
    (
        "mera beta hospital mein hai, tum jitna kar sako bhej do",
        "allow",
        (),
        True,
    ),
    (
        # Added to the set as a case the words could not see, and no longer one: the
        # purchase-scam family reads "token amount". It stays in the set with its real
        # expectation, because deleting a case that started passing is how a test set
        # quietly stops covering the thing it was written for.
        "property ka token amount aaj dena hoga warna deal nikal jayegi",
        "warn",
        ("purchase_scam",),
        False,
    ),
)

# ---------------------------------------------------------------- replies
# (reply, expected classification)
REPLY_CASES: tuple[tuple[str, str], ...] = (
    ("haan", "affirmative"),
    ("ji haan", "affirmative"),
    ("yes", "affirmative"),
    ("confirm", "affirmative"),
    ("theek hai", "affirmative"),
    ("bhej do", "affirmative"),
    ("ہاں", "affirmative"),
    ("nahi", "negative"),
    ("no", "negative"),
    ("cancel karo", "negative"),
    ("rok do", "negative"),
    ("mat bhejo", "negative"),
    ("نہیں", "negative"),
    ("hmm", "unclear"),
    ("", "unclear"),
    ("maybe", "unclear"),
    ("pata nahi", "unclear"),
    ("kya", "unclear"),
    ("acha", "unclear"),
    ("Imran ko bhejo", "unclear"),
    ("haan lekin pehle balance batao", "unclear"),
)


def totals() -> dict[str, int]:
    return {
        "numerals": len(NUMERAL_CASES),
        "no_amount": len(NO_AMOUNT_CASES),
        "actions": len(ACTION_CASES),
        "near_miss": len(NEAR_MISS_CASES),
        "coercion": len(COERCION_CASES),
        "coercion_expected_misses": sum(1 for case in COERCION_CASES if case[3]),
        "replies": len(REPLY_CASES),
        "paired": len(PAIRED_CASES),
        "paired_scams": sum(1 for case in PAIRED_CASES if case[6]),
        "all": (
            len(NUMERAL_CASES)
            + len(NO_AMOUNT_CASES)
            + len(ACTION_CASES)
            + len(NEAR_MISS_CASES)
            + len(COERCION_CASES)
            + len(REPLY_CASES)
            + len(PAIRED_CASES)
        ),
    }


# ---------------------------------------------------------------- paired cases
# The coercion check reads words and the set above measures what that misses. These
# cases pair a message with the transfer it is trying to produce, so the behavioural
# layer can be measured on the same scams - and, just as importantly, so the cost of
# that layer is measured on ordinary transfers.
#
# (label, message, beneficiary, amount, expected coercion verdict,
#  expected behaviour level, scam)
PAIRED_CASES: tuple[tuple[str, str, str, float, str, str, bool], ...] = (
    # The four approaches the lexical check misses, each with its transfer.
    (
        "ammi's friend",
        "beta main tumhari ammi ki saheli hoon, thori madad chahiye thi",
        "Zubair Khan",
        50000,
        "allow",
        "elevated",
        True,
    ),
    (
        "the fixer",
        "main wohi hoon jisne kal tumhara kaam karwaya tha, account number note karo",
        "Zubair Khan",
        40000,
        "allow",
        "elevated",
        True,
    ),
    (
        "hospital",
        "mera beta hospital mein hai, tum jitna kar sako bhej do",
        "Rashida Bibi",
        35000,
        "allow",
        "elevated",
        True,
    ),
    (
        # The words catch this one now; the behavioural layer still escalates it, so both
        # layers fire and the transfer is stopped twice over. Kept as a paired case to
        # show that happening rather than deleted for being easy.
        "property token",
        "property ka token amount aaj dena hoga warna deal nikal jayegi",
        "Ayesha Noor",
        45000,
        "warn",
        "elevated",
        False,
    ),
    # Ordinary transfers. An escalation on any of these is a cost, not a catch.
    (
        "usual shopkeeper",
        "Bilal Traders ko teen hazaar bhejo",
        "Bilal Traders",
        3200,
        "allow",
        "none",
        False,
    ),
    (
        "monthly to mother",
        "Nasreen ko barah hazaar bhejo",
        "Nasreen Akhtar",
        12000,
        "allow",
        "none",
        False,
    ),
    ("small to brother", "Imran ko hazaar bhejo", "Imran Shah", 1000, "allow", "none", False),
    (
        "slightly larger, known payee",
        "Bilal Traders ko paanch hazaar bhejo",
        "Bilal Traders",
        5000,
        "allow",
        "none",
        False,
    ),
    # A legitimate first large transfer. Escalating this is correct behaviour and is
    # counted as a cost rather than claimed as a catch: the system slows down, and the
    # user goes ahead.
    (
        "new landlord",
        "Rashid Ali ko pandrah hazaar bhejo",
        "Rashid Ali",
        15000,
        "allow",
        "elevated",
        False,
    ),
)
