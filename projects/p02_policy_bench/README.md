# 02 &mdash; Policy Bench

A sandbox for nano-lending credit policy. Write the policy in a language a product owner can
read, compile it to something executable, and see what it would have done to a borrower
cohort before it goes live &mdash; approval rate, bad rate, the money, who it quietly
excludes, and the notice each declined applicant would receive in their own language.

`make serve02` &rarr; <http://127.0.0.1:8102> &middot; `make bench02` to re-measure.

## The policy language

The policy text is the artefact. It compiles to a syntax tree, the tree is what runs, and
the tree prints back to the same text &mdash; so the document the credit team argues over
and the thing that decides applications cannot drift apart.

```
policy "Nano loan v2"
version 2
currency PKR

decline if monthly_income < 18000 because "monthly income below the floor of 18,000"
decline if txn_velocity > 120 because "wallet turnover too high to read as income"
refer if dependants >= 6 because "a large household needs an officer to size the limit"

score +20 if wallet_months >= 6 because "six months of wallet history"
score -15 if txn_velocity > 80 because "turnover high enough to blur the income signal"
score +10 if employment == "salaried" because "a salary lands on a predictable date"

approve if score >= 45
limit min(0.25 * monthly_income, 20000)
fee 2.5% of limit
```

All three bundled policies round-trip through the parser. Every error carries a line number
and says what was expected: a policy language whose parser reports "syntax error" has failed
at the only job that makes it better than a spreadsheet. The compiler also refuses a policy
that can never approve anyone, which is otherwise a silent configuration mistake.

## The numbers

5,000 synthetic borrowers, base default rate 5.86%. From [`results.json`](results.json).

| Policy | Approvals | Bad rate | Disbursed | Net | Lint |
|---|---:|---:|---:|---:|---|
| Nano loan v1 | 56.7% | 2.61% | 26,984,871 | 221,531 | clean |
| Nano loan v2 | 31.9% | 1.69% | 17,167,846 | 229,925 | clean |
| Nano loan v3 | 31.9% | 1.69% | 17,167,846 | 229,925 | **1 critical** |

### What the v1 &rarr; v2 tightening bought

| | |
|---|---:|
| Newly declined | **1,238** |
| Of those, would have defaulted | **47** |
| Of those, would have repaid | **1,191** |
| Precision of the tightening | **3.8%** |
| Approval rate | &minus;24.76 points |
| Bad rate | &minus;0.92 points |
| Net | +8,394 PKR |
| Disbursed | &minus;9,817,026 PKR |

The tightening works and it costs 1,191 good borrowers to avoid 47. Bad rate improved and
net barely moved while disbursement fell by 9.8 million. **An approval-rate chart shows the
first half of that sentence only**, which is the whole reason a version comparison here
reports approvals, bad rate *and* money together.

v3 is v2 with the late fee re-expressed per day. It changes **0** credit decisions and earns
one critical lint note, so the only thing it alters is the basis of the charge.

### Fairness, v2 by region

| | |
|---|---:|
| Raw approval-rate gap | **39.0%** (central-urban vs east-rural) |
| Gap within income bands | **17.0%** |

No policy here mentions region. The gap falls by more than half once income is held roughly
still, so most of it is income reaching the decision through the income rules rather than
region itself &mdash; a product question, not a defect in the rules. A parity table alone
would have reported the 39% and stopped.

The module says in its own output that this is a measurement and not a compliance verdict.
It cannot see whether a variable is a legitimate business necessity, and it does not pretend
to.

## The compliance lint

Eight structural checks on the policy text, read from the tree rather than the output, so
they catch a problem in a policy that has never been run.

The Shariah check (**C1**) flags a charge priced **per unit of time**: a fee that grows with
the number of days the money is outstanding is a return on lending rather than a charge for
a service. It is a lint on the structure of the charge and nothing more &mdash; it cannot
rule on a product and it is not a fatwa. The verdict object states that scope itself, so a
clean result cannot be quoted as "compliant".

## Decline notices

Generated from the rule that fired, in English and Roman Urdu, template-based so the Urdu
cannot say anything the rule does not.

The first version of this listed the unmet scoring rules under the same heading as an
approval's reasons, which produced "not approved because your income is above the floor"
&mdash; the opposite of what happened. Unmet requirements are now a separate list, each
marked unmet and carrying what it was worth. A decline notice is a regulated communication
and does not get to be ambiguous about which direction a reason points.

## The cohort

Synthetic, seeded, and the generator is written out in full in [`cohort.py`](cohort.py) so a
reader can see what was assumed and therefore which conclusions would be circular. It
deliberately contains three things a policy sandbox has to be able to show: income
correlated with region, wallet turnover that is not income, and repayment driven partly by
factors no policy can see.

**Every number here is a statement about that generator, not about a market.**
