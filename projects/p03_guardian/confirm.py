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
