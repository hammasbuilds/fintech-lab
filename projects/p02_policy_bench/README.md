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

50,000 synthetic borrowers, base default rate 5.86%. From [`results.json`](results.json).

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

### Where the cutoff should be

The first round found that the tightening cut mostly good borrowers and left the obvious
follow-up unanswered. A sweep answers it: score every borrower once, then re-decide at
every candidate cutoff.

| | |
|---|---:|
| The policy's own cutoff | 45, net **229,924 PKR** |
| Net-maximising cutoff | **40**, net **255,108 PKR** |
| Available by moving it | **+184,716 PKR** |

And it explains the earlier finding rather than repeating it. The scorecard's
discrimination is reported separately, because moving a cutoff cannot change it:

| | |
|---|---:|
| Gini | **0.233** |
| AUC | 0.642 |
| KS | 0.259 (at score 35) |
| Measured on | 2,531 scored; 2,469 stopped by a gate first |

**Weak separation. A cutoff on this score will cut good and bad alike** &mdash; which is
precisely why the v1&rarr;v2 tightening had a precision of 3.8%. The problem was never the
cutoff; it is a scorecard that barely ranks.

Two cautions are printed with those numbers rather than left to the reader:

- **The net-maximising cutoff is in-sample.** It is chosen on the same borrowers it is
  scored against, so it is the best cutoff for this cohort's past and an optimistic
  estimate for anyone it has not seen. The shape of the curve is the finding, not the
  argmax.
- **Discrimination is measured on the scored population only.** The 2,469 applicants a
  gate stopped never received a score, so including them would be measuring the gates and
  calling it the scorecard. Excluding them flatters the figure whenever the gates are
  removing bad borrowers, which is what gates are for.

The sweep also reports approval rate against **both** denominators, named: the share of
those who reached the scorecard, which the cutoff controls, and the share of all
applicants, which the rest of this project reports. One unlabelled "approval rate" across
the two is how a 63% and a 32% end up in the same conversation.

### Your own borrowers

A cohort can be pasted in as CSV, so the sandbox can be pointed at a real portfolio.
The outcome column is optional, and that difference is enforced rather than papered over:

| | |
|---|---|
| CSV round trip | exact |
| Outcome column present | bad rate, expected loss and tightening precision all available |
| Outcome column absent | **7 figures withheld** rather than computed against zeros |

Without a label there is no bad rate, no expected loss, no net and no precision, because
every one of those is computed against a known outcome. Filling the gap with zeros and
reporting a 0% bad rate is the worst available option, so the report returns them empty
with a line saying why.

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

The bundled one is synthetic, seeded, and the generator is written out in full in
[`cohort.py`](cohort.py) so a reader can see what was assumed and therefore which
conclusions would be circular. It
deliberately contains three things a policy sandbox has to be able to show: income
correlated with region, wallet turnover that is not income, and repayment driven partly by
factors no policy can see.

**Every number here is a statement about that generator, not about a market.** Load your
own cohort to get numbers about something real.

## At full size, on two real books

The cohort above is generated, and a generated cohort can only tell you about its own
generator. These numbers are the same policy engine, the same backtest and the same cutoff
sweep run over **255,347 real loan applications with the outcomes that actually happened**,
and over **438,557 real card applications of which only 8.3% have any repayment history** -
which is the shape a credit team actually has to work with.

Both are public Kaggle files, fetched by `make data` and never committed. The field map for
each one is written out in `loanbook.py` and `cardbook.py`, including the two derivations
and the two real-data traps that are handled rather than averaged over.

<!-- datasets:start -->
| Dataset | Rows | Size | Role | Source |
| --- | ---: | ---: | --- | --- |
| `real/loan_book` | 255,347 | 24 MB | cohort | kaggle:nikhil1e9/loan-default |
| `real/card_applications` | 438,557 | 52 MB | cohort | kaggle:rikdifos/credit-card-approval-prediction |
| `real/card_credit_records` | 1,048,575 | 15 MB | lookup | kaggle:rikdifos/credit-card-approval-prediction |

| The real loan book | Result | Over |
| --- | ---: | --- |
| base default rate | **0.1161** | 29,653 of 255,347 applications |
| v1 approval rate | **0.2584** | the whole book |
| v1 bad rate among approvals | **0.0679** | 65,987 approvals |
| v1 Gini | **0.2551** | 163,562 scored |
| v2 Gini, price signal removed | **0.1917** | same book, one rule dropped |
| v1 net, one-off 2% fee | **-3834225.85** | negative at every cutoff |
| v3 net, priced over the term | **72098169.15** | and flagged critical by the compliance lint |

| The real card book | Result | Over |
| --- | ---: | --- |
| applications scored | **438,557** | every outcome figure withheld |
| joined to a repayment history | **36,457** | a join rate of 0.0831 |
| pensioners caught by the 365243 sentinel | **75,329** | would otherwise read as -12,175 months of work |
| Gini, bad = ever 30+ days past due | **0.0462** | the 36,457-row slice |
| Gini, bad = ever 60+ days past due | **0.1644** | the same 36,457 rows |

Three findings come out of the real books that the synthetic cohort cannot show. The strongest feature in the loan book is `interest_rate` - the lender's own price for that applicant - and dropping it costs a quarter of the separation, so a scorecard that uses it is partly scoring a decision the lender already made. A one-off 2% service fee loses money at every cutoff, which is why the sweep's net-maximising threshold for v1 and v2 is the one that approves nobody; only pricing over the term earns anything, and that is the version rule C1 flags as critical. And on the card book, two defensible definitions of "bad" on the same 36,457 rows give Gini figures that differ by more than three times - whoever picks the definition picks the result.
<!-- datasets:end -->
