# 01 &mdash; Switchboard

A payment-message workbench for ISO 8583, the message format behind card transactions at
ATMs and point-of-sale terminals. Paste raw traffic &mdash; in the ASCII form a
specification uses **or the binary framing a live switch sends** &mdash; read what it was,
generate a whole transaction lifecycle from a named scenario, and check a flow against the
invariants a switch depends on.

`make serve01` &rarr; <http://127.0.0.1:8101> &middot; `make bench01` to re-measure.

## What it holds

| Piece | File | What it does |
|---|---|---|
| Data element dictionary | [`fields.py`](fields.py) | The **50** elements a card switch actually populates, each with its character class and length discipline. |
| Codec, ASCII | [`codec.py`](codec.py) | Pack and unpack, bitmap and MTI handling, character-class enforcement, field offsets, the Luhn check. |
| Codec, binary | [`binary.py`](binary.py) | Packed bitmaps, BCD numeric fields, BCD length prefixes, the 2-byte length header, and framing detection that tries each candidate against the parser. |
| Chip data | [`emv.py`](emv.py) | DE 55 as BER-TLV: multi-byte tags and lengths, constructed tags, **25** named tags, and the cross-field checks against DE 4 and DE 49. |
| Narration | [`narrate.py`](narrate.py) | Processing codes, response codes, entry modes, currencies and DE 90 in plain English. |
| Scenarios | [`scenarios.py`](scenarios.py) | **14** lifecycles &mdash; approvals, partial approval, declines, reversals, inquiries, transfers, network management. |
| Invariants | [`lifecycle.py`](lifecycle.py) | **22** rules over a whole flow, not a single message. |
| Fault injection | [`mutations.py`](mutations.py) | **38** hand-written faults, plus a random-corruption pass over both framings. |

## The numbers

From [`results.json`](results.json), produced by `make bench01`.

| Measure | Result |
|---|---|
| Valid flows checked | 14 (33 messages) |
| False positives on valid traffic | **0** |
| Round-trip stable, ASCII | 33/33 |
| Round-trip stable, binary | **33/33** |
| Framings told apart | **3/3** (ASCII, binary, binary without a header) |
| One message | **244 bytes** binary against 386 ASCII characters |
| Messages carrying chip data | 12, **132 EMV tags** parsed, 11 distinct, 0 unknown |
| Faults injected | 38 |
| Killed | **38/38** |
| Killed by the rule written for it | **38/38** |
| Invariants never exercised | none |
| Random corruptions, illegal for the field | **651/651 rejected** |
| Random corruptions, legal for the field | 767/767 accepted, as they should be |
| Random corruptions, in the framing | 567/582 rejected (97.4%) |
| Random byte damage to the binary form | **659/1000 rejected**, 13 of them by the length header |

A 38/38 kill rate is coverage of *known* fault classes, because the catalogue is
hand-written. The random-corruption pass is the honest complement: 2,000 single-character
corruptions of a valid message, split into the three populations that have three different
correct answers. Swapping one digit of an amount for another digit produces a different but
entirely valid message, and a parser that rejected it would be wrong.

**Why the framing figure cannot reach 100%.** Of the 15 accepted corruptions, 10 turned a
valid MTI into another valid MTI &mdash; `0200` into `0400`, `0810` into `0710` &mdash;
which a parser must accept. Four flipped a bitmap bit and swapped one element for another
of the same width, so the layout stays byte-identical and the message re-parses as a
different, well-formed message. One changed a length prefix in a way that still consumed
the message exactly.

None of that is a parser defect. An ISO 8583 bitmap carries no redundancy and neither does
a length prefix, so nothing at the framing layer can tell those cases from legitimate
traffic. It is the reason the invariant layer exists instead of stopping at the codec: an
MTI that becomes `0400` has become a reversal with no DE 90, and **L06** catches that even
though the codec cannot.

## The defect the chip parser exists for

Most decoders print DE 55 as one hex blob. Inside it, tag 9F02 is the amount **the chip
authorised**, and it has to equal DE 4. When a terminal or a switch rewrites one and not
the other, the message still parses, every field is individually well-formed, and the
cardholder is debited an amount the card never approved.

```
  INPUT   DE 4 doubled, with the chip's own 9F02 left alone
  every field still well-formed  True
  the message still parses       True
    caught by  chip tag 9F02 authorised 1,000,000 minor units but DE 4 asks for
               2,000,000 - the cardholder would be debited an amount the chip
               never approved
```

That is rule **L20**, and nothing below the cross-field layer can see it.

## Why binary framing is harder to damage silently

The random-byte pass over binary messages rejects 65.9% against 60.9% for the ASCII form,
and the reason has nothing to do with this project's code. A packed numeric field has four
bits per nibble and only ten of the sixteen values are legal, and the length header has to
agree with the number of bytes after it. The same damage an ASCII message absorbs silently
tends to be caught here. What survives is the same residue as before: a byte landing in a
text field, or in the chip blob, where any value is legal.

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

Overview (the fault catalogue and the corruption breakdown), Decode, Build a flow,
Validate pasted traffic, and the rule list with the dictionary.

The Decode tab takes either framing and will work it out for itself, showing the reasoning
rather than silently picking one. Click any field row to highlight that field inside the
raw message; when the input was binary it is re-serialised to the ASCII form first, so the
offsets line up with characters that can be counted. DE 55 is expanded tag by tag, with
its cross-field checks against DE 4 and DE 49 stated above the table.

A message that will not parse still gets an answer: the error text, how far the parser got,
and the MTI and bitmap it could still read. A workbench that says only "invalid" when handed
real broken traffic is useless.
