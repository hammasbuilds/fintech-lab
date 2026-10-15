"""Guardian - a vernacular transaction assistant with a confirmation spine.

Speak or type a transaction in Roman Urdu, Urdu or a mix of either with English, and it
resolves to one concrete action - beneficiary, amount, account - reads that action back
in the user's own language, and executes nothing without an explicit confirmation.

There is no model in here. The language layer is a lexicon and a grammar, which for this
task is a feature rather than a limitation: a rule that mis-parses an amount can be found
and fixed, and the thing deciding to move money is small enough to read in an afternoon.
It is also the honest baseline any model-based version has to beat.
"""
