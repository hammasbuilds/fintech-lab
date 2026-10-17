# 03 &mdash; Guardian

A transaction assistant for Roman Urdu, Urdu and code-mixed speech. Say what you want in
whatever mixture you actually speak, and it resolves to one concrete action, reads that
action back in your own language, and executes nothing without an explicit yes.

**There is no model in it.** The language layer is a lexicon and a grammar, and the risk
layer is an account history. For this task that is a feature: a rule that mis-parses an
amount can be found and fixed, and the thing deciding to move money is small enough to
read in an afternoon. It is also the honest baseline any model-based version has to beat.

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
| **Scams the word check misses** | **4/4 (100%)** |
| Of those, escalated once the *transfer* is judged | **4/4** |
| Missed by both layers | **0/4** |
| Ordinary transfers also escalated | **1/5** &mdash; the cost |
| Paired cases behaving as written | 9/9 |

Actions are graded on the **resulting action**, not the wording. An assistant that produces
a fluent sentence and the wrong beneficiary has failed, and a text-overlap metric would hide
that.

Four real scam approaches that use none of the detector's vocabulary are in the test set
on purpose. A coercion detector evaluated only on messages matching its own lexicon reports
a perfect score and tells you nothing.

The word-based check still misses all four, and that is still published. What the second
round added is a layer that looks at the **transfer** instead of the sentence, and it
escalates all four. The cost is in the table too: one ordinary transfer in five is also
slowed down. Reporting the first number without the second would be advertising.

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

## Judging the transfer, not the words

A scammer can rephrase a sentence. They cannot make a transfer look ordinary for an account
they have never seen. [`behaviour.py`](behaviour.py) scores a proposed transfer against what
this account actually does:

| Signal | When it fires |
|---|---|
| `first_time_beneficiary` | the payee has never been paid from this account |
| `unusually_large` | at least 3&times; the usual transfer **to this payee**, or to anyone if they are new |
| `balance_sweep` | at least 85% of the balance |
| `exceeds_balance` | more than is in the account, so it cannot complete |
| `beyond_daily_cap` | would push the day's total past the account's own cap |
| `rapid_repeat` | three or more transfers inside the hour |

```
  message    beta main tumhari ammi ki saheli hoon, thori madad chahiye thi
  coercion check                 allow   <- it misses this entirely
  the transfer it wants          PKR 50,000 to Zubair Khan, never paid before
  behaviour                      elevated
      - first_time_beneficiary: Zubair Khan has not been paid from this account before
      - unusually_large: 50,000 is 15.6 times the usual transfer from this account, 3,200
      - exceeds_balance: 50,000 is more than the 18,424 in the account
```

**Behaviour escalates; it does not block.** A first large transfer to a new payee is also
what paying a new landlord looks like, so refusing it would make the product unusable. An
escalated confirmation says what stands out and asks for the word `confirm` rather than a
habitual `haan` &mdash; because a habitual yes is exactly what someone being talked through
a transfer says. Words that only occur in a script block outright; unusual behaviour slows
things down. Conflating the two would mean either blocking honest people or letting a script
through.

Two details that matter more than they look:

- **A payee with a history sets their own baseline.** The first version judged every
  transfer against one account-wide median, which marked the regular 12,000 to a parent as
  unusual purely because most *other* transfers are small &mdash; a false positive on the
  single most routine payment the account makes.
- **The daily cap escalates on its own**, without needing a second signal. A cap that a
  habitual yes clears is not a cap.

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
   it, and neither can `confirm`.

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
The test set measures exactly that, and the answer is 4 of 4 missed &mdash; which is why
the behavioural layer above exists and is measured on the same four cases.

## The audit trail

Every action **and every refusal**, hash-chained, each entry carrying the hash of the one
before it. `verify` reports the first index where the chain breaks rather than a bare
true/false.

Refusals are recorded as deliberately as executions, because a log that only holds what
happened cannot answer the question that gets asked after a fraud: did the system warn them?

## At full size

The 140 cases above are written by hand, which makes them a regression suite rather than
a measurement. These numbers are the same shipped layers over **200,000 generated
utterances** whose gold labels come from how each sentence was composed - the amount is
chosen before any words exist - and whose spellings are the ones attested in 37,220 real
Roman Urdu sentences.

Nothing in the generator imports the product, so it cannot be wrong in the same direction
as the parser. Build it with `make data` and run it with `make big03`.

<!-- datasets:start -->
| Dataset | Rows | Size | Role | Source |
| --- | ---: | ---: | --- | --- |
| `generated/p03_utterances` | 200,000 | 93 MB | cohort | generated by projects/p03_guardian/generator.py |

| At full size | Result | Over |
| --- | ---: | --- |
| amounts read exactly | **0.9943** | 141,882 cases carrying an amount |
| amounts not found at all | **0** | same cases |
| amounts wrong by ten times or more | **0** | same cases |
| intent accuracy | **0.9759** | 146,667 cases with an intent |
| payees resolved | **0.9263** | 77,275 naming one contact |
| asked instead of guessing | **0.9899** | 32,401 ambiguous cases |
| coercion caught, families it knows | **0.9857** | the twelve families in the product's own vocabulary |
| coercion caught, types it does not know | **0.0817** | job offers, QR swaps, SIM swaps, refund overpayment |
| coercion caught, everything together | **0.8744** | 45,000 scam scripts; the blend of the two rows above |
| ordinary traffic blocked | **0.0211** | 25,000 ordinary messages |
| confirmations read correctly | **0.8992** | 5,000 replies |

| The dimensions added this round | Result | Over |
| --- | ---: | --- |
| scam types the product has no words for | **0.0817** | 5,542 scripts: job offers, QR swaps, SIM swaps, refund overpayment |
| obfuscated security words | **0.9974** | leetspeak; the other four techniques are within 0.01 of it |
| Urdu script, ordinary spelling | **1** | 2,410 scripts |
| Urdu script, Arabic codepoints | **1** | the same sentences, pasted from elsewhere - identical to the row above, which is the point of folding the codepoints |
| splits across one day | **1** | 514 splits |
| splits across several days | **0.9867** | 527 splits |
| whole conversations handled correctly | **0.7289** | 1,667 threads, 0 executed after a no |

Two of those rows were zero before the work in this round. Splits spread over several days never came near the daily cap and the layer had no monthly view, so 527 of them went entirely unnoticed; with a monthly signal they are caught 0.987 of the time and the false-block rate on ordinary traffic is unchanged. And a conversation where the assistant asked which payee was meant could not read the answer - 53 of 1,667 threads completed - because the session parsed every reply as a fresh instruction.

| Coercion family | Caught | Named correctly | Scripts |
| --- | ---: | ---: | ---: |
| account redirect | 0.9873 | 0.9835 | 3,932 |
| advance fee | 0.9931 | 0.9902 | 3,469 |
| investment scam | 1 | 1 | 3,204 |
| invoice redirect | 1 | 1 | 2,214 |
| lure | 0.9879 | 0.9857 | 1,401 |
| otp solicitation | 0.9945 | 0.9922 | 4,348 |
| purchase scam | 0.9909 | 0.9897 | 14,257 |
| remote access | 0.9864 | 0.9809 | 1,836 |
| romance scam | 0.9937 | 0.9919 | 2,234 |
| staff impersonation | 0.9871 | 0.9788 | 3,965 |
| threat | 0.9917 | 0.9896 | 2,894 |
| urgency | 0.9302 | 0.8818 | 2,952 |

Sixteen defects came out of this run: nine in the product, and seven of my own in the generator and the scorer. Both halves belong here, because a generated test set nobody audits measures the generator.

**In the product.** Three in the amount parser: the mobile-number guard matched starting *inside* an amount, so "42000 03461124202 ko bhejo" came back with no amount at all; the word scanner read the phone number as a continuation of the amount and produced 3,104,061,991, which only the guard's own reserved span stopped being offered as a transfer; and `hzar`, a spelling attested in the mined corpora, was missing from the scale table. Three in the intent lexicon: the bare stem `bhej` - the commonest way of saying it - matched nothing, `X ke account me 154000 dein` needed a pattern rather than a substring, and `250 ka load do` and `account me kitna hai` were both unknown. Three in the coercion corpus: the commonest account-redirect phrasings were absent, which took that family from 0.300 to 0.981 once added - and it had been *naming* none of them, because "purana account band hai" contains "account band" and was being caught as a threat.

**In the generator, which is mine.** Fractional modifiers were multiplied instead of added, so "saadhe do crore" was labelled 30,000,000 rather than 25,000,000; benign scripts naming a relation carried a random contact as the gold payee; `k` was used as a word scale because the corpora attest it - as the shortened conjunction *ke* - producing "chaar k paanch sau"; typos were injected into the amount itself, so the sentence no longer held the number it was scored on; a transfer to a phone number was labelled a top-up; the giving verb `do` was placed next to a number, where it is genuinely ambiguous with the numeral two; and confirmations were scored through the intent detector, which has no affirmative intent to return - that one read 0.5034, which is chance.

The false-block rate is printed next to the catch rate because they move together. Adding the bare words for "quickly" took the catch rate to 0.9802 and the false-block rate from 0.0211 to 0.1232 - one ordinary message in eight, since "jaldi nahi koi baat nahi" means *no rush*. Requiring a verb alongside them gives 0.9609 at 0.0211, which is what shipped.
<!-- datasets:end -->
