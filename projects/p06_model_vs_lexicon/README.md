# 06 &mdash; Model against lexicon

The model arm of [project 03](../p03_guardian/), on **the same cases with the same
grader**. Project 03 resolves code-mixed Urdu into one concrete action with a lexicon and a
grammar, and publishes what that costs: it misses every scam phrased in ordinary language.
This asks a local model to do the same job, so the comparison is a comparison rather than
two differently-generous evaluations.

`make serve06` &rarr; <http://127.0.0.1:8106> &middot; `make bench06` to re-measure (needs a
local model) &middot; `/io` for the input-and-output proof.

## Four things, and they do not move together

| Column | What it is |
|---|---|
| **amounts** | `saat hazaar` against `saath hazaar`, `sawa lakh`, `paune do lakh`. Being out by a factor of ten is a wrong transfer. |
| **resolved** | The amount, the beneficiary and the intent on utterances that determine an action. Graded on the resulting action, never on the wording. |
| **asked** | Under-specified utterances the answer stopped and asked about. A zero here with a high *resolved* is not a better assistant; it is one that guesses at who to pay. |
| **verdicts** | The coercion call on 23 messages, including the four the lexicon provably misses. |
| **blind spot** | Those four. The rules score **0/4** by construction &mdash; the one place a model has room to win. |
| **false blocks** | What winning there costs on ordinary traffic. Read with the column before it, never alone. |

## The baseline is the shipped product, not a strawman

The rules column is project 03's own layers run over the same corpus by the same grader.
It scores 48/48 on the full amount set, 30/30 on the actions, 23/23 on the verdicts, and
**0/4** on the scams it cannot see. Only the thing that answers differs.

One asymmetry is deliberate and is reported rather than smoothed over: **a reply that does
not come back as usable JSON is scored wrong, not skipped.** A reply that cannot be parsed
cannot drive a transfer, and dropping those cases would grade the subset that happened to
be formatted correctly.

## Two arms, and why the second one exists

- **bare** &mdash; the task, the contact list, and the output shape. No vocabulary.
- **taught** &mdash; the same prompt plus the conventions the rules encode: that `hazaar`
  is a thousand and `lakh` a hundred thousand, that `sawa`/`paune`/`saadhe` shift the
  number that follows, that `saat` and `saath` differ by a factor of ten, and that an
  ambiguous beneficiary must be asked about rather than chosen.

The `taught` prompt contains the `bare` prompt verbatim, and a test asserts it. That arm
exists because of an earlier result worth not repeating: a zero-shot rung can score near
zero for the plain reason that the task's own vocabulary was never stated, and the fair
question is whether a model *told* the conventions can then apply them.

<!-- numbers:start -->

## The numbers

From [`results.json`](results.json). Models: **qwen2.5:14b-instruct**. 65 replies generated, 65 served from the committed cache, 93.7s of wall clock, 0 answers cut off by the token cap.

### The same cases, both ways

| Arm | Amounts | Resolved | Asked | Verdicts | Blind spot | False blocks |
|---|---:|---:|---:|---:|---:|---:|
| **project 03's rules** | 12/12 | 20/20 | 10/10 | 23/23 | 0/4 | 0/5 |
| qwen2.5:14b-instruct / bare | 12/12 | 16/20 | 6/10 | 13/23 | 2/4 | 0/5 |
| qwen2.5:14b-instruct / taught | 12/12 | 17/20 | 7/10 | 13/23 | 1/4 | 0/5 |

Every cell is correct over attempted. The rules row is the shipped product on the same cases through the same grader.

<!-- numbers:end -->

## What these numbers will and will not carry

**The blind-spot column has four cases in it.** A model catching two of them where the
rules catch none says a non-lexical reading finds *something* a lexicon cannot; it does not
rank the two arms, and a 2-against-1 difference between them is one case. With n=4 that is
noise, and treating it as a result would be exactly the mistake this project is built to
avoid. The honest summary is: a model recovers some of what the lexicon misses, here at no
cost in false blocks, and the sample is far too small to say how much.

The columns that *are* large enough to read go the other way. The rules resolve every
determined utterance and ask on every under-specified one; the model resolves fewer and
**guesses where it should ask**, which is the failure that moves money to the wrong person.
On the coercion verdicts overall the rules are exact and the model is not, because most of
those cases are ones the lexicon was written for.

## Reproducing it without a GPU

Every reply is cached and the cache is committed:

```bash
uv run python -m projects.p06_model_vs_lexicon.benchmark      # cache only
```

The rules column needs no model at all, so it is always present. A prompt with no cached
reply is reported as **not run** rather than scored.

## At full size

This project has no dataset of its own and should not have one: its question is how the
rules and a model compare **on the same cases**, so it uses project 03's generated set of
200,000 labelled utterances, scored exactly as project 03 scores it. The rules answer all
of them. The model answers 65.

<!-- datasets:start -->
| Dataset | Rows | Size | Role | Source |
| --- | ---: | ---: | --- | --- |
| `generated/p03_utterances` | 200,000 | 70 MB | cohort | generated by projects/p03_guardian/generator.py |

| The rules, on the whole set | Result | Over |
| --- | ---: | --- |
| amounts exact | **0.9939** | the cases carrying an amount |
| intent accuracy | **0.9746** | the cases with an intent |
| coercion caught | **0.9609** | 40,000 scripts |
| ordinary traffic blocked | **0.0211** | 25,000 messages |
| the model's share of the set | **0.0003** | 65 of 200,000 cases |

The asymmetry is the point rather than an omission. A lexicon and a grammar answer 200,000 cases in about a minute; 200,000 local model calls is a fortnight of GPU time for a result whose shape is visible at 65. What the comparison cannot say is which side is better at the blind spot, because the blind spot is four cases - and scaling that family needs new hand-written scams rather than generated ones, since a generator able to write them would have to know which words the lexicon lacks, and would then be writing the answer key.
<!-- datasets:end -->
