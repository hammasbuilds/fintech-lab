"""The confirmation spine: the part that decides whether anything actually happens.

Three properties, and they are the product:

1. **Nothing executes without an explicit yes.** The affirmative and negative replies are
   closed sets. Anything outside them - "hmm", "theek", silence, a new sentence - is
   treated as *unclear* and the question is asked again. A reply that is not understood
   is never read as consent, which is the failure mode that costs money.
2. **The read-back is in the user's language and spells the amount out.** "15,000" and
   "1,500" differ by one character and by ten times the money. ``pandrah hazaar rupay``
   and ``dedh hazaar rupay`` do not sound alike, so the read-back says the words.
3. **A blocked assessment cannot be confirmed past.** When the coercion check blocks, the
   session refuses to even offer a confirmation. There is no "proceed anyway" button,
   because a user under pressure is exactly the user who would press it.

A fourth thing was added in the second round. The coercion check reads words, and the
test set measures what that misses: four scam approaches phrased in ordinary language,
four misses. :mod:`behaviour` judges the transfer instead of the sentence, and when a
transfer is unusual for the account it **escalates** the confirmation - says what stands
out, and requires the full word ``confirm`` rather than a habitual "haan". It never
blocks, because a first large transfer to a new payee is also what paying a new landlord
looks like. Words that only occur in a script block; unusual behaviour slows things down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from . import behaviour, coercion, nlu, numerals
from .ledger import Ledger


class State(StrEnum):
    IDLE = "idle"
    NEEDS_INFORMATION = "needs_information"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTED = "executed"
    REFUSED = "refused"
    BLOCKED = "blocked"


#: The only replies accepted when the behavioural layer has escalated. A habitual
#: "haan" is exactly what someone being talked through a transfer says, so an escalated
#: confirmation asks for a word they have to mean.
STEP_UP = {"confirm", "confirm karo", "tasdeeq", "haan tasdeeq", "yes confirm"}

#: Replies that mean yes. Closed set, matched whole.
AFFIRMATIVE = {
    "haan",
    "han",
    "haan ji",
    "ji haan",
    "ji",
    "jee",
    "yes",
    "y",
    "confirm",
    "confirmed",
    "theek hai",
    "thik hai",
    "ok karo",
    "karo",
    "bhej do",
    "send it",
    "go ahead",
    "bilkul",
    # Compound forms. The set is closed on purpose - nothing outside it counts as consent -
    # and it was missing the commonest way of saying yes to a transfer in Roman Urdu. Over
    # 1,667 generated conversations, 71 ended with "haan bhej do" and were read as neither
    # a yes nor a no, which is a refusal to act on an unmistakable instruction. Each of
    # these is a yes *and* the verb; none is ambiguous.
    "haan bhej do",
    "haan bhejo",
    "haan bhej dein",
    "ji haan bhej do",
    "haan karo",
    "theek hai bhej do",
    "ok bhej do",
    "yes send",
    "yes send it",
    "ہاں بھیج دو",
    "ہاں",
    "جی ہاں",
    "ٹھیک ہے",
} | STEP_UP
# The step-up words are folded in rather than listed twice. They have to be a subset:
# a word that is accepted as a step-up but not recognised as a yes can never execute
# anything, which is what the first version of this did to "confirm karo" and "tasdeeq".

#: Replies that mean no. Also closed.
NEGATIVE = {
    "nahi",
    "nahin",
    "na",
    "no",
    "n",
    "cancel",
    "cancel karo",
    "rok do",
    "ruko",
    "mat bhejo",
    "nahi bhejna",
    "stop",
    "band karo",
    "نہیں",
    "منسوخ",
}


@dataclass
class Turn:
    speaker: str
    text: str
    language: str = "en"

    def as_dict(self) -> dict[str, object]:
        return {"speaker": self.speaker, "text": self.text, "language": self.language}


@dataclass
class Session:
    """One conversation. Holds the pending action and the audit trail."""

    language: str = "en"
    state: State = State.IDLE
    pending: nlu.Utterance | None = None
    assessment: coercion.Assessment | None = None
    transcript: list[Turn] = field(default_factory=list)
    ledger: Ledger = field(default_factory=Ledger)
    contacts: tuple[nlu.Contact, ...] = nlu.CONTACTS
    profile: behaviour.Profile = field(default_factory=behaviour.demo_profile)
    conduct: behaviour.Assessment | None = None
    stepped_up: bool = False

    # -- helpers --
    def say(self, text: str) -> Turn:
        turn = Turn("assistant", text, self.language)
        self.transcript.append(turn)
        return turn

    def heard(self, text: str) -> Turn:
        turn = Turn("user", text, self.language)
        self.transcript.append(turn)
        return turn

    @property
    def awaiting(self) -> bool:
        return self.state == State.AWAITING_CONFIRMATION

    # -- the one entry point --
    def handle(self, text: str) -> dict[str, object]:
        """Take one user message and return what the assistant does about it."""
        self.heard(text)

        assessment = coercion.assess(text)
        if self.awaiting and assessment.verdict != coercion.ALLOW:
            # Pressure arriving *during* a confirmation is the live coaching case. The
            # pending action is dropped rather than held, so a later bare "haan" cannot
            # confirm it.
            self.pending = None
            self.stepped_up = False
            self.state = State.BLOCKED
            self.assessment = assessment
            message = self._blocked_message(assessment)
            self.ledger.append(
                "coercion_blocked_mid_confirmation",
                {
                    "families": assessment.families,
                    "text": text,
                },
            )
            return {
                "state": self.state.value,
                "reply": message,
                "assessment": assessment.as_dict(),
                "action": None,
            }

        if self.awaiting:
            return self._resolve_confirmation(text)

        if self.state is State.NEEDS_INFORMATION and self.pending is not None:
            filled = self._fill_the_gap(text)
            if filled is not None:
                return filled
            kept = self._keep_waiting(text)
            if kept is not None:
                return kept

        if assessment.verdict == coercion.BLOCK:
            self.state = State.BLOCKED
            self.assessment = assessment
            self.ledger.append(
                "coercion_blocked",
                {
                    "families": assessment.families,
                    "text": text,
                },
            )
            return {
                "state": self.state.value,
                "reply": self._blocked_message(assessment),
                "assessment": assessment.as_dict(),
                "action": None,
            }

        utterance = nlu.parse(text, self.contacts)
        self.assessment = assessment

        if utterance.issues:
            self.pending = utterance
            self.state = State.NEEDS_INFORMATION
            question = nlu.question(utterance.issues[0], self.language)
            extra = self._candidate_list(utterance)
            reply = self.say(question + (("  " + extra) if extra else ""))
            return {
                "state": self.state.value,
                "reply": reply.text,
                "asking": utterance.issues[0],
                "utterance": utterance.as_dict(),
                "assessment": assessment.as_dict(),
                "action": None,
            }

        if not utterance.moves_money:
            self.pending = None
            self.state = State.EXECUTED
            reply = self.say(self._read_only_reply(utterance))
            self.ledger.append("read_only_action", {"action": utterance.action()})
            return {
                "state": self.state.value,
                "reply": reply.text,
                "utterance": utterance.as_dict(),
                "assessment": assessment.as_dict(),
                "action": utterance.action(),
            }

        self.pending = utterance
        self.state = State.AWAITING_CONFIRMATION
        payee = (
            utterance.beneficiary.name
            if utterance.beneficiary
            else (utterance.target_number or None)
        )
        conduct = behaviour.assess(
            self.profile, payee, utterance.amount.value if utterance.amount else None
        )
        self.conduct = conduct
        self.stepped_up = conduct.escalated
        reply = self.say(self.read_back(utterance, assessment, conduct))
        self.ledger.append(
            "confirmation_requested",
            {
                "action": utterance.action(),
                "behaviour": conduct.level,
                "signals": [signal.name for signal in conduct.signals],
            },
        )
        return {
            "state": self.state.value,
            "reply": reply.text,
            "utterance": utterance.as_dict(),
            "assessment": assessment.as_dict(),
            "behaviour": conduct.as_dict(),
            "action": None,
        }

    #: Ways of saying "the Nth one", in the two languages this assistant speaks. Only
    #: reachable while a candidate list is on screen, so there is nothing for an ordinal
    #: to point at by accident.
    ORDINALS = {
        "pehla": 0,
        "pehla wala": 0,
        "pehle wala": 0,
        "first": 0,
        "1": 0,
        "ek": 0,
        "doosra": 1,
        "doosra wala": 1,
        "dusra": 1,
        "dusra wala": 1,
        "second": 1,
        "2": 1,
        "teesra": 2,
        "teesra wala": 2,
        "tisra": 2,
        "third": 2,
        "3": 2,
        "chautha": 3,
        "fourth": 3,
        "4": 3,
        "aakhri": -1,
        "akhri": -1,
        "last": -1,
        "aakhri wala": -1,
    }

    def _choose_from_candidates(self, text: str, pending: nlu.Utterance) -> nlu.Contact | None:
        """Read "the first one", "the HBL one" or "...7725" as a pick from the list.

        Narrow on purpose. It only runs while a candidate list has been offered, it only
        accepts an answer that picks exactly one of them, and it never guesses: two banks
        called HBL in the list means the answer was not an answer, and the question is
        asked again.
        """
        candidates = list(pending.candidates) or list(pending.near_misses)
        if not candidates:
            return None
        cleaned = numerals.normalise(text).strip().lower().strip(".!?,")

        index = self.ORDINALS.get(cleaned)
        if index is None:
            # "jo pehla hai", "pehla wala hi" - the ordinal with words around it.
            for word, position in self.ORDINALS.items():
                if word.isdigit():
                    continue
                if re.search(rf"\b{re.escape(word)}\b", cleaned):
                    index = position
                    break
        if index is not None and -len(candidates) <= index < len(candidates):
            return candidates[index]

        by_bank = [
            contact
            for contact in candidates
            if re.search(rf"\b{re.escape(contact.bank.lower())}\b", cleaned)
        ]
        if len(by_bank) == 1:
            return by_bank[0]

        digits = re.findall(r"\d{4}", cleaned)
        by_tail = [contact for contact in candidates if contact.account_tail in digits]
        if len(by_tail) == 1:
            return by_tail[0]
        return None

    # -- answering the question that was just asked --
    def _fill_the_gap(self, text: str) -> dict[str, object] | None:
        """Read ``text`` as an answer to the pending question, or decline to.

        Only the gap that was asked about is filled, and only when the answer resolves it
        outright: a name that matches exactly one contact, or a single amount. Anything
        else returns None and the message is parsed as a new instruction, which is what
        the session did with every answer before this existed.

        The narrowness is the point. A session that treats the next message as an answer
        whatever it says will happily read "no, cancel that" as a payee.
        """
        pending = self.pending
        if pending is None:
            return None
        issue = pending.issues[0] if pending.issues else ""

        if issue in {nlu.NO_BENEFICIARY, nlu.AMBIGUOUS_BENEFICIARY, nlu.UNKNOWN_BENEFICIARY}:
            resolution = nlu.resolve_beneficiary(text, self.contacts)
            contact = resolution.contact
            if contact is None:
                # The question was "which one?" and it listed the candidates with their
                # banks and account tails. A person answers that with "the first one" or
                # "the HBL one", and neither is a name, so the resolver above cannot see
                # it: 381 of 1,667 generated conversations died on exactly this turn.
                contact = self._choose_from_candidates(text, pending)
            if contact is None:
                return None
            # Rebuilt rather than mutated: the resolver's own reply said no single
            # contact matched, and this is a different answer to a different question.
            resolution = nlu.Resolution(
                contact=contact, candidates=[], mention=text.strip(), issues=[]
            )
            pending.beneficiary = contact
            pending.beneficiary_mention = text.strip()
            pending.candidates = []
            pending.issues = [
                name
                for name in pending.issues
                if name
                not in {
                    nlu.NO_BENEFICIARY,
                    nlu.AMBIGUOUS_BENEFICIARY,
                    nlu.UNKNOWN_BENEFICIARY,
                }
            ]
            self.ledger.append(
                "gap_filled",
                {"issue": issue, "answer": text, "beneficiary": contact.name},
            )
        elif issue == nlu.NO_AMOUNT:
            amounts = numerals.find_all(text)
            if len(amounts) != 1:
                return None
            pending.amount = amounts[0]
            pending.all_amounts = amounts
            pending.issues = [name for name in pending.issues if name != nlu.NO_AMOUNT]
            self.ledger.append(
                "gap_filled", {"issue": issue, "answer": text, "amount": amounts[0].value}
            )
        else:
            return None

        if pending.issues:
            question = nlu.question(pending.issues[0], self.language)
            extra = self._candidate_list(pending)
            reply = self.say(question + (("  " + extra) if extra else ""))
            return {
                "state": self.state.value,
                "reply": reply.text,
                "asking": pending.issues[0],
                "utterance": pending.as_dict(),
                "action": None,
            }

        # The gap is closed and the instruction is whole: ask for confirmation exactly as
        # if it had arrived complete in the first place.
        self.state = State.AWAITING_CONFIRMATION
        payee = pending.beneficiary.name if pending.beneficiary else (pending.target_number or None)
        assessment = coercion.assess(" ".join(turn.text for turn in self.transcript[-3:]))
        conduct = behaviour.assess(
            self.profile, payee, pending.amount.value if pending.amount else None
        )
        self.assessment = assessment
        self.conduct = conduct
        self.stepped_up = conduct.escalated
        reply = self.say(self.read_back(pending, assessment, conduct))
        self.ledger.append(
            "confirmation_requested",
            {
                "action": pending.action(),
                "behaviour": conduct.level,
                "signals": [signal.name for signal in conduct.signals],
                "after_filling": issue,
            },
        )
        return {
            "state": self.state.value,
            "reply": reply.text,
            "utterance": pending.as_dict(),
            "assessment": assessment.as_dict(),
            "behaviour": conduct.as_dict(),
            "action": None,
        }

    def _keep_waiting(self, text: str) -> dict[str, object] | None:
        """Hold the pending request when the reply is neither an answer nor a new order.

        "koi bhi" ("any of them") answers nothing, and the session used to parse it as a
        fresh instruction, find no intent in it, and **drop the transfer** - so the user's
        whole request was gone and the assistant asked what they wanted to do. Only a
        message that is recognisably a new instruction should replace a pending one.
        """
        pending = self.pending
        if pending is None or not pending.issues:
            return None
        fresh = nlu.parse(text, self.contacts)
        if fresh.intent != nlu.UNKNOWN:
            return None  # a real instruction: let it take over
        question = nlu.question(pending.issues[0], self.language)
        extra = self._candidate_list(pending)
        reply = self.say(
            "I did not understand that as an answer.  "
            + question
            + (("  " + extra) if extra else "")
        )
        self.ledger.append(
            "answer_not_understood", {"text": text, "still_asking": pending.issues[0]}
        )
        return {
            "state": self.state.value,
            "reply": reply.text,
            "asking": pending.issues[0],
            "utterance": pending.as_dict(),
            "action": None,
        }

    # -- confirmation --
    def _resolve_confirmation(self, text: str) -> dict[str, object]:
        reply_kind = classify_reply(text)
        pending = self.pending
        if (
            reply_kind == "affirmative"
            and self.stepped_up
            and numerals.normalise(text).strip().lower().strip(".!?,") not in STEP_UP
        ):
            # Escalated: a plain yes is not enough. Nothing is refused, the question is
            # asked again in stronger terms.
            self.ledger.append("step_up_required", {"text": text})
            message = self.say(
                (
                    "Because this transfer is unusual for your account, a plain yes is not "
                    "enough. Reply with the word 'confirm' if you are sure, or 'no' to stop. "
                    "Nothing has been sent."
                )
                if self.language == "en"
                else (
                    "Yeh transfer aap ke account ke liye ghair-mamooli hai, is liye sirf "
                    "'haan' kaafi nahi. Yaqeen ho to 'confirm' likhein, warna 'nahi'. "
                    "Abhi kuch nahi bheja gaya."
                )
            )
            return {
                "state": self.state.value,
                "reply": message.text,
                "action": None,
                "step_up_required": True,
                "assessment": None,
            }
        if reply_kind == "affirmative" and pending is not None:
            action = pending.action()
            self.state = State.EXECUTED
            self.pending = None
            if pending.amount is not None:
                # The profile learns, so the same payee is not "first time" twice and the
                # daily total actually accumulates.
                self.profile.record(
                    pending.beneficiary.name
                    if pending.beneficiary
                    else pending.target_number or "unknown",
                    pending.amount.value,
                )
            self.stepped_up = False
            entry = self.ledger.append("executed", {"action": action})
            message = self.say(self._executed_message(pending))
            return {
                "state": self.state.value,
                "reply": message.text,
                "action": action,
                "ledger_entry": entry,
                "assessment": None,
            }
        if reply_kind == "negative":
            self.state = State.REFUSED
            self.stepped_up = False
            dropped = self.pending.action() if self.pending else None
            self.pending = None
            self.ledger.append("refused_by_user", {"action": dropped})
            message = self.say(
                "Cancelled. Nothing was sent."
                if self.language == "en"
                else "Mansookh. Kuch bhi nahi bheja gaya."
            )
            return {
                "state": self.state.value,
                "reply": message.text,
                "action": None,
                "assessment": None,
            }

        # Anything else: the reply was not understood, so it is not consent.
        self.ledger.append("unclear_reply", {"text": text})
        message = self.say(
            "I did not understand that as a yes or a no, so nothing has been sent. "
            "Reply with 'yes' to send, or 'no' to cancel."
            if self.language == "en"
            else "Yeh 'haan' ya 'nahi' samajh nahi aya, is liye kuch nahi bheja gaya. "
            "Bhejne ke liye 'haan' likhein, mansookh karne ke liye 'nahi'."
        )
        return {
            "state": self.state.value,
            "reply": message.text,
            "action": None,
            "unclear": True,
            "assessment": None,
        }

    # -- messages --
    def read_back(
        self,
        utterance: nlu.Utterance,
        assessment: coercion.Assessment | None = None,
        conduct: behaviour.Assessment | None = None,
    ) -> str:
        urdu = self.language == "ur"
        amount = utterance.amount.value if utterance.amount else 0.0
        spelled = numerals.say(amount, "ur" if urdu else "en")
        figure = f"{amount:,.0f}"

        if utterance.intent == nlu.SEND and utterance.beneficiary is not None:
            contact = utterance.beneficiary
            if urdu:
                body = (
                    f"Tasdeeq karein: {spelled} ({figure}) {contact.name} ko, "
                    f"{contact.bank} account jo {contact.account_tail} par khatam hota hai."
                )
            else:
                body = (
                    f"Please confirm: {spelled} ({figure}) to {contact.name}, "
                    f"{contact.bank} account ending {contact.account_tail}."
                )
            if utterance.near_misses:
                others = ", ".join(f"{c.name} (...{c.account_tail})" for c in utterance.near_misses)
                body += f"  Not {others}." if not urdu else f"  {others} nahi."
        elif utterance.intent == nlu.TOPUP:
            body = (
                f"Tasdeeq karein: {spelled} ({figure}) ka load {utterance.target_number} par."
                if urdu
                else f"Please confirm: {spelled} ({figure}) loaded to {utterance.target_number}."
            )
        elif utterance.intent == nlu.BILL:
            body = (
                f"Tasdeeq karein: {utterance.biller} ka bill {spelled} ({figure})."
                if urdu
                else f"Please confirm: {utterance.biller} bill of {spelled} ({figure})."
            )
        else:
            body = (
                f"Tasdeeq karein: {spelled} ({figure}) cash."
                if urdu
                else f"Please confirm: {spelled} ({figure}) in cash."
            )

        if utterance.corrected:
            body += (
                "  I took the amount you corrected to."
                if not urdu
                else "  Main ne woh raqam li jo aap ne theek ki."
            )
        if assessment is not None and assessment.verdict == coercion.WARN:
            body += "  " + assessment.reason
        if conduct is not None and conduct.level != behaviour.NONE:
            body += "  " + conduct.reason
            if conduct.escalated:
                body += "  " + behaviour.advice(self.language)
        if conduct is not None and conduct.escalated:
            body += (
                "  Reply with the word 'confirm' to send, or 'no' to cancel."
                if not urdu
                else "  Bhejne ke liye 'confirm' likhein, mansookh karne ke liye 'nahi'."
            )
        else:
            body += (
                "  Reply 'yes' to send or 'no' to cancel."
                if not urdu
                else "  Bhejne ke liye 'haan', mansookh karne ke liye 'nahi'."
            )
        return body

    def _executed_message(self, utterance: nlu.Utterance) -> str:
        amount = utterance.amount.value if utterance.amount else 0.0
        spelled = numerals.say(amount, self.language if self.language == "ur" else "en")
        if utterance.intent == nlu.SEND and utterance.beneficiary is not None:
            target = utterance.beneficiary.name
        elif utterance.intent == nlu.TOPUP:
            target = utterance.target_number
        elif utterance.intent == nlu.BILL:
            target = utterance.biller
        else:
            target = "cash"
        return (
            f"Sent: {spelled} to {target}. This is a sandbox, so no real money moved."
            if self.language == "en"
            else f"Bhej diya: {spelled} {target} ko. Yeh sandbox hai, asli paise nahi gaye."
        )

    def _read_only_reply(self, utterance: nlu.Utterance) -> str:
        if utterance.intent == nlu.BALANCE:
            return (
                "Your balance is PKR 18,423.50."
                if self.language == "en"
                else "Aap ka balance PKR 18,423.50 hai."
            )
        if utterance.intent == nlu.STATEMENT:
            return (
                "Your last three entries: 2,500 out, 18,000 in, 400 out."
                if self.language == "en"
                else "Aap ki aakhri teen entries: 2,500 gaye, 18,000 aye, 400 gaye."
            )
        return (
            "Your card is now blocked. No confirmation was needed: blocking a card only "
            "ever makes you safer, so it is not gated."
            if self.language == "en"
            else "Aap ka card band kar diya gaya. Is ke liye tasdeeq ki zaroorat nahi - card "
            "band karna sirf mehfooz banata hai."
        )

    def _blocked_message(self, assessment: coercion.Assessment) -> str:
        return assessment.reason + "  " + coercion.advice(self.language)

    def _candidate_list(self, utterance: nlu.Utterance) -> str:
        if not utterance.candidates:
            return ""
        return ", ".join(
            f"{contact.name} ({contact.bank} ...{contact.account_tail})"
            for contact in utterance.candidates
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "language": self.language,
            "pending": self.pending.as_dict() if self.pending else None,
            "behaviour": self.conduct.as_dict() if self.conduct else None,
            "step_up_required": self.stepped_up,
            "profile": {
                "balance": self.profile.balance,
                "daily_cap": self.profile.daily_cap,
                "usual_transfer": self.profile.usual_transfer,
                "sent_today": self.profile.sent_today(),
                "known_beneficiaries": sorted(self.profile.known_beneficiaries),
            },
            "transcript": [turn.as_dict() for turn in self.transcript],
            "ledger": self.ledger.as_dict(),
        }


def classify_reply(text: str) -> str:
    """``affirmative``, ``negative`` or ``unclear``. Nothing outside the sets counts."""
    cleaned = numerals.normalise(text).strip().lower().strip(".!?,")
    if cleaned in AFFIRMATIVE:
        return "affirmative"
    if cleaned in NEGATIVE:
        return "negative"
    return "unclear"
