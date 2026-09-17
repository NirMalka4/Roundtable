"""Downgrading a peer's severity must cost what rejecting it costs.

Phase 3 priced `rejected`: a rejection now owes a `contradiction` anchor and recorded
counter-evidence. It left the rung above it free. `G6` produces seven kinds of
counterevidence, the override ladder offers five moves, and nothing mapped category to
move — so any counterevidence could be routed to "downgrade", which keeps the claim,
satisfies `claims_coverage`, dodges the rejection floor, and owes no citation.

The regression this pins is real. On the evaluated run, the merge-comparer finding was
upheld at `high`/`blocker`; on the next run the same finding, from the same source with the
same mechanism and trigger, was upheld at `medium`/`suggestion`. The discount rested on a
counter-evidence item reading "the comparer also omits BitwiseServiceSources, so coarse
merge identity predates this change and may be deliberate". The factual half is true. But
pre-existence bounds nothing on the path — the merge still drops the rule and the caller
still gets success — and "may be deliberate" is exactly the unanchored intent claim G6
already disqualifies, admitted because G6's closing rule spoke only of *defeating* a
failure while the Judge was *downgrading* one.

Worse for the reader: `counter_evidence` is never published on a thread, so the claim
published as `Medium · suggestion` describing silent data loss with a false success, and
the argument for why that is not a blocker was invisible to the only audience that matters.

These tests pin the text. They cannot prove the Judge reasons better — only an evaluation
can — but they do prove the rules are stated, reachable, and agreed between schema and
prompt, which is the part that fails silently.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

import roundtable.configs.buddies as buddies
from roundtable.validation.gates import compile_validator

BUNDLE = Path(buddies.__file__).parent
SCHEMAS = BUNDLE / "schemas"
JUDGE = yaml.safe_load((SCHEMAS / "judge.schema.yaml").read_text("utf-8"))
VALIDATOR = compile_validator(JUDGE, SCHEMAS)
PROMPT = " ".join(
    (BUNDLE / "prompts" / "Reviewer" / "Agents" / "judge.agent.md").read_text("utf-8").split()
)

_CLAIM = JUDGE["properties"]["claims"]["items"]["properties"]


def _document(claim: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(JUDGE["examples"][0])
    doc["claims"] = [claim]
    return doc


def _errors(claim: dict[str, Any]) -> list[str]:
    return [e.message for e in VALIDATOR.iter_errors(_document(claim))]


# ── the instructed destination must exist ───────────────────────────────────────────────


def test_an_upheld_claim_may_publish_the_bound_that_set_its_severity() -> None:
    """Positive control. The schema now sends a severity-setting bound to `evidence` with
    role `verification`; if that combination did not validate, the instruction would be
    unfollowable and the Judge would file the bound where no reader can reach it."""
    claim = {
        "id": "J-01",
        "source_finding_ids": ["countercase::CC-01"],
        "primary_source_finding_id": "countercase::CC-01",
        "title": "Merge drops a rule that differs only by its new restriction",
        "criterion": "functional_reliability",
        "disposition": "upheld",
        "severity": "medium",
        "reason": "The narrowing never reaches the destination although the caller is told it did.",
        "evidence": [
            {
                "file": "src/Utils/RuleEqualityComparerForMerging.cs",
                "start_line": 14,
                "observation": "the equality method never reads the new restriction column",
                "role": "defect",
            },
            {
                "file": "src/Services/SqlOrgSuppressionRulesProvider.cs",
                "start_line": 705,
                "observation": "the caller retries the merge on the next synchronization pass",
                "role": "verification",
            },
        ],
    }
    assert not _errors(claim)


# ── schema: where a winning bound goes, and how it differs from a contradiction ──────────


def test_counter_evidence_is_scoped_to_the_case_that_lost() -> None:
    """The field held a severity-setting bound on the evaluated run. Its description is the
    only thing that separates "argued against the ruling" from "produced it"."""
    description = " ".join(_CLAIM["counter_evidence"]["description"].split())
    assert "and lost" in description
    assert "changed*" in description or "*changed*" in description
    assert "belongs in `evidence` with role `verification`" in description


def test_a_bound_is_a_contradiction_only_where_it_defeats_the_finding() -> None:
    """`contradiction` already told the Judge to cite what bounds a consequence. Routing a
    *surviving* claim's bound to `verification` makes that ambiguous unless the role text
    says which case is which."""
    role = " ".join(_CLAIM["evidence"]["items"]["properties"]["role"]["description"].split())
    assert "only where it defeats the finding" in role
    assert "the finding stands and the bound merely limits it, it is a `verification`" in role


# ── prompt: the ladder, the scope question, and the disqualifier ─────────────────────────


def test_the_ladder_licenses_a_downgrade_only_from_a_bound_on_the_same_path() -> None:
    """An open-ended downgrade step is what let a truth-bearing argument buy a discount."""
    assert (
        "reduce severity only for something that bounds the consequence **on the same path**"
        in (PROMPT)
    )
    assert "carries no severity setting" in PROMPT


def test_counterevidence_about_truth_routes_away_from_severity() -> None:
    """Pairing the hard rule with where the excluded categories go, so the Judge is never
    left with a bound rule and no legal move."""
    assert "rules the claim out at step 1 or leaves it standing at full weight" in PROMPT
    assert "`insufficient_evidence` is the answer when the record settles neither" in PROMPT


def test_pre_existence_is_a_scope_question_and_never_a_discount() -> None:
    """The reviewers already answer this binarily; the Judge had no counterpart and invented
    partial credit."""
    assert "That an analogous gap already exists elsewhere carries no severity setting" in PROMPT
    assert "introduced or exposed the proposition is G1's question" in PROMPT
    assert "never a discount on what the change now costs" in PROMPT


def test_unanchored_counterevidence_cannot_soften_a_failure_either() -> None:
    """G6 disqualified it from *defeating* a failure. The loophole was one verb wide."""
    assert 'a guess that the behavior "may be" intentional' in PROMPT
    assert "does not defeat a concrete failure — and it does not soften one either" in PROMPT
    assert "too weak to overturn a claim is too weak to discount it" in PROMPT


# ── schema and prompt must agree, or the model meets the rule only as a retry ────────────


def test_the_prompt_mirrors_where_a_winning_bound_is_recorded() -> None:
    """`counter_evidence`'s description states the routing; the body states the sorting rule
    that produces it. A field meaning without its procedure is how north_star emitted prose
    for two runs."""
    assert "Sort them by which one won" in PROMPT
    assert "did not argue against your ruling, it produced it" in PROMPT
    assert "Only the case that argued and lost stays in the separate record" in PROMPT


# ── phase 8: the ladder was bypassed, not violated ───────────────────────────────────────


def test_a_downgrade_is_defined_by_where_the_rating_lands_not_how_it_was_reached() -> None:
    """The measured failure. `G7` says never inherit reviewer severity, which authorizes a
    *fresh* assignment; step 3 governed a *reduction*. The Judge rated a `high` countercase
    finding `medium` while citing no bound at all — it never took the step whose licensed
    reasons phase 4 had closed. Pricing a move the Judge does not make changes nothing."""
    assert "Rating a claim below the highest severity its reviewers gave it" in PROMPT
    assert (
        "*is* a downgrade, whether you got there by lowering their rating or by assigning "
        "your own from scratch" in PROMPT
    )
    assert "this step governs the outcome, not the route" in PROMPT


def test_a_band_below_the_stated_consequence_must_name_its_bound() -> None:
    """Phase 4 removed the illegitimate reason to lower severity without requiring a
    legitimate one, so the published rating became unexplained rather than unjustified.
    `medium` asserts two positive conditions; nothing had ever asked the Judge to show them."""
    assert "A rating below the band that consequence matches has to be earned in `reason`" in PROMPT
    assert "name what contains the reach, or what makes it recoverable" in PROMPT
    assert "the consequence you described is the rating" in PROMPT


def test_the_reason_field_mirrors_where_a_severity_bound_is_published() -> None:
    """Schema and prompt must agree or the model meets the rule only as a retry."""
    assert "states the bound that lowered it" in _CLAIM["reason"]["description"]


def test_the_summary_may_not_assert_a_block_no_claim_carries() -> None:
    """Observed verbatim: a summary ending "the target may not proceed as-is" rendered
    directly beneath the derived heading `APPROVE_WITH_SUGGESTIONS`, because every claim was
    `medium`/`low`. Phase 4 banned the discount that had kept the narrative consistent, and
    the prose drifted to blocking while the rating stayed. Prose and machinery contradicted
    each other inside one comment."""
    assert "The summary is not an opinion held separately from the claims it summarizes" in PROMPT
    assert "say the target may not proceed only when one of your own claims blocks it" in PROMPT
    assert "without implying the merge is held" in PROMPT
