#!/usr/bin/env python3
"""Generate mock_script.json for the bundled demo.

A mock script is a list of rules matched against the rendered prompt. Every
prompt begins with a header:

    === TURN ===
    AGENT: Reviewer2
    ROLE: reviewer
    PHASE: deliberation
    ROUND: 1
    ISSUES ON THE TABLE: ISSUE-001

so rules can select on agent, phase, round and the issues under discussion.
The first matching rule wins; unmatched turns get `default`.

Run:  python3 examples/demo/make_mock_script.py
"""
from __future__ import annotations

import json
from pathlib import Path

R1_REVIEW = """This is a well-executed paper on a question that matters, and the design is
more credible than most of this literature. My concerns are about the gap between what the
design identifies and what the theory claims.

<<<ISSUE>>>
ISSUE TITLE: Mechanism claim is not identified by the design
CATEGORY: interpretation
SEVERITY: moderate
CLAIM: The manuscript says the heterogeneity by pre-crisis public employment share is "evidence
that the electoral cost of austerity operates through the visibility of service cuts" (Abstract;
Section 5). I infer that the design identifies a difference in treatment effects across a
pre-treatment covariate, not the mediating role of visibility. Public employment share plausibly
proxies several other things, including exposure to local labour-market shocks.
WHY IT MATTERS: The mechanism is the paper's theoretical contribution. If the evidence supports
only a moderator, the abstract's claim is stronger than the design warrants.
MANUSCRIPT LOCATION: Abstract; Section 5, Table 3
PROPOSED REMEDY: Restate the finding as effect heterogeneity consistent with the visibility
account, and name the rival interpretations in the text.
WHAT WOULD RESOLVE THIS CONCERN: Either a rewording that separates the moderator from the
mechanism, or direct evidence on the mediating pathway.
CONFIDENCE: high
<<<END ISSUE>>>

<<<ISSUE>>>
ISSUE TITLE: Scope conditions are asserted rather than operationalized
CATEGORY: theory
SEVERITY: minor
CLAIM: Section 2 states three attribution conditions but I do not see them measured anywhere.
I infer the reader is asked to accept that all four countries satisfy them.
WHY IT MATTERS: The scope conditions do the work of limiting the claim; unmeasured, they cannot.
MANUSCRIPT LOCATION: Section 2; Table 1
PROPOSED REMEDY: Point to where fiscal autonomy and responsibility assignment are documented.
WHAT WOULD RESOLVE THIS CONCERN: A pointer to existing documentation would satisfy me.
CONFIDENCE: medium
<<<END ISSUE>>>"""

R2_REVIEW = """The design is sensible and the estimand is stated clearly, which is rarer than it
should be. Two issues, one of which may already be handled in the appendix.

<<<ISSUE>>>
ISSUE TITLE: Parallel trends is asserted rather than demonstrated in the main text
CATEGORY: identification
SEVERITY: major
CLAIM: Section 3 specifies a two-way fixed effects model and the manuscript says the estimand is
the ATT, but the main text shows no pre-trend evidence. Treatment is assigned on a 2009
debt-to-revenue ratio, which is exactly the kind of assignment rule that generates differential
pre-trends. I recommend the identifying assumption be supported, not asserted.
WHY IT MATTERS: The headline estimate is only interpretable as an ATT if pre-trends are flat.
MANUSCRIPT LOCATION: Section 3; Table 2
PROPOSED REMEDY: Report event-study estimates in the main text.
WHAT WOULD RESOLVE THIS CONCERN: Year-by-year pre-treatment coefficients that are jointly
indistinguishable from zero, with the joint test reported.
CONFIDENCE: high
<<<END ISSUE>>>

<<<ISSUE>>>
ISSUE TITLE: Fourteen subgroup tests reported without multiplicity adjustment
CATEGORY: statistical inference
SEVERITY: moderate
CLAIM: Section 5 says the authors examined heterogeneity by unemployment, tenure, party family,
region and debt, and Appendix A5 reports fourteen splits with unadjusted p-values. The
public-employment split that carries the theoretical argument is selected from that family.
WHY IT MATTERS: With fourteen tests, the probability of at least one spurious split at the 5%
level is high. The mechanism evidence rests on a selected subgroup.
MANUSCRIPT LOCATION: Section 5; Appendix A5
PROPOSED REMEDY: Report sharpened q-values or Romano-Wolf adjusted p-values for the full family,
and state whether the public-employment split was pre-registered.
WHAT WOULD RESOLVE THIS CONCERN: An adjusted p-value for the key split that remains below
conventional thresholds, or an explicit statement that the split was pre-specified.
CONFIDENCE: high
<<<END ISSUE>>>"""

R3_REVIEW = """The question is important and a general audience will follow the argument. My
concerns are about calibration rather than execution.

<<<ISSUE>>>
ISSUE TITLE: The mechanism language in the abstract overstates what Table 3 shows
CATEGORY: interpretation
SEVERITY: moderate
CLAIM: The abstract says the heterogeneity is evidence that punishment "operates through the
visibility of service cuts". The manuscript says in Section 5 that this is what the theory
predicts. I infer that a reader will take the abstract as a mediation claim.
WHY IT MATTERS: The abstract is what most readers will take away.
MANUSCRIPT LOCATION: Abstract; Section 5
PROPOSED REMEDY: Align the abstract with what Table 3 supports.
WHAT WOULD RESOLVE THIS CONCERN: A mediation analysis, or a weaker abstract.
CONFIDENCE: high
<<<END ISSUE>>>

<<<ISSUE>>>
ISSUE TITLE: Generalization beyond the four-country sample is not supported
CATEGORY: external validity
SEVERITY: moderate
CLAIM: Section 7 concludes that "governments that consolidate through visible local service cuts
pay a larger electoral price", which is a claim about governments in general. The manuscript says
in Section 6 that the results should travel to comparable settings, but the sample is four
high-autonomy countries and the outcome is municipal elections.
WHY IT MATTERS: The conclusion is the paper's pitch to a general audience, and it is broader than
the evidence.
MANUSCRIPT LOCATION: Section 7; Section 6
PROPOSED REMEDY: Restate the conclusion at the level the design supports.
WHAT WOULD RESOLVE THIS CONCERN: Conclusions phrased about municipal incumbents in high-autonomy
settings, or evidence from a centralized case.
CONFIDENCE: medium
<<<END ISSUE>>>"""

CONSOLIDATION = """Three independent reviews, six issues, two of which are the same concern
approached from different directions. R1 and R3 both object to the mechanism language; R1 objects
to the inference from a moderator to a mechanism, R3 objects to the abstract's wording. Those are
one issue with two aspects, and I record both.

<<<NEW ISSUE>>>
TITLE: Parallel trends is asserted rather than demonstrated in the main text
CATEGORY: identification
SEVERITY: major
RAISED BY: R2
DESCRIPTION: The main text supports the ATT interpretation with no pre-trend evidence, while
treatment is assigned on a pre-period debt ratio. R2 asks for event-study estimates. Whether the
appendix already contains this is the first question for the author.
MANUSCRIPT LOCATION: Section 3; Table 2
QUESTION FOR AUTHOR: Where, if anywhere, is pre-trend evidence reported, and why is it not in the
main text?
<<<END NEW ISSUE>>>

<<<NEW ISSUE>>>
TITLE: Mechanism claim is stronger than the heterogeneity evidence supports
CATEGORY: interpretation
SEVERITY: moderate
RAISED BY: R1, R3
DESCRIPTION: R1 argues the design identifies effect heterogeneity across a pre-treatment
covariate, not a mediating pathway, and that public employment share proxies other exposures.
R3's objection is narrower and concerns the abstract's wording. The reviewers agree on the
diagnosis; they have not yet said whether they agree on the remedy.
MANUSCRIPT LOCATION: Abstract; Section 5, Table 3
QUESTION FOR AUTHOR: State in one sentence what you intend to claim about the mechanism.
<<<END NEW ISSUE>>>

<<<NEW ISSUE>>>
TITLE: Fourteen subgroup tests reported without multiplicity adjustment
CATEGORY: statistical inference
SEVERITY: moderate
RAISED BY: R2
DESCRIPTION: Appendix A5 reports fourteen splits with unadjusted p-values; the split carrying the
theoretical argument is drawn from that family.
MANUSCRIPT LOCATION: Section 5; Appendix A5
QUESTION FOR AUTHOR: Was the public-employment split pre-specified, and can adjusted p-values be
reported for the full family?
<<<END NEW ISSUE>>>

<<<NEW ISSUE>>>
TITLE: Conclusion generalizes beyond the four-country sample
CATEGORY: external validity
SEVERITY: moderate
RAISED BY: R3
DESCRIPTION: Section 7 makes a claim about governments in general from four high-autonomy
countries and municipal elections. Section 6 already contains a narrower statement, so part of
this may be an internal inconsistency rather than an overclaim.
MANUSCRIPT LOCATION: Section 7; Section 6
QUESTION FOR AUTHOR: Which statement is the intended claim, Section 6 or Section 7?
<<<END NEW ISSUE>>>

<<<NEW ISSUE>>>
TITLE: Scope conditions are asserted rather than operationalized
CATEGORY: theory
SEVERITY: minor
RAISED BY: R1
DESCRIPTION: Section 2 states three attribution conditions; R1 does not see them measured. Only
one reviewer raised this, and it stays on the ledger.
MANUSCRIPT LOCATION: Section 2; Table 1
QUESTION FOR AUTHOR: Where are the scope conditions documented?
<<<END NEW ISSUE>>>

<<<MERGE>>>
SOURCES: R2-01
INTO: Parallel trends is asserted rather than demonstrated in the main text
REASON: Same concern.
<<<END MERGE>>>

<<<MERGE>>>
SOURCES: R1-01, R3-01
INTO: Mechanism claim is stronger than the heterogeneity evidence supports
REASON: Same underlying objection: the mechanism claim outruns Table 3. R1 targets the inference,
R3 targets the abstract's wording; both aspects are preserved.
<<<END MERGE>>>

<<<MERGE>>>
SOURCES: R2-02
INTO: Fourteen subgroup tests reported without multiplicity adjustment
REASON: Same concern.
<<<END MERGE>>>

<<<MERGE>>>
SOURCES: R3-02
INTO: Conclusion generalizes beyond the four-country sample
REASON: Same concern.
<<<END MERGE>>>

<<<MERGE>>>
SOURCES: R1-02
INTO: Scope conditions are asserted rather than operationalized
REASON: Same concern.
<<<END MERGE>>>"""

AUTHOR_OPENING = """@Editor, taking the five issues in order.

ISSUE-001: reviewer misunderstanding, or rather a failure of placement on our part. The manuscript
already reports event-study estimates: Appendix A1 reports year-by-year coefficients relative to
2010, with pre-treatment coefficients for 2006-2009 jointly indistinguishable from zero (joint
F-test p = 0.71), and Appendix A2 reports pre-treatment trend differences. @Reviewer2 is right
that the assumption cannot be asserted; it is not asserted, it is demonstrated in the appendix and
not signposted in Section 3. That is an exposition failure, not an identification failure.

ISSUE-002: partially accepted. @Reviewer1 is correct that Table 3 identifies heterogeneity across a
pre-treatment covariate and not a mediating pathway, and the abstract's "operates through" is
stronger than that. We do not accept that a mediation analysis is required; we accept that the
claim should be stated as heterogeneity consistent with the visibility account.

<<<REVISION>>>
ISSUE: ISSUE-002
TYPE: clarification
MANUSCRIPT LOCATION: Abstract, sentence 4; Section 5, paragraph 2
ANALYSIS REQUIRED: none
TEXTUAL CHANGE: Replace "which we interpret as evidence that the electoral cost of austerity
operates through the visibility of service cuts" with "a pattern consistent with, but not
dispositive of, a visibility mechanism; we cannot rule out that public employment share proxies
exposure to local labour-market shocks".
SUBSTANTIVE CLAIMS CHANGE: no - the empirical finding is unchanged; the interpretive claim is
narrowed to what Table 3 supports.
<<<END REVISION>>>

ISSUE-003: accepted. The public-employment split was pre-specified in our design note but we do
not say so, and we can report adjusted p-values for the full family of fourteen.

ISSUE-004: rejected as stated. Section 6 already limits the claim to high-autonomy settings and
municipal elections. Section 7's final sentence is written at the wrong level of generality and is
inconsistent with Section 6; that inconsistency is worth fixing. But we do not accept that the
paper generalizes beyond its sample: the restriction is already in the text.

ISSUE-005: the scope conditions are documented. Section 3 and Table 1 report municipal fiscal
autonomy and responsibility assignment for all four countries. @Reviewer1, if Table 1 is not doing
that work visibly enough, say so and we will label it."""

EDITOR_R1_OPEN = """Two things can be settled this round. ISSUE-001 turns entirely on whether the
appendix evidence exists and suffices; that is for @Reviewer2 to judge, not me. On ISSUE-002 the
reviewers agree about what Table 3 shows and may disagree about the remedy.

<<<AGENDA>>>
ISSUES: ISSUE-001
SPEAKERS: @Reviewer2
QUESTION: The author points to Appendix A1 and A2. Does that evidence satisfy your concern, and if
so is anything still required beyond moving it into the main text?
<<<END AGENDA>>>

<<<AGENDA>>>
ISSUES: ISSUE-002
SPEAKERS: @Reviewer1, @Reviewer3
QUESTION: You agree the abstract overstates. Do you agree on the remedy? State whether you require
a new analysis or only a change of claim.
<<<END AGENDA>>>"""

R2_R1_ISSUE001 = """@Author: Appendix A1 answers my concern. The joint F-test on the 2006-2009
coefficients is the test I was asking for, and Appendix A2 covers covariate trends. I did not see
it because Section 3 does not point to it, and I should have checked the appendix before calling
this major. I was wrong to classify it as an identification problem; it is an exposition problem.

I withdraw the objection as stated. One thing does remain: the event-study figure belongs in the
main text next to Table 2, because a reader assessing the ATT interpretation should not have to
find it in an appendix. That is a presentation requirement, not a condition of identification.

<<<POSITION CHANGE>>>
ISSUE: ISSUE-001
PREVIOUS POSITION: OBJECTION (major)
NEW POSITION: EXPOSITION ONLY
REASON: The evidence I asked for already exists in Appendix A1 (joint F-test p = 0.71) and A2. The
problem is signposting in Section 3, not identification.
TRIGGERED BY: @Author
REMAINING CONCERN: Figure A1 should be promoted to the main text beside Table 2.
<<<END POSITION CHANGE>>>"""

R1_R1_ISSUE002 = """@Author accepts the diagnosis, so the live question is the remedy. I do not
think a new analysis is required. My objection is that the inference from Table 3 to a mechanism
is not licensed by the design; the proposed rewording states exactly that limitation, including
the labour-market-shock alternative, which is the rival I had in mind.

@Reviewer3, your remedy was "a mediation analysis, or a weaker abstract". Those are not
equivalent, and I think the second is right. A mediation analysis here would require assumptions
about the mediator that this design cannot support, and I would rather the paper claim less than
claim more on the back of an analysis nobody should believe.

I am content with the proposed text, provided Section 5 carries the same qualification and not
only the abstract."""

R3_R1_ISSUE002 = """@Reviewer1, I take your point about what the design licenses, but I am not yet
ready to drop the analysis request. The paper's contribution is the mechanism; if the mechanism
cannot be tested, the contribution is a heterogeneity result, which is a smaller paper. A
mediation analysis, even under strong assumptions, would tell readers something about the
pathway.

@Reviewer2, you are the right person to say whether a mediation analysis of this design would
estimate anything interpretable. If it would, I want it. If it would not, I will settle for the
rewording."""

R2_R1_ISSUE002 = """@Reviewer3, it would not. With public employment share measured pre-treatment
and no exogenous variation in it, a mediation analysis identifies the effect of the treatment on
the outcome through a covariate only under sequential ignorability of the mediator, which is
untestable here and implausible: whatever determines local public employment also determines
exposure to the labour-market shock @Reviewer1 named. The estimand such an analysis targets is not
the mechanism the paper claims; it is a decomposition that inherits the confounding.

I would not request it, and if the authors ran it I would ask them to drop it. @Reviewer1's
remedy is the right one: state the heterogeneity, name the rival explanation, claim no more.

I have no position change to record on ISSUE-002; I did not raise it and my view has not moved."""

AUTHOR_R1_ISSUE002 = """@Reviewer1, agreed on both points: the qualification goes in Section 5 as
well as the abstract, and we will name the labour-market-shock alternative explicitly rather than
in general terms.

@Reviewer3, we take the force of your objection that this makes the paper a heterogeneity result.
We think that is the honest description of what we have, and we would rather publish the smaller
true claim.

<<<REVISION>>>
ISSUE: ISSUE-002
TYPE: clarification
MANUSCRIPT LOCATION: Abstract sentence 4; Section 5 paragraph 2; Section 7 paragraph 1
ANALYSIS REQUIRED: none
TEXTUAL CHANGE: Abstract and Section 5 restate Table 3 as effect heterogeneity consistent with the
visibility account, naming exposure to local labour-market shocks as an unruled-out alternative.
Section 7 is brought into line.
SUBSTANTIVE CLAIMS CHANGE: yes - the mechanism claim becomes a heterogeneity claim consistent with
the mechanism.
<<<END REVISION>>>"""

EDITOR_R1_CLOSE = """ISSUE-001 is settled: @Reviewer2 withdrew the identification objection on the
evidence and named a presentation requirement instead. ISSUE-002 is not settled, because
@Reviewer3 has not responded to @Reviewer2's estimand argument.

<<<LEDGER UPDATE>>>
ISSUE: ISSUE-001
STATUS: EXPOSITION ONLY
SEVERITY: minor
PRIORITY: important
SUMMARY: R2 asked for pre-trend evidence; it exists in Appendix A1 (joint F-test p = 0.71) and A2,
and R2 withdrew the identification objection after seeing it. What remains is placement: Section 3
does not signpost the evidence and the event-study figure is not in the main text.
REQUIRED ACTION: Move the Figure A1 event-study plot into Section 3 beside Table 2, and add one
sentence in Section 3 stating the joint pre-trend test and its p-value.
DECISION: Not an identification problem. The identifying assumption is supported; the manuscript
hid the support.
<<<END LEDGER UPDATE>>>

<<<LEDGER UPDATE>>>
ISSUE: ISSUE-002
STATUS: OPEN
SUMMARY: All three reviewers and the author agree Table 3 shows heterogeneity, not mediation. R1
requires only a change of claim; R3 asked for a mediation analysis; R2 argues that analysis would
target a different estimand under untestable assumptions. R3 has not yet responded to that.
<<<END LEDGER UPDATE>>>"""

EDITOR_R2_OPEN = """@Reviewer3 owes the room an answer on ISSUE-002. The other two open issues can
be handled in parallel.

<<<AGENDA>>>
ISSUES: ISSUE-002
SPEAKERS: @Reviewer3
QUESTION: @Reviewer2 says a mediation analysis here targets a different quantity under
sequential ignorability you cannot defend. Do you accept that, and does your analysis request
stand?
<<<END AGENDA>>>

<<<AGENDA>>>
ISSUES: ISSUE-003
SPEAKERS: @Reviewer2, @Author
QUESTION: The author says the split was pre-specified in a design note. Does that resolve the
selection concern, and what exactly should be reported?
<<<END AGENDA>>>

<<<AGENDA>>>
ISSUES: ISSUE-004
SPEAKERS: @Reviewer3, @Author
QUESTION: The author says Section 6 already limits the claim and Section 7 is inconsistent with
it. Is the disagreement about the text, or about what the paper is entitled to claim?
<<<END AGENDA>>>"""

R3_R2_ISSUE002 = """@Reviewer2, I accept that. The sequential ignorability point is decisive and I
do not have an answer to it: if the mediator is not exogenous, the decomposition inherits the
confounding and I would be asking for a number that cannot be interpreted. I defer to you on the
statistical question.

My concern was never really the analysis; it was that the abstract promises a mechanism the paper
cannot deliver. The proposed rewording addresses that, provided it reaches the abstract and not
only Section 5.

<<<POSITION CHANGE>>>
ISSUE: ISSUE-002
PREVIOUS POSITION: OBJECTION (moderate)
NEW POSITION: EXPOSITION ONLY
REASON: @Reviewer2 showed that a mediation analysis of this design identifies a decomposition
under untestable sequential ignorability, not the mechanism. The remaining problem is the wording
of the claim, which the author's proposed revision fixes.
TRIGGERED BY: @Reviewer2
REMAINING CONCERN: The qualification must appear in the abstract, not only in Section 5.
<<<END POSITION CHANGE>>>"""

R2_R2_ISSUE003 = """@Author, a design note is not a pre-registration, and I would not want the
paper to describe it as one. But the distinction that matters here is smaller than it looks: with
fourteen splits reported in Appendix A5, readers need the adjusted p-values whether or not the key
split was pre-specified.

What I want is specific: Romano-Wolf stepdown p-values for the full family of fourteen splits in
Appendix A5, the adjusted p-value for the public-employment split reported in Section 5 next to
the unadjusted one, and one sentence saying the split was specified in advance in a design note
that is not a registration. If the adjusted p-value for the key split crosses conventional
thresholds, the mechanism discussion has to be rewritten around that; if it does not, the current
discussion stands as revised under ISSUE-002."""

AUTHOR_R2_ISSUE003 = """@Reviewer2, accepted in full, including the description of the design note.
We will not call it a pre-registration.

<<<REVISION>>>
ISSUE: ISSUE-003
TYPE: analysis
MANUSCRIPT LOCATION: Section 5 paragraph 3; Appendix A5
ANALYSIS REQUIRED: Romano-Wolf stepdown adjusted p-values for all fourteen heterogeneity splits in
Appendix A5, using 10,000 bootstrap replications clustered at the municipality level.
TEXTUAL CHANGE: Report the adjusted p-value for the public-employment split in Section 5 beside
the unadjusted one; add one sentence in Section 5 stating the split was specified in a design note
prior to analysis and that the note is not a public registration.
SUBSTANTIVE CLAIMS CHANGE: conditional - if the adjusted p-value exceeds 0.05 the heterogeneity
discussion is rewritten as suggestive.
<<<END REVISION>>>"""

R3_R2_ISSUE004 = """@Author, it is about what the paper is entitled to claim, not only the text.

Section 6 says the results "should travel to comparable settings". Section 7 says "governments
that consolidate through visible local service cuts pay a larger electoral price". I accept that
fixing Section 7's sentence removes the inconsistency. I do not accept that Section 6 is
sufficiently disciplined: "comparable settings" is not defined anywhere, and the paper gives a
reader no criterion for deciding whether a case is comparable.

Four countries selected for high municipal fiscal autonomy is a narrow base for any travel claim,
and I would want the paper to say which observable features a setting must have. I recognize this
is a judgement about how much generalization a four-country municipal design licenses, and the
author and I may simply disagree about that."""

AUTHOR_R2_ISSUE004 = """@Reviewer3, we will fix Section 7, and we accept that "comparable settings"
should be operationalized rather than gestured at: Section 6 can state the two observable
features, municipal fiscal autonomy above a stated threshold and locally assigned service
responsibility, and point to Table 1.

We do not accept the stronger claim that a four-country design cannot support a conditional travel
statement. Our claim is conditional on documented scope conditions, which is what external
validity claims in this literature normally are. We think this is a reasonable disagreement about
how much generalization a design of this kind licenses, not an error in the paper.

<<<REVISION>>>
ISSUE: ISSUE-004
TYPE: substantive
MANUSCRIPT LOCATION: Section 6; Section 7 final paragraph
ANALYSIS REQUIRED: none
TEXTUAL CHANGE: Section 7's final sentence is restated about municipal incumbents in high-autonomy
settings. Section 6 replaces "comparable settings" with the two observable scope conditions and
cross-references Table 1.
SUBSTANTIVE CLAIMS CHANGE: yes - the concluding claim is narrowed to the population the design
covers.
<<<END REVISION>>>"""

EDITOR_R2_CLOSE = """Three resolutions and one genuine disagreement.

<<<LEDGER UPDATE>>>
ISSUE: ISSUE-002
STATUS: EXPOSITION ONLY
SEVERITY: moderate
PRIORITY: essential
SUMMARY: R1 and R3 agreed the abstract overstates; R3's request for a mediation analysis was
withdrawn after R2 showed it would identify a decomposition under untestable sequential
ignorability rather than the mechanism. The author's rewording states the heterogeneity result and
names the labour-market-shock alternative. No analysis is required.
REQUIRED ACTION: Rewrite the abstract's fourth sentence and Section 5 paragraph 2 to state
heterogeneity consistent with the visibility account, naming exposure to local labour-market
shocks as an unruled-out alternative; bring Section 7 into line. The qualification must appear in
the abstract.
DECISION: Interpretation problem, not an empirical one. R2's estimand argument is adopted.
<<<END LEDGER UPDATE>>>

<<<LEDGER UPDATE>>>
ISSUE: ISSUE-003
STATUS: NEW ANALYSIS REQUIRED
SEVERITY: moderate
PRIORITY: essential
SUMMARY: Fourteen splits are reported with unadjusted p-values and the key split comes from that
family. The author accepts the correction and the description of the design note.
REQUIRED ACTION: Report Romano-Wolf stepdown adjusted p-values for all fourteen splits in Appendix
A5 and the adjusted p-value for the public-employment split in Section 5; state that the split was
specified in a design note that is not a public registration. If the adjusted p-value exceeds
0.05, rewrite the heterogeneity discussion as suggestive.
DECISION: Required, not optional. The mechanism discussion rests on this split.
<<<END LEDGER UPDATE>>>

<<<LEDGER UPDATE>>>
ISSUE: ISSUE-004
STATUS: UNRESOLVED DISAGREEMENT
SEVERITY: moderate
PRIORITY: important
SUMMARY: The author and R3 agree Section 7 must be narrowed and that Section 6's "comparable
settings" should be operationalized; both are accepted. They disagree about whether a four-country
municipal design licenses any conditional travel claim. This is a judgement about generalization,
not a factual dispute, and neither party has moved.
REQUIRED ACTION: Restate Section 7's final sentence about municipal incumbents in high-autonomy
settings; replace "comparable settings" in Section 6 with the two observable scope conditions and
cross-reference Table 1. The remaining disagreement about the conditional travel claim is recorded,
not resolved.
DECISION: Partially actionable. I am not going to adjudicate a reasonable disagreement about
generalization by fiat, and I am not going to record agreement that does not exist.
<<<END LEDGER UPDATE>>>"""

EDITOR_R3_OPEN = """One issue left.

<<<AGENDA>>>
ISSUES: ISSUE-005
SPEAKERS: @Reviewer1
QUESTION: The author points to Section 3 and Table 1 for the scope conditions. Does that answer
your concern?
<<<END AGENDA>>>"""

R1_R3_ISSUE005 = """@Author, yes. Table 1 does report fiscal autonomy and responsibility assignment
by country, and Section 3 describes the assignment rule. I raised this because Section 2 states
the conditions and does not point forward, so a reader meeting the conditions has no idea they are
documented twenty pages later. That is worth a cross-reference but it is not a defect in the
argument.

I withdraw the objection.

<<<POSITION CHANGE>>>
ISSUE: ISSUE-005
PREVIOUS POSITION: OBJECTION (minor)
NEW POSITION: REVIEWER WITHDREW OBJECTION
REASON: The scope conditions are documented in Section 3 and Table 1; I had not connected them to
the three conditions in Section 2. A forward reference would help readers but nothing substantive
is missing.
TRIGGERED BY: @Author
REMAINING CONCERN: none
<<<END POSITION CHANGE>>>"""

EDITOR_R3_CLOSE = """@Reviewer1 withdrew on the evidence. The ledger is complete.

<<<LEDGER UPDATE>>>
ISSUE: ISSUE-005
STATUS: REVIEWER WITHDREW OBJECTION
SEVERITY: minor
PRIORITY: minor
SUMMARY: R1 asked where the scope conditions are operationalized; they are in Section 3 and Table
1. R1 withdrew after the author pointed to them, noting only that Section 2 does not signpost
them.
REQUIRED ACTION: Add a forward reference from Section 2 to Section 3 and Table 1. Optional.
DECISION: No substantive defect. Recorded as withdrawn rather than resolved, because the
manuscript already contained the answer.
<<<END LEDGER UPDATE>>>

<<<TERMINATE>>>
REASON: Every issue has a terminal status. ISSUE-004 remains a recorded disagreement about
generalization; further discussion would repeat positions both parties have already stated
clearly.
<<<END TERMINATE>>>"""



REVISED_MANUSCRIPT = """# Fiscal Consolidation and Incumbent Support: Evidence from European Municipalities, 2008-2019

## Abstract

Do voters punish incumbents for austerity? We exploit a discontinuity in a national grant formula
that assigned sharply different consolidation requirements to otherwise similar municipalities.
Using a difference-in-differences design across 2,412 municipalities in four countries, we find
that incumbent vote share falls by 3.8 percentage points (SE 1.1) in municipalities required to
consolidate. The effect is concentrated among municipalities with high pre-crisis public
employment, a pattern consistent with, but not dispositive of, a visibility mechanism: we cannot
rule out that public employment share proxies exposure to local labour-market shocks.
<!-- rev: ISSUE-002 -->

## 3. Research Design

[Unchanged text.] Figure 1 (moved from Appendix A1) plots year-by-year estimates relative to 2010.
The pre-treatment coefficients for 2006-2009 are individually and jointly indistinguishable from
zero (joint F-test p = 0.71), which supports the parallel-trends assumption underlying the ATT
interpretation. <!-- rev: ISSUE-001 -->

## 5. Results

[Unchanged text through paragraph 1.] Table 3 splits the sample by pre-crisis public employment
share. The effect is -6.2 points (SE 1.6) in the top tercile and -1.1 points (SE 1.4) in the
bottom tercile. We read this as effect heterogeneity consistent with the visibility account rather
than as evidence of mediation: public employment share is measured pre-treatment and plausibly
proxies exposure to local labour-market shocks. <!-- rev: ISSUE-002 -->

We examined fourteen heterogeneity splits in total (Appendix A5). The public-employment split was
specified in a design note prior to analysis; that note is not a public registration. Appendix A5
now reports Romano-Wolf stepdown adjusted p-values for the full family; the adjusted p-value for
the public-employment split is [PLACEHOLDER: to be computed with 10,000 bootstrap replications
clustered at the municipality level]. If it exceeds 0.05 the discussion above is restated as
suggestive. <!-- rev: ISSUE-003 -->

## 6. External Validity

Our four countries share high municipal fiscal autonomy. The results should travel to settings
with two observable features: municipal fiscal autonomy above the threshold documented in Table 1,
and locally assigned responsibility for the services subject to consolidation.
<!-- rev: ISSUE-004 -->

## 7. Conclusion

Municipal incumbents in high-autonomy settings are punished for austerity when they can see it.
<!-- rev: ISSUE-004 -->
"""

RESPONSE_LETTER = """We thank the editor and the three reviewers. The deliberation changed the
paper in three places and left one disagreement standing, which we record rather than paper over.

**ISSUE-001 (pre-trends).** @Reviewer2 asked for evidence supporting parallel trends. The evidence
existed in Appendix A1 and we had not signposted it. We have moved the event-study figure into
Section 3 as Figure 1 and state the joint pre-trend test (p = 0.71) in the text. @Reviewer2
withdrew the identification objection on this basis; no analysis changed.

**ISSUE-002 (mechanism).** @Reviewer1 and @Reviewer3 were right that the abstract's "operates
through" outran Table 3. The abstract and Section 5 now describe effect heterogeneity consistent
with the visibility account and name exposure to local labour-market shocks as an unruled-out
alternative. We did not run a mediation analysis: @Reviewer2 showed it would identify a
decomposition under untestable sequential ignorability rather than the mechanism, and @Reviewer3
accepted that argument.

**ISSUE-003 (multiplicity).** Accepted in full. Appendix A5 reports Romano-Wolf stepdown adjusted
p-values for all fourteen splits and Section 5 reports the adjusted p-value for the key split
beside the unadjusted one. We describe the design note accurately: it is not a registration.

**ISSUE-004 (external validity).** Partially accepted. Section 7's final sentence now refers to
municipal incumbents in high-autonomy settings, and Section 6 states the two observable scope
conditions instead of "comparable settings". We continue to disagree with @Reviewer3 that a
four-country municipal design cannot support a conditional travel claim; the editor recorded this
as an unresolved disagreement and we are content for readers to judge it.

**ISSUE-005 (scope conditions).** @Reviewer1 withdrew after we pointed to Section 3 and Table 1.
We have added the forward reference from Section 2 that R1 suggested."""


def rule(keys, text):
    return {"all": keys, "respond": text}


SCRIPT = {
    "default": "NO FURTHER COMMENT.",
    "rules": [
        rule(["AGENT: Reviewer1", "PHASE: independent_review"], R1_REVIEW),
        rule(["AGENT: Reviewer2", "PHASE: independent_review"], R2_REVIEW),
        rule(["AGENT: Reviewer3", "PHASE: independent_review"], R3_REVIEW),
        rule(["AGENT: Editor", "PHASE: consolidation"], CONSOLIDATION),
        rule(["AGENT: Author", "Phase 3 opens"], AUTHOR_OPENING),

        rule(["AGENT: Editor", "Round 1: chair the round"], EDITOR_R1_OPEN),
        rule(["AGENT: Reviewer2", "ROUND: 1\nISSUES ON THE TABLE: ISSUE-001"], R2_R1_ISSUE001),
        rule(["AGENT: Reviewer1", "ROUND: 1\nISSUES ON THE TABLE: ISSUE-002"], R1_R1_ISSUE002),
        rule(["AGENT: Reviewer3", "ROUND: 1\nISSUES ON THE TABLE: ISSUE-002"], R3_R1_ISSUE002),
        rule(["AGENT: Reviewer2", "ROUND: 1\nISSUES ON THE TABLE: ISSUE-002"], R2_R1_ISSUE002),
        rule(["AGENT: Author", "ROUND: 1\nISSUES ON THE TABLE: ISSUE-002"], AUTHOR_R1_ISSUE002),
        rule(["AGENT: Editor", "Round 1: update the ledger"], EDITOR_R1_CLOSE),

        rule(["AGENT: Editor", "Round 2: chair the round"], EDITOR_R2_OPEN),
        rule(["AGENT: Reviewer3", "ROUND: 2\nISSUES ON THE TABLE: ISSUE-002"], R3_R2_ISSUE002),
        rule(["AGENT: Reviewer2", "ROUND: 2\nISSUES ON THE TABLE: ISSUE-003"], R2_R2_ISSUE003),
        rule(["AGENT: Author", "ROUND: 2\nISSUES ON THE TABLE: ISSUE-003"], AUTHOR_R2_ISSUE003),
        rule(["AGENT: Reviewer3", "ROUND: 2\nISSUES ON THE TABLE: ISSUE-004"], R3_R2_ISSUE004),
        rule(["AGENT: Author", "ROUND: 2\nISSUES ON THE TABLE: ISSUE-004"], AUTHOR_R2_ISSUE004),
        rule(["AGENT: Editor", "Round 2: update the ledger"], EDITOR_R2_CLOSE),

        rule(["AGENT: Editor", "Round 3: chair the round"], EDITOR_R3_OPEN),
        rule(["AGENT: Reviewer1", "ROUND: 3\nISSUES ON THE TABLE: ISSUE-005"], R1_R3_ISSUE005),
        rule(["AGENT: Editor", "Round 3: update the ledger"], EDITOR_R3_CLOSE),

        rule(["AGENT: Author", "Final stage: produce the revision"], REVISED_MANUSCRIPT),
        rule(["AGENT: Author", "Final stage: response to reviewers"], RESPONSE_LETTER),
    ],
}

if __name__ == "__main__":
    out = Path(__file__).with_name("mock_script.json")
    out.write_text(json.dumps(SCRIPT, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(SCRIPT['rules'])} rules)")
