"""04 Spec to wire - can a local model produce payment traffic a switch would accept?

The pair to project 01. Project 01 generates ISO 8583 from a scenario with code, and
checks it with a parser and 22 invariants. This project asks a model to do the generating
and uses project 01 as the oracle, which is the only reason the scores mean anything:
**every grade here is computed, not judged.** A message either packs or it does not, the
invariants either hold or they name what broke, and the deterministic generator sits beside
every case as the answer.

Four task families, in increasing order of how little the model is given:

* ``decode`` - read a raw message and say what it was. Graded against narration.
* ``fields`` - emit the data elements for a scenario as JSON. Graded on packing, the
  invariants, and field-exact agreement with the generator.
* ``wire`` - emit the whole message including the bitmap. Graded on whether the parser
  accepts it at all.
* ``negative`` - produce traffic that breaks a *named* invariant. Graded on whether the
  checker flags that rule and no other. This is the one with product value: it is asking
  the model to write a test case, and the checker says whether the test case works.
"""
