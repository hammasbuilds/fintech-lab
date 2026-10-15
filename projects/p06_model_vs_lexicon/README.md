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

From [`results.json`](results.json). Models: **qwen2.5:14b-instruct**. 0 replies generated, 0 served from the committed cache, 0.0s of wall clock, 0 answers cut off by the token cap.

> **146 case(s) have no cached reply and were not run**, so the tables below are not a full sweep. Re-run with `--live` and a local model. They are listed in `results.json` under `incomplete`.

### The same cases, both ways

| Arm | Amounts | Resolved | Asked | Verdicts | Blind spot | False blocks |
|---|---:|---:|---:|---:|---:|---:|
| **project 03's rules** | 20/20 | 20/20 | 10/10 | 23/23 | 0/4 | 0/5 |

Every cell is correct over attempted. The rules row is the shipped product on the same cases through the same grader.

<!-- numbers:end -->

## Reproducing it without a GPU

Every reply is cached and the cache is committed:

```bash
uv run python -m projects.p06_model_vs_lexicon.benchmark      # cache only
```

The rules column needs no model at all, so it is always present. A prompt with no cached
reply is reported as **not run** rather than scored.
