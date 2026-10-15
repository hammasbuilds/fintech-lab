"""05 Policy from prose - can a model turn a written credit policy into a running one?

The pair to project 02. Project 02's point is that the document the credit team argues
over and the thing that decides applications are the same artefact. Getting there still
means someone writing the policy in the DSL by hand, and this project asks whether a model
can do that step from prose.

What makes it worth measuring is that **the grade is not "does it look right"**. A
compiled policy can be run, so the grader runs it:

* does it compile at all, and if not, where;
* does it **behave identically** to the hand-written policy over all 5,000 borrowers -
  decision for decision, limit for limit;
* when it differs, on how many borrowers, and in which direction.

That last column is the one that matters. A policy that compiles, reads sensibly and
approves a different 4% of applicants is the dangerous output here, and it is invisible to
any check that stops at the text.

The second task is the decline notice: given the rule that fired, write the sentence the
applicant receives. Graded for faithfulness - does it state the actual reason, does it
avoid inventing a reason, does it avoid promising anything - against project 02's
template, which is correct by construction.
"""
