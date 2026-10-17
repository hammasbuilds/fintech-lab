# 04 &mdash; Spec to wire

Can a local model produce payment traffic a switch would accept? This is the model arm of
[project 01](../p01_switchboard/), and project 01 is the oracle: **every grade here is
computed, not judged.** A message either packs or it does not, the 22 invariants either
hold or they name what broke, and the deterministic generator sits beside every case as
the answer.

`make serve04` &rarr; <http://127.0.0.1:8104> &middot; `make bench04` to re-measure (needs a
local model) &middot; `/io` for the input-and-output proof.

## Four task families

In increasing order of how little the model is given.

| Family | The task | How it is graded |
|---|---|---|
| `decode` | Read a raw message and say what it was | Five fields against the codec: MTI, amount, currency, trace, and the transaction type |
| `fields` | Emit the data elements for a scenario as JSON | Packs? Invariants hold? Field-exact agreement with the generator, element by element |
| `wire` | Emit the whole message, bitmap included | Does the parser accept it at all |
| `negative` | Produce traffic that breaks a **named** invariant | Does the checker flag that rule, and only that rule |

`negative` is the one with product value. It asks the model to write a test case, and the
checker says whether the test case works. Full marks need the named rule and nothing else:
a flow that breaks four rules does catch the one, but it is a poor test, because it would
also pass against a switch that enforces only the other three.

## Two arms, identical apart from the prompt

- **bare** &mdash; the task and nothing else. What the model knows about ISO 8583 unaided.
- **spec** &mdash; the same task with the data element dictionary and the invariants pasted
  in.

The question an engineer actually has is whether handing it the specification closes the
gap, so the two arms differ by that and nothing else. The first paragraph of both prompts
is byte-identical, and a test asserts it.

<!-- numbers:start -->

## The numbers

From [`results.json`](results.json). Models: **qwen2.5:14b-instruct**. 9 replies generated, 49 served from the committed cache, 40.1s of wall clock, 0 answers cut off by the token cap.

### Mean score by task family

| Arm | decode | fields | negative | wire |
|---|---:|---:|---:|---:|
| **project 01's generator (code)** | 1.00 | 1.00 | 1.00 | 1.00 |
| qwen2.5:14b-instruct / bare | 0.33 | 0.03 | 0.10 | 0.00 |
| qwen2.5:14b-instruct / spec | 0.45 | 0.25 | 0.40 | 0.00 |

0 to 1. The generator is 1.00 by construction and is shown because a model score with no baseline beside it invites the reader to supply their own idea of what good looks like.

### How many answers were usable at all

| Arm | Parsed | Packs | Invariants hold |
|---|---:|---:|---:|
| qwen2.5:14b-instruct / bare | 19/29 | 2/29 | 0/29 |
| qwen2.5:14b-instruct / spec | 20/29 | 10/29 | 4/29 |

These stay separate on purpose: an unparseable message is not 90% of a message.

<!-- numbers:end -->

## The one result worth the build

Handing the model the specification helps everything **except the task that needs a
computed bitmap**, where it does not help at all:

- `fields`, `negative` and `decode` all improve substantially with the spec in the prompt.
- `wire` is **0.00 in both arms**, and not because the messages were wrong. In 16 of 16
  attempts the server **aborted the prediction**: asked for a complete message, the model
  degenerates into repeating a token until Ollama's repeat guard kills it.

A bitmap is a computed index over which elements are present, not a string to recall, and
nothing in the prompt changes that. The abort is recorded as a score of zero with the
server's own words beside it, rather than as a case that did not run &mdash; it is the most
informative thing the model did.

## What is deliberately excluded, and why

Two of the four models installed on this machine are not in the headline, with the reason
recorded in `results.json` rather than left as a gap:

- **qwen3.6:27b** does not fit this machine's 16 GB alongside anything else, so it runs
  partly on CPU &mdash; measured at about 13 seconds per token &mdash; and it spends its
  token budget on hidden reasoning before emitting any content. Both are properties of the
  setup, not of the model, and either way it cannot be measured here honestly.
- **qwen2.5-coder:14b** is a base model, not instruction-tuned. Asked to reply with `OK` it
  replied `88888888`. Including it would be measuring the wrong thing.

That second one was found the slow way: the first version of the client used the completion
endpoint, which applies no chat template, so an instruction was just text to continue.

## Reproducing it without a GPU

Every reply is cached on disk, keyed by a digest of the model, the prompt, the system
prompt and the sampling options, and **the cache is committed**. So:

```bash
uv run python -m projects.p04_spec_to_wire.benchmark      # no --live: cache only
```

reproduces the published numbers with no model and no GPU. Change a prompt and the digest
changes, so a stale answer can never be served for a new question. A prompt with no cached
reply is reported as **not run** rather than scored &mdash; a benchmark that silently
scores an empty string reports the model as very bad at the task instead of reporting that
it was never asked.

## At full size

The model answers 29 hand-written tasks. Project 01's codec answers **60,000 generated
ones** drawn from the ISO 8583 corpus, in the same four families, because a baseline
measured on 29 cases cannot tell a parser that is right from a parser that is right about
those 29. Both columns are below, and the model's share of the bank is printed next to its
figures so a count is never read as a rate.

<!-- datasets:start -->
| Dataset | Rows | Size | Role | Source |
| --- | ---: | ---: | --- | --- |
| `generated/p04_tasks` | 60,000 | 29 MB | cohort | generated by projects/p04_spec_to_wire/bank.py |

| At full size | Result | Over |
| --- | ---: | --- |
| decode | **1** | 15,000 tasks |
| fields | **1** | 15,000 tasks |
| wire | **1** | 15,000 tasks |
| negative: found a fault | **1** | 15,000 tasks |
| negative: named the injected fault | **0.7999** | same tasks, stricter rule |
| the model's share of the bank | **0.0005** | 29 of 60,000 tasks |

The gap between the last two negative rows is the bitmap family. A flipped bitmap bit shifts the layout, so the field that lands where the amount should be is genuinely not numeric - and an ISO 8583 bitmap carries no redundancy, so nothing downstream can say which bit moved. Calling that message malformed is correct; demanding the word "bitmap" would be scoring a guess. Two faults were also removed from the bank for not surviving the packer: a two-digit trace number comes back padded to six, so the task asked about damage the message no longer carried.
<!-- datasets:end -->
