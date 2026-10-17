# What these products did not do, what the datasets were missing, and what each one measured

Six projects, each measured on between 50,000 and a million rows. The numbers were good.
That is exactly when it is worth asking the harder question: **what does the industry do
that these products have no opinion about, and what is absent from the datasets that would
have caught it?**

Each gap below says what the industry requires, what the product and its dataset did,
what changed, and - the part that matters - **what the change measured**. Where a gap is
left open, it says why.

Sources are linked per item. Where a search could not produce a primary source - Pakistan's
instant-payment message specifications are not public - the gap is recorded as unverified
rather than guessed at.

The short version: **nine defects in the products**, found by traffic that had never been
put through them, and **one number that got worse on purpose** - project 03's lexical blind
spot shrank from four cases to three, so a figure this repo had been publishing as a
weakness is now smaller and the write-ups say so.

---

## 01 Switchboard - ISO 8583

### Gap 1.1 - Only authorisation traffic existed. A switch spends most of its life on the rest.

The corpus was 120,120 messages over 14 scenarios, and every one was an authorisation, a
response or a reversal: MTI classes 01xx, 02xx, 04xx and 08xx. A real switch also carries
**advice** messages for transactions that already happened, **reconciliation** at cutover
where the two sides compare totals, and **chargeback** traffic where disputes live. A
switch that authorises perfectly and reconciles wrongly loses money quietly for a day at a
time, because nothing declines.

**Closed, and it found four defects on the way in.** Three scenarios were added -
stand-in authorisation advice, end-of-day reconciliation, chargeback and representment -
and a fourth, a duplicate trace number inside one window, is kept as a *known-invalid*
fixture rather than in the corpus, because every scenario in the corpus has to be valid
by construction for a finding to mean "false positive". Building them broke four things:

1. **The dictionary had no reconciliation elements.** DE 74, 75, 76, 77 and 86-89 - the
   counts and values a cutover message exists to carry - were absent, so the codec refused
   to pack a 0500 at all. The entire 05xx class was unreachable.
2. **Function code 5 was missing from the MTI table**, so 0450 was "reserved" and a
   chargeback had no response the codec would build. One digit, and the dispute path did
   not exist.
3. **Request/response pairing used `is_request`**, which is true only for requests and
   advices. A chargeback is a *notification*, so every 0440 landed in the response pile,
   matched nothing, and the flow reported four invariant breaches for existing. Pairing
   now uses `expects_answer`.
4. **L01 ("a request is answered or reversed") had no concept of an advice.** In the
   stand-in scenario the issuer is unreachable by construction, so nothing answers the
   request - the advice reports it afterwards and quotes it in DE 90. The rule now accepts
   that, and **L12** was scoped off advice responses, which are acknowledgements and carry
   no authorisation id.

The corpus is now **160,160 messages over 61,880 lifecycles**, and all 17 scenarios are
clean under all 22 invariants - zero findings, where every finding would be a false
positive by construction.

### Gap 1.2 - Every amount in the corpus behaved as if it had two decimal places.

DE 4 is an amount in the **minor units of the currency in DE 49**, and the number of minor
units is not always two: ISO 4217 gives 0 for JPY and KRW and 3 for the Gulf dinars. The
narrator already knew this and had an exponent table - what it had never seen was a message
in a currency with any exponent but 2, because the corpus drew from five currencies and all
five were 2-decimal.

**Closed.** The table gained KWD, BHD, OMR, JOD and KRW; the corpus draws from eight
currencies across **three different exponents** and scales each amount into its own minor
units, so the same purchase is 500,000 units in rupees and 5,000 in yen. The whole class of
"divide by 100" bugs is now reachable by the fuzz and invariant passes rather than
invisible to them.

### Gap 1.3 - ISO 20022 is not here, and the industry has finished moving.

SWIFT's MT/MX coexistence ended on **22 November 2025**; cross-border payments on that
network are ISO 20022 only, and messages in the old format are rejected or routed through a
contingency path with stricter validation and extra charges
([ACI](https://www.aciworldwide.com/blog/the-end-of-coexistence-is-near-banks-take-an-existential-leap-towards-iso-20022),
[dps.de](https://dps.de/en/news/iso-20022-only-100-days-left-until-the-end-of-mt-mx-coexistence/)).
Card authorisation is still ISO 8583 and will be for years, but the rails next to it are
not, and a payments workbench that cannot show one transaction in both formats describes
half the estate.

**Left open, deliberately.** A credible `pacs.008` implementation is a project, not a
feature, and a hand-waved XML template with no schema validation would be worse than
nothing. It is written down here as the largest known gap in project 01.

### Gap 1.4 - Pakistan's own rails are not represented.

Raast and 1LINK's IBFT are the rails this repo's framing implies, and their message
specifications are not published. Searching returned other countries' ISO 20022 instant
payment profiles and nothing authoritative for either.

**Left open, recorded as unverified** rather than approximated from a neighbour's spec.

---

## 02 Policy Bench - lending policy

### Gap 2.1 - The product had no idea what the regulator caps, and nano lending is capped hard.

This was the largest gap of the six, and the project's own framing invites it. Digital nano
lending in Pakistan is regulated by the **SECP**, not the State Bank, and **Circular 10 of
2023** with the pricing circular that followed set limits the engine could not express
([circular](https://www.secp.gov.pk/document/circular-no-10-of-2023-requirements-for-lending-nbfcs-providing-digital-nano-lending),
[August 2023](https://www.secp.gov.pk/wp-content/uploads/2023/08/Press-Release-Aug-7-SECP-strengthens-measures-to-prevent-predatory-lending-in-Digital-Nano-Lending.pdf),
[September 2023](https://www.secp.gov.pk/wp-content/uploads/2023/09/Press-Release-Sep-25-SECP-imposes-pricing-caps-on-digital-personal-loans.pdf)):

| Rule | Limit |
| --- | --- |
| One borrower, one app | **Rs 25,000** |
| One borrower, all apps together | **Rs 75,000** |
| Tenor | **90 days**, later reduced to **30** |
| Total recoveries - markup, fees, penalties | **not more than the principal** |
| APR | at most **10x the policy rate** |

The shipped policies sat under the single-app cap by arithmetic accident, and nothing in
the engine, the lint or the cohort knew about the others. There was **no way to see another
app's loan**, so the aggregate cap could not be checked even in principle, and the cohort
had no tenor at all.

**Closed.** Five regulatory checks (R1-R5) joined the eight written-form ones; the cohort
carries `other_app_loans`, `other_app_outstanding` and `tenor_days`; and two policies were
added - `nano_v4_compliant`, which writes the caps into the policy, and `nano_v5_overcap`,
which breaks every one of them and is kept as a fixture, because a lint with nothing to
catch has not been shown to work.

What it measured, on 50,000 borrowers: **every shipped policy earns an R2** - none of them
can see another app's loan - and 5.0% of applicants are carrying enough from other apps
that a legal 20,000 loan would break the 75,000 aggregate. `nano_v4` gates on exactly that
and approves 22.0% at a 2.4% bad rate; `nano_v5` approves 71.8% at 7.1% and trips five
rules at once. R5 reports an APR and the minimum policy rate that would make it legal
rather than holding a rate that would go stale.

### Gap 2.2 - No time. Nothing had a date, so half of credit practice was impossible.

Every borrower existed at the same instant, which rules out out-of-time validation, vintage
curves and population stability.

**Closed.** The cohort has an origination month over a **24-month window** with a drifting
population, and a `months_to_default`. Four analyses are now possible and all four are
reported:

- **Vintage curves** over 8 origination quarters and 10,990 approved loans. The oldest
  vintage reaches 1.86% by month 12; the youngest shows 1.30% at month 3 because that is
  all the book it has. Reading those two as a trend is the mistake the curves exist to
  prevent.
- **Population stability.** PSI on the score is **0.0043** - "stable" by the conventional
  reading - while PSI on `other_app_outstanding` is **0.09**, twenty times larger. A
  scorecard turns several fields into one coarse number, so a population shift that is
  plain in the inputs can be nearly invisible in the score. Reporting only the score's PSI
  is how a drifting population gets called stable, and that is why the characteristic-level
  table is printed next to it.
- **Out-of-time.** The cutoff chosen on months 0-17 earns **110.25 per loan** on months
  18-23 against the **103.88** the early window promised - an optimism of **-6.38**, which
  is to say the later book did *better*. That is not the usual direction and the reason is
  worth the ink: the population drifts towards borrowers already carrying another app's
  loan, `nano_v4` gates on exactly that, and the applicants who survive are more selected.
  What falls is volume, not quality.
- **Approvals-only.** 78.0% of the population has no observable outcome once the policy has
  declined them, and the approved bad rate (2.15%) is a quarter of the population's
  (8.51%). That gap is the size of the bias reject inference argues about.

### Gap 2.3 - The cohort is everybody. A real book only knows about who was approved.

**Partly closed**, by the approvals-only view above: the size of the censoring is now a
published number. Inference methods themselves are left out on purpose - they would be the
project's opinion presented as data.

---

## 03 Guardian - Roman Urdu wallet assistant

### Gap 3.1 - The commonest scam in the published statistics was not in the test set.

The seven families were OTP solicitation, staff impersonation, urgency, threat, remote
access, lure and account redirect. Published authorised-push-payment breakdowns put
**purchase scams** first by a wide margin - one summary of the UK regulator's data puts them
at **68%** of cases, impersonation at 10%, advance fee at 9% and investment at 6%
([PSR summary](https://www.crowdfundinsider.com/?p=234318),
[UK Finance categories](https://www.harpendenbs.co.uk/wp-content/uploads/2026/04/Issue-3-UK-Finance-Fraud-Report.pdf),
[Deloitte on US losses](https://www.deloitte.com/us/en/insights/industry/financial-services/authorized-push-payment-fraud.html)).
A purchase scam carries none of the markers this product looked for: nobody asks for a
code, nobody impersonates the bank, nobody threatens. The victim is buying a phone that
does not exist and is perfectly willing.

**Closed.** Five families added - purchase, investment, romance, advance fee and invoice
redirection - with the generated mix weighted to match the published breakdown rather than
split evenly, and **four scam types the product has no words for at all** (job offers, QR
swaps, SIM swaps, refund overpayment) generated on purpose and labelled as outside the
vocabulary.

What it measured, over 45,000 scam scripts: **0.986** for the twelve families the product
has words for, and **0.082** for the four it does not, over 5,542 cases. The blended figure
is 0.874 and is the least useful of the three, which is why all three are printed. That 8.2% is the blind spot measured instead of
asserted, and a generator cannot close it - writing the missing words would be writing the
answer key.

**One number moved the wrong way, and the write-ups say so.** The purchase-scam family
reads "token amount", which is one of the four hand-written cases this repo had been
publishing as a lexical blind spot. The blind spot is now **three cases, not four**; the
case stayed in the test set with its real expectation rather than being deleted, and the
behavioural layer still escalates it, so both layers now catch it.

### Gap 3.2 - Obfuscation was untested, and it is the first thing a real scammer tries.

Every script spelled its giveaway correctly. A scammer who has been blocked once writes
`0tp`, `o.t.p`, `ot p`, `O-T-P`.

**Closed.** The matcher reads a second, de-obfuscated copy of the message for the security
vocabulary only - collapsing separators everywhere would join words that were never one
word, and "pin" is inside "pindi". The generator produces all five techniques and records
which was used.

Measured: **0.988 to 0.997** caught per technique over 1,842 obfuscated scripts, and
"pindi" is still not a PIN.

### Gap 3.3 - Urdu script has more than one way to write the same letter.

Text arrives with Arabic codepoints mixed in - `ي` for `ی`, `ك` for `ک` - plus zero-width
joiners. The normaliser folded Urdu digits and nothing else, so a lexicon written in Urdu
never matched a message pasted from elsewhere.

**Closed.** The normaliser folds the standard Arabic-to-Urdu substitutions and strips
zero-width marks. The first measurement over 4,762 Urdu-script scripts was **0.880** in
ordinary Urdu and **0.867** with Arabic codepoints - and the gap between those two and
1.000 was a second finding: the account-redirect family had exactly one Urdu entry, so the
Urdu phrasings of it went uncaught whichever codepoints they used. With three Urdu entries
added, both spellings are at **1.000**, and the false-block rate on ordinary traffic is
unchanged. Before the fold, the arabised spelling matched nothing at all - disabling it in
a single assertion shows it returning no families.

### Gap 3.4 - Wallet limits and structuring. No account level, no sequences.

Branchless banking wallets are tiered and the tiers are limits: reporting on the 2016
revision gives Level 0 **Rs 25,000** daily and **Rs 40,000** monthly, Level 1 Rs 50,000 and
Rs 80,000 ([ProPakistani](https://propakistani.pk/2016/07/14/sbp-revises-transactions-limits-for-branchless-banking-accounts/),
[SBP 2011](https://www.sbp.org.pk/press/2011/Regulations-20-Jun-11.pdf); a bank's own page
lists a different monthly figure, so the current numbers need the live circular). The
behavioural layer had a daily cap and the dataset had no levels and no sequences, so
**structuring** could not appear in it.

**Closed, and it found the gap it was built to find.** The generator emits splits in two
shapes: `same_day`, where the day's total crosses the daily cap, and `across_days`, where
no day comes near it and the *month* goes over. Measured over 1,041 splits with the payee
already known, so the first-time-payee signal cannot do the work:

| Split | Caught before the change | Caught after |
| --- | ---: | ---: |
| same day, crosses the daily cap | 1.000 | 1.000 |
| spread over days, crosses the monthly cap | **0.000** | **0.987** |

The product gained a monthly-cap signal, and the false-block rate on 25,000 ordinary
messages is **unchanged at 0.0211** - which is the half of that trade worth checking.

### Gap 3.5 - Every utterance was a single turn.

A wallet conversation is a sequence, and the confirmation spine is built for exactly that.

**Closed, and it found a real defect.** The session asked "which Imran?" and then read the
answer as a brand-new command, so a bare name came back as "I did not understand that".
Over 1,667 generated conversations only **53** completed. The session now fills the gap it
just asked about - and only that gap, and only when the answer resolves it outright, so
"nahi, rehne do" is still not a payee.

After the fix: **72.9%** of threads are handled correctly, **0** executed after a no, and
358 were correctly asked to strengthen a plain "haan" into "confirm" because the
behavioural layer had escalated them. The remaining 452 are counted as misses rather than
explained away.

---

## 04, 05 and 06

04 and 06 inherit their datasets from 01 and 03: the wire bank regenerates from the widened
corpus - three currency exponents and four new message classes - and project 06 is scored
on project 03's set, so every family and obfuscation above flows into it.

### Gap 5.1 - The policy bank only generated policies the language can already express.

60,000 policies, 100% of which compile. A strong property result and a weak coverage one:
it says the parser can read everything the printer writes, and nothing about what a real
policy document contains that the language cannot say.

**Closed.** `inexpressible.py` holds **14 requirements** taken from how credit policies are
actually written - effective dates, segment cascades, an exceptions queue, an override with
an approver, affordability across lenders, a cooling-off period, stepped limits, risk-based
pricing, portfolio limits, a Murabaha structure, co-borrowers, a citation on a rule - each
with the construct it would need and the workaround a team would reach for instead. Every
one is run through the real compiler by a test.

They split in two, and the split is the finding: **12 are refused by the grammar**, which
is the safe failure, and **2 compile, pass review and then fail on the first applicant**
because the field they name does not exist. The second kind is the one worth knowing about,
and a coverage figure of 100% says nothing about either.
