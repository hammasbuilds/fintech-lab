# 03 &mdash; Guardian

A transaction assistant for Roman Urdu, Urdu and code-mixed speech. Say what you want in
whatever mixture you actually speak, and it resolves to one concrete action, reads that
action back in your own language, and executes nothing without an explicit yes.

**There is no model in it.** The language layer is a lexicon and a grammar, which for this
task is a feature: a rule that mis-parses an amount can be found and fixed, and the thing
deciding to move money is small enough to read in an afternoon. It is also the honest
baseline any model-based version has to beat.

`make serve03` &rarr; <http://127.0.0.1:8103> &middot; `make bench03` to re-measure.

## The numbers

131 hand-written cases, each with its answer stated. From [`results.json`](results.json).

| Measure | Result |
|---|---|
| Amounts parsed exactly | **48/48** |
| Amounts invented from text with none | **0/6** |
| Actions resolved correctly | **20/20** |
| Asked instead of guessing | **10/10** |
| Near miss named rather than resolved quietly | **3/3** |
| Coercion verdict, on messages its lexicon covers | **19/19** |
| Ordinary traffic wrongly blocked | **0/5** |
| Confirmation replies classified | **21/21** |
| Replies wrongly read as consent | **0** |
| End-to-end properties holding | **7/7** |
| **Out-of-vocabulary scams missed** | **4/4 (100%)** |

Actions are graded on the **resulting action**, not the wording. An assistant that produces
a fluent sentence and the wrong beneficiary has failed, and a text-overlap metric would hide
that.

The last row is the point of the last row. Four real scam approaches that use none of the
detector's vocabulary are in the test set on purpose, and it lets all four through. A
coercion detector evaluated only on messages matching its own lexicon reports a perfect
score and tells you nothing. **The confirmation spine, not the detector, is what stands
between a pressured user and a transfer.**

## Amounts

The part that has to be right. Everything else can be re-asked; an amount silently wrong by
a factor of a hundred is a wrong transfer.

| Said | Read as | Read back as |
|---|---:|---|
| `pandrah hazaar` | 15,000 | 15 hazaar rupay |
| `dedh hazaar` | 1,500 | 1 hazaar 5 sau rupay |
| `sawa lakh` | 125,000 | 1 lakh 25 hazaar rupay |
| `saadhe teen hazaar` | 3,500 | 3 hazaar 5 sau rupay |
| `paune do lakh` | 175,000 | 1 lakh 75 hazaar rupay |
| `do hazaar paanch sau` | 2,500 | 2 hazaar 5 sau rupay |
| `saat hazaar` | 7,000 | 7 hazaar rupay |
| `saath hazaar` | 60,000 | 60 hazaar rupay |

`saat` and `saath` are one letter apart and 53,000 rupees apart, which is why 114 number
words are listed with their spelling variants rather than matched fuzzily. Fractional
modifiers attach to the number *after* them, so `paune do lakh` is 1.75 lakh, not 2.

Three things the parser refuses to read as an amount: a mobile number (including
`0321-4455667`, which a length rule lets through because the hyphen splits it into two short
runs), a long bare digit run, and an IBAN-shaped string.

**`5 hazaar aur 7 hazaar` is two amounts, not 12,000.** A legitimate compound descends
through the scales &mdash; `char lakh pachaas hazaar` is 450,000 &mdash; so a scale that
repeats or grows starts a second amount instead of continuing the first.

## It refuses to guess

Where an utterance is ambiguous, the ambiguity is returned rather than resolved. "Rashid"
matches both *Rashid Ali* and *Rashida Bibi*; that is not a 60/40 call to settle quietly.
The exact first-name match is acted on and the near miss is **named in the read-back**:

```
user       Rashid ko pandrah hazaar bhejo
assistant  Please confirm: 15 thousand rupees (15,000) to Rashid Ali, Meezan account
           ending 4417.  Not Rashida Bibi (...9082).  Reply 'yes' to send or 'no' to cancel.
```

Self-correction is followed when it is unambiguous &mdash; `5 hazaar nahi nahi, pandrah
hazaar` takes 15,000 &mdash; and asked about when it is not. `nahi nahi, pandrah hazaar
bhejo, 5 hazaar nahi` is a sentence two readings fit, so it asks.

## The confirmation spine

Three properties, and they are the product:

1. **Nothing executes without an explicit yes.** The affirmative and negative replies are
   closed sets. "hmm", "acha", "theek", a new sentence &mdash; all unclear, and the question
   is asked again. A reply that is not understood is never consent.
2. **The read-back spells the amount out** in the user's language. 15,000 and 1,500 look
   alike; `pandrah hazaar` and `dedh hazaar` do not sound alike.
3. **A blocked assessment cannot be confirmed past.** There is no "proceed anyway" button,
   because the user under pressure is exactly the user who would press it. Pressure arriving
   *during* a confirmation drops the pending transfer, so a later bare "haan" cannot execute
   it.

Blocking a card is deliberately **not** gated. Gating a safety action behind a confirmation
makes the user less safe.

## Coercion

Seven families of pressure pattern. The score is the number of **distinct families** present,
not the number of matched phrases: "abhi bhejo" alone is an impatient relative, while "abhi
bhejo" with "main bank se baat kar raha hoon" and "OTP batao" is one script. One family
warns; two or more block.

Some patterns are a pair of words with an amount in between &mdash; `abhi 50 hazaar mere
account mein bhejo` &mdash; which a list of adjacent phrases cannot see, so those carry
bounded-gap patterns instead.

What it cannot do, said plainly: it reads the words in front of it. A caller coaching a
victim in the room is invisible to it, and a scammer who avoids these phrases scores zero.
The test set measures exactly that, and the answer is 4 of 4 missed.

## The audit trail

Every action **and every refusal**, hash-chained, each entry carrying the hash of the one
before it. `verify` reports the first index where the chain breaks rather than a bare
true/false.

Refusals are recorded as deliberately as executions, because a log that only holds what
happened cannot answer the question that gets asked after a fraud: did the system warn them?
