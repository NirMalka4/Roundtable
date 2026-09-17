---
name: roundtable-review
description: Run or interpret an explicitly requested Roundtable code review of an Azure DevOps PR, a committed local branch diff, or a complete frozen session. Use when the user says "review this PR", "review my branch", asks to replay a review, or wants action guidance from a Roundtable verdict. Routes one review through Buddies. Do NOT use for evaluation-only draft reproduction (roundtable-evaluation-pr), Roundtable authoring, non-review work, static file/module/function review, or routine implementation self-checks.
---

# Roundtable review

Run one user-requested review through Roundtable. Do not also perform a direct or specialist review.

## Workflow

1. Translate the request into one supported mode:
   - Azure DevOps PR URL;
   - numeric PR ID plus its local repository;
   - clean local repository, optionally with an explicit base ref, reviewing committed
     `base...HEAD` after Roundtable resolves the default base when omitted;
   - complete frozen Roundtable session via `--input-from`.
2. Run [`scripts/plan_review.py`](scripts/plan_review.py) with those inputs. It validates the
   boundary and prints the exact JSON `argv`; it never starts a review.
3. Show the planned command when clarification or confirmation is still needed. Otherwise execute
   that exact command once.
4. Add `--publish` only when the user explicitly requested publishing and the input is a PR.
5. After a completed review, run
   [`scripts/brief_verdict.py`](scripts/brief_verdict.py) on the printed session directory.
6. Present every canonical Judge claim as an actionability brief:
   - **Address**: `yes`, `no`, `investigate`, or `optional`;
   - **Why**: the Judge disposition, severity, reason, and decisive evidence;
   - **How**: the remediation carried by the claim's `primarySourceFindingId` reviewer finding,
     and the limits that finding states. The Judge adjudicates claims and proposes no remediation.
7. Surface any consistency warnings emitted by the brief script. Do not reopen the code or launch
   another reviewer to overturn the Judge. A second code review requires a new explicit request.

## Boundaries

- Reject dirty repositories and ask the user to commit first. The canonical local review is the
  committed `base...HEAD` diff.
- Reject requests limited to files, modules, functions, snippets, or other static scopes.
- Reject incomplete frozen sessions rather than attempting a partial replay.
- Route exact-diff evaluation draft requests to `roundtable-evaluation-pr`.
- Never substitute another configuration for `buddies`.
- Never publish by inference. A review request alone is not publishing consent.
- Treat the accepted Judge payload as canonical for post-review advice. Do not call an upheld
  remediation `verified` when its recorded disposition is weaker.
- If the user supplies an existing completed session only, skip review execution and produce the
  actionability brief from that session.
- This skill executes reviews; use `skill-forge`, `roundtable-agent-forge`,
  `roundtable-add-agent`, or `roundtable-add-config` for authoring work.

## Evaluation

Routing prompts are versioned in `.github/skill-evals/roundtable-review-routing.json`. Routing has
not been measured; the corpus is not evidence that the runtime selects this skill.
