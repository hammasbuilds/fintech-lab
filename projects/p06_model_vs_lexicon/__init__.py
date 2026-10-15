"""06 Model against lexicon - the same test set, scored the same way, no model in project 03.

The pair to project 03. Project 03 resolves code-mixed Urdu into one concrete action with
a lexicon and a grammar, and publishes what that costs: it misses every scam phrased in
ordinary language. This project asks a local model to do the same job and scores it on the
**same cases with the same grader**, so the comparison is a comparison and not two
differently-generous evaluations.

Four things are measured, and they do not move together:

* **action exact-match** - the amount, the beneficiary, the intent. Graded on the resulting
  action, never on the wording.
* **asked instead of guessed** - on the under-specified utterances, did it stop and ask?
  A model that resolves everything scores well on the first measure and is dangerous.
* **coercion verdict** - including the four scams the lexicon provably misses. This is
  where a model should win, and the interesting question is by how much and at what cost
  in false alarms on ordinary traffic.
* **the amounts** - ``saat hazaar`` against ``saath hazaar``, ``sawa lakh``, ``paune do
  lakh``. Fifty-odd cases where being out by a factor of ten is a wrong transfer.

The rule-based layer is not a strawman here. It is the shipped product, it scores 48/48 on
the amounts, and it is the column the model is read against.
"""
