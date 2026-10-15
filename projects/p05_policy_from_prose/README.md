# 05 &mdash; Policy from prose

Can a model turn a written credit policy into one that runs? This is the model arm of
[project 02](../p02_policy_bench/). Project 02's point is that the document the credit team
argues over and the thing that decides applications are the same artefact; getting there
still means someone writing the policy in the language by hand, and this asks whether a
model can do that step.

`make serve05` &rarr; <http://127.0.0.1:8105> &middot; `make bench05` to re-measure (needs a
local model) &middot; `/io` for the input-and-output proof.

## The grade is not "does it look right"

A compiled policy can be run, so the grader runs it:

- does it **compile** at all, and if not, on which line;
- does it **behave identically** to the hand-written reference over all **5,000**
  borrowers &mdash; decision for decision, limit for limit;
- when it differs, on how many borrowers, and in which direction.

That last column is the one that matters. **A policy that compiles, reads sensibly and
approves a different 4% of applicants is the dangerous output here**, and it is invisible
to any check that stops at the text. A policy that does not compile scores zero rather
than partial credit: it has no behaviour to agree with.

## Five policies in prose, three of them awkward on purpose

| Case | What makes it hard |
|---|---|
| `starter` | Nothing. Every number is a digit and every rule is a sentence. |
| `thresholds` | Every threshold is in words &mdash; "at least eighteen thousand". A transcription error here is a credit decision. |
| `contradiction` | Two overlapping gates, added by different people without removing the first. Keeping both is faithful; silently merging them changes the reason an applicant is given. |
| `nano_v1` | The real v1 policy, described in prose. |
| `nano_v2` | A change described as a diff against another policy, which is how they actually arrive. |

## The second task: the notice

Given the rule that fired, write the sentence the applicant receives. The checks are
keyword and pattern checks and the project says so &mdash; they can tell that a notice
mentions income when the rule was about defaults, and that it contains a promise; they
cannot tell whether the sentence is any good. The two that matter:

- **invented a reason** &mdash; it gave the applicant a reason the policy did not.
- **promised something** &mdash; it implied a future approval.

Project 02's templates pass every check by construction, which is what makes the model
column readable. Validating the grader against them first caught two real bugs in it: an
approval's own stated reasons were being counted as "invented", and the Roman Urdu phrase
for a *repaid* loan matched the keyword list for a *default*.

<!-- numbers:start -->

## The numbers

From [`results.json`](results.json). Models: **qwen2.5:14b-instruct**. 20 replies generated, 0 served from the committed cache, 74.1s of wall clock, 0 answers cut off by the token cap.

### Turning prose into a policy that runs

| Arm | Compiles | Identical behaviour | Mean agreement | Compiled but different |
|---|---:|---:|---:|---:|
| **project 02's own policies** | 5/5 | 5/5 | 1.000 | 0 |
| qwen2.5:14b-instruct / bare | 0/5 | 0/5 | 0.000 | 0 |
| qwen2.5:14b-instruct / spec | 4/5 | 4/5 | 0.800 | 0 |

Agreement is over all the borrowers in the cohort. **Compiled but different** is the column that matters: it parsed, it ran, and it decided a different set of applicants than the document it came from.

### The decline and approval notices

| Arm | Mean checks | All checks | Safe | Invented a reason | Promised something |
|---|---:|---:|---:|---:|---:|
| **project 02's templates** | 1.00 | 5/5 | 5/5 | 0 | 0 |
| qwen2.5:14b-instruct / bare | 0.94 | 3/5 | 5/5 | 0 | 0 |
| qwen2.5:14b-instruct / spec | 0.94 | 3/5 | 5/5 | 0 | 0 |

<!-- numbers:end -->

## The result contradicted the expectation

This project was built around the fear that a model would produce a policy which compiles,
reads sensibly, and quietly approves a different set of applicants. **That is not what
happened.** Of the policies that compiled with the grammar in the prompt, every one decided
all 5,000 borrowers *identically* to the hand-written reference &mdash; the
"compiled but different" column is zero.

The failure mode is the loud one instead: without the grammar nothing compiles at all, and
with it one case still fails outright with a located parse error. That is a better
situation than the one feared, because a compiler error stops the policy from shipping and
silent drift does not &mdash; but it is worth saying plainly that the measurement went
against the hypothesis rather than for it.

Five policies is a small sample, and one of them failing to compile is one case. The column
to keep watching on a larger set is still the silent one.

## Reproducing it without a GPU

Every reply is cached and the cache is committed:

```bash
uv run python -m projects.p05_policy_from_prose.benchmark      # cache only
```

A prompt with no cached reply is reported as **not run** rather than scored.
