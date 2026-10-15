# 01 &mdash; Switchboard

A payment-message workbench for ISO 8583, the message format behind card transactions at
ATMs and point-of-sale terminals. Paste raw traffic and read what it was, generate a whole
transaction lifecycle from a named scenario, and check a flow against the invariants a
switch depends on.

`make serve01` &rarr; <http://127.0.0.1:8101> &middot; `make bench01` to re-measure.

## What it holds

| Piece | File | What it does |
|---|---|---|
| Data element dictionary | [`fields.py`](fields.py) | The **50** elements a card switch actually populates, each with its character class and length discipline. |
| Codec | [`codec.py`](codec.py) | Pack and unpack, bitmap and MTI handling, character-class enforcement, field offsets. |
| Narration | [`narrate.py`](narrate.py) | Processing codes, response codes, entry modes, currencies and DE 90 in plain English. |
| Scenarios | [`scenarios.py`](scenarios.py) | **14** lifecycles &mdash; approvals, partial approval, declines, reversals, inquiries, transfers, network management. |
| Invariants | [`lifecycle.py`](lifecycle.py) | **18** rules over a whole flow, not a single message. |
| Fault injection | [`mutations.py`](mutations.py) | **31** hand-written faults plus a random-corruption pass. |

## The numbers

From [`results.json`](results.json), produced by `make bench01`.

| Measure | Result |
|---|---|
| Valid flows checked | 14 (33 messages) |
| False positives on valid traffic | **0** |
| Round-trip stable | 33/33 |
| Faults injected | 31 |
| Killed | **31/31** |
| Killed by the rule written for it | **31/31** |
| Invariants never exercised | none |
| Random corruptions, illegal for the field | **627/627 rejected** |
| Random corruptions, legal for the field | 988/988 accepted, as they should be |
| Random corruptions, in the framing | 377/385 rejected (97.9%) |

A 31/31 kill rate is coverage of *known* fault classes, because the catalogue is
hand-written. The random-corruption pass is the honest complement: 2,000 single-character
corruptions of a valid message, split into the three populations that have three different
correct answers. Swapping one digit of an amount for another digit produces a different but
entirely valid message, and a parser that rejected it would be wrong.

**Why the framing figure cannot reach 100%.** Seven of the eight accepted corruptions turned
a valid MTI into another valid MTI. The eighth flipped one bitmap bit and swapped DE 14 for
DE 16 &mdash; both `n4`, so the layout is byte-identical and the message re-parses as a
different, well-formed message. An ISO 8583 bitmap carries no redundancy, so no parser can
catch that. It is the reason the invariant layer exists instead of stopping at the codec.

## Two rules that are not the obvious version

- **L01 is not "every request has a response."** An unanswered request is correct when the
  acquirer went on to reverse it, which is exactly what a timeout looks like. The rule is
  that a request is answered *or* reversed; the simpler version flags every timeout in
  production.
- **L04 is not "the response amount always equals the request amount."** Partial approval
  is the one response allowed to change DE 4, so the amount rule and the partial rule (L05)
  have to be written as a pair or one of them is wrong.

A third was found by running the checker over its own valid flows before trusting it: **L12**
demanded an authorisation id on every approval, and reversal and network approvals correctly
carry none. It is now scoped to MTI classes 1 and 2.

## The dashboard

Overview (the fault catalogue and the corruption breakdown), Decode (click any field row to
highlight that field inside the raw message), Build a flow, Validate pasted traffic, and the
rule list with the dictionary.

A message that will not parse still gets an answer: the error text, how far the parser got,
and the MTI and bitmap it could still read. A workbench that says only "invalid" when handed
real broken traffic is useless.
