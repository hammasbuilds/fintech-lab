"""Every requirement the policy language cannot express, checked against the compiler.

A list of things a language cannot do is worth nothing unless something runs it. These
tests put each attempt through the real compiler and assert the outcome the file claims -
refused by the grammar, or accepted and then dead on the first applicant.
"""

from __future__ import annotations

import pytest

from projects.p02_policy_bench import cohort, dsl, engine
from projects.p05_policy_from_prose import inexpressible

#: A minimal policy with a hole in it. Everything else here is valid, so a failure is
#: about the line under test and nothing else.
SHELL = """policy "Probe"
version 1
currency PKR

decline if age < 18 because "below the legal age"
{line}

score +50 if wallet_months >= 6 because "half a year of wallet history"

approve if score >= 50
limit min(0.25 * monthly_income, 20000)
fee 2% of limit
"""


def compile_attempt(attempt: str):
    return dsl.compile_policy(SHELL.format(line=attempt))


@pytest.mark.parametrize("requirement", inexpressible.REQUIREMENTS, ids=lambda item: item.key)
def test_each_requirement_fails_the_way_it_says_it_does(requirement) -> None:
    if requirement.limit == "grammar":
        with pytest.raises(dsl.PolicyError) as caught:
            compile_attempt(requirement.attempt)
        # A located error, not a bare "syntax error": the line number is what makes a
        # refusal usable by whoever wrote the policy.
        assert "line" in str(caught.value), requirement.key
        return

    # The other kind: the grammar accepts it, so it reaches production looking fine.
    policy = compile_attempt(requirement.attempt)
    applicant = cohort.generate(1)[0].record()
    with pytest.raises(Exception) as caught:
        engine.evaluate(policy, applicant)
    assert "has no field" in str(caught.value), requirement.key


def test_both_kinds_of_limit_are_represented() -> None:
    """A test that expects everything to fail passes against a compiler that refuses all."""
    summary = inexpressible.summary()
    assert summary["refused_by_the_grammar"] >= 10
    assert summary["compile_but_have_no_field"] >= 1
    assert summary["requirements"] == len(inexpressible.REQUIREMENTS)


def test_every_requirement_names_what_it_would_need() -> None:
    for requirement in inexpressible.REQUIREMENTS:
        assert requirement.asked_for.strip()
        assert requirement.needs.strip()
        assert requirement.workaround.strip()
        assert requirement.limit in {"grammar", "data"}


def test_the_missing_constructs_are_listed_without_duplicates() -> None:
    constructs = inexpressible.summary()["constructs_missing"]
    assert len(constructs) == len(set(constructs))
    assert all(isinstance(item, str) and item.strip() for item in constructs)


def test_a_policy_the_language_can_express_still_compiles() -> None:
    """The control for all of the above."""
    policy = compile_attempt('refer if prior_defaults >= 2 because "two defaults already on file"')
    applicant = cohort.generate(1)[0].record()
    assert engine.evaluate(policy, applicant).outcome in {"approve", "refer", "decline"}
