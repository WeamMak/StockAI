# Pre-draft refinement and manager permission inheritance — design amendment

## Status

Design sections approved interactively by the user on 2026-08-21. This written
document is awaiting the required user review before implementation planning.

## Context and defect

The bounded-refinement design intends an officer to reconsider an
`approval_ready` recommendation up to three times before a purchase-order
draft exists. T28 subsequently routed every valid initial or refined
recommendation directly through `create_draft`, and T29 correctly hid and
rejected refinement after that draft existed. The combined behavior leaves no
normal user-visible refinement window: a real successful scan moves directly
to `pending_approval`.

This is a cross-task design contradiction rather than a styling-only defect:

- `docs/spec.md` says refinement is allowed only before draft creation;
- the graph routes a valid recommendation immediately to `create_draft`;
- `ScanService.refine_case` rejects a case with a draft; and
- `RecommendationPage` hides refinement outside `succeeded`.

The role description also understates the implemented hierarchy. Backend
officer dependencies already accept managers, but the main specification says
only that managers inherit officer *read* actions. The product decision is that
a manager is a strict superset of the procurement-officer role, including
starting scans, refining recommendations, and submitting the selected
recommendation for draft creation.

## Goals

- Restore a real, durable refinement window before any Odoo draft exists.
- Require an explicit **Create draft and send to manager** action to close that
  window.
- Let both officers and managers start scans, read cases, refine, create the
  draft, inspect status, and read audit history.
- Retain approve, budget-exception approval, and reject as manager-only
  actions.
- Preserve the exact latest recommendation/checkpoint through draft creation
  and the existing T29 decision lifecycle.
- Remove duplicated commercial-detail cards from the manager action area and
  place compact modern decision controls at the bottom of the case page.
- Preserve idempotency, optimistic concurrency, restart safety, auditability,
  and the existing fictional/no-supplier-contact boundary.

## Non-goals

- No refinement after a draft exists.
- No request-change, draft-edit, reapproval, supplier contact, payment, or
  legally binding order path.
- No new human role, agent, service, database, queue, or AWS resource.
- No change to the three-attempt refinement cap or the 280-character note,
  justification, and rejection-reason bounds.
- No authority for an officer to approve, reject, confirm, or cancel a draft.

## Considered approaches

### 1. Pause the existing LangGraph before draft creation — selected

Add a pre-draft human interrupt after validated reasoning. Persist its exact
thread ID on the case. Refinement replaces that current checkpoint with a fresh
refinement thread. An explicit authenticated submission resumes the selected
thread, creates the draft through the existing MCP tool, and then reaches the
existing T29 manager-decision interrupt.

This keeps the recommendation, evidence, draft, and decision in one durable
workflow and makes the user handoff explicit.

### 2. Create the draft directly in the API service — rejected

A new API service could reconstruct a draft command from the persisted case and
call MCP without resuming LangGraph. This is initially smaller but duplicates
workflow validation, weakens the single coded-agent path, and separates the
draft from the checkpoint T29 must later resume.

### 3. Create immediately, then cancel/recreate for refinement — rejected

This preserves the present graph shape but introduces unnecessary Odoo writes,
ambiguous cancellation outcomes, superseded revisions, and reapproval rules.
It violates the approved rule that refinement happens before a draft exists.

## Role model

The effective permission hierarchy is explicit:

| Capability | Officer | Manager |
|---|---:|---:|
| Sign in and sign out | Yes | Yes |
| Start a manual scan | Yes | Yes |
| List and read scans/cases/evidence | Yes | Yes |
| Submit a bounded refinement | Yes | Yes |
| Create draft and send to manager review | Yes | Yes |
| Read decision audit | Yes | Yes |
| Approve or approve a budget exception | No | Yes |
| Reject and cancel a draft | No | Yes |

`OfficerPrincipalDep` remains the shared application-operator dependency and
must continue to accept `OFFICER` and `MANAGER`. Manager-only dependencies
remain exact. Route-level tests must enumerate every officer capability with a
manager identity so future UI or API changes cannot accidentally narrow the
inheritance.

## State and graph design

The corrected lifecycle is:

```text
queued -> running -> succeeded/approval_ready
                         |        ^
                         | refine | (maximum three fresh attempts)
                         v        |
                recommendation_ready interrupt
                         |
             Create draft and send to manager
                         v
                   creating_draft
                         v
                  pending_approval
                    |          |
                 approve     reject
                    |          |
                confirming   cancelled
                    |
                 confirmed
```

The existing graph becomes:

```text
gather_evidence -> resolve_preferences -> reason
    -> await_draft_submission -> create_draft -> load_decision
        -> confirm -> END
        -> cancel  -> END
```

`await_draft_submission` calls `interrupt()` only for a validated
`approval_ready` recommendation. The initial case thread is the case ID. Each
refinement continues to use `{case_id}:refine-{attempt}`. `CaseRecord.workflow_thread_id`
changes meaning from "thread that owns the draft" to "latest authoritative
paused workflow thread" and is persisted as soon as a recommendation reaches
the pre-draft interrupt. The same value remains attached when that thread later
creates the draft and pauses for T29.

The pre-draft interrupt does not trust browser commercial fields. The server
resumes it with a fixed, server-owned proceed command after independently
validating the stored case status, revision, absence of a draft, and exact
thread identifier. The draft node consumes only checkpointed recommendation
and evidence.

Submitting a refinement invalidates the previous pre-draft checkpoint as the
case's selectable workflow, runs a fresh thread, and atomically replaces the
case's current result/evidence/thread when successful. Old checkpoints may
remain within their retention period but can no longer be selected through the
case API.

## Draft-submission API and service

Add:

```text
POST /api/v1/scans/{scan_id}/cases/{case_id}/draft
```

The endpoint requires:

- an officer-or-manager session;
- session-bound CSRF;
- `Idempotency-Key`;
- the expected current case revision in a strict request body; and
- an environment-bound scan/case identity match.

The request contains no vendor, quantity, amount, budget, offer, evidence, or
PO fields. `DraftSubmissionService` reads those only from the current persisted
case and its exact checkpoint.

The service accepts only `succeeded/approval_ready` with no draft and a
non-empty `workflow_thread_id`. It conditionally transitions the case to
`creating_draft`, records a sanitized `draft_requested` audit event, and
resumes the exact current graph thread. A successful draft interrupt persists
the existing `DraftRecord`, retains the same thread ID, and transitions to
`pending_approval`.

The action is one-way for the case revision. Once a draft exists, neither
refinement nor another draft request is available. Compatible repeat requests
return or repair the one current draft operation; they never create a second
PO. Concurrent/stale requests receive the existing safe revision-conflict or
validation envelope. Recovery inspects the durable case/checkpoint and uses
T28's case-origin idempotent MCP creation and reconciliation before any retry.

If the bounded draft operation cannot establish a safe outcome, the case moves
to the existing sanitized failure or reconciliation/manual-review behavior. It
never advances to manager approval without one verified draft snapshot.

## Frontend design

### Pre-draft state

For `succeeded/approval_ready`, the bottom of the recommendation page contains
the existing bounded refinement panel followed by a separate primary
**Create draft and send to manager** action. Both officers and managers see and
may use both controls. The action explains that submission locks refinement
for this recommendation. While accepted work is in `creating_draft`, controls
are disabled and the page polls with a specific progress message.

### Pending and decision states

The manager action area no longer repeats vendor, quantity, amount, evidence
digest, remaining budget, or overage cards. Those facts remain visible in the
existing recommendation, evidence, and budget sections. The pending-draft
summary gains the PO revision if it is not already visible elsewhere, keeping
the exact approval binding inspectable without duplicate cards.

The decision audit appears before the action area. For a manager and only while
the case is `pending_approval`, a compact action card is the final section on
the page:

- **Approve and confirm** is the primary action.
- **Reject** uses a modern destructive-outline treatment.
- Selecting **Reject** progressively reveals the bounded reason field,
  **Confirm rejection**, and **Cancel**; the reason is not permanently visible.
- An over-budget approval progressively presents the required exception
  acknowledgement and justification before enabling confirmation.
- Submission disables all competing controls and uses accessible live status
  and safe error messages.

On narrow screens, actions and revealed fields stack vertically. Keyboard
focus moves into an expanded rejection/exception form and returns sensibly when
cancelled. Officers see the pending draft and audit but never the manager action
card.

## Error handling and recovery

- Stale case revision: `REVISION_CONFLICT`; refresh the authoritative case.
- Case is no longer pre-draft eligible: safe `VALIDATION_FAILED`; do not resume
  any checkpoint.
- Missing or mismatched checkpoint: safe non-retryable validation/manual-review
  result; never guess a thread ID.
- Duplicate or lost HTTP response: reconcile the durable case/checkpoint and
  T28 origin-bound draft before attempting another write.
- MCP timeout or ambiguous creation: use the existing read-after-timeout
  reconciliation contract; unresolved ambiguity becomes
  `RECONCILIATION_REQUIRED`.
- Process restart during `creating_draft`: recovery uses the persisted state and
  checkpoint, never browser state, and can safely complete or surface the
  bounded failure.
- Officer decision attempt: `FORBIDDEN`; no decision record or audit transition
  is written.

All logs remain sanitized. Refinement notes, rejection reasons, budget values,
commercial values, credentials, and raw provider responses do not enter
operational logs.

## Observability

Reuse existing scan, MCP draft, decision, and action metrics. Add only the
minimum bounded draft-submission signal needed to distinguish
`accepted|replay|conflict|error` and the `creating_draft` duration. Do not add
identifiers, actors, commercial values, or free text as metric labels.

The existing pending-decision and purchase-order dashboards remain. The case
lifecycle/dashboard query must distinguish recommendation-ready cases from
drafts actually pending manager approval so the restored window is visible and
not counted as manager backlog.

## Testing and acceptance

### Backend and graph

- A valid initial recommendation pauses with `succeeded/approval_ready`, no
  draft, and the exact persisted case thread ID.
- Refinements zero through three remain possible before submission; the third
  attempt does not create a draft automatically.
- A draft submission resumes the exact latest thread, including
  `{case_id}:refine-{n}`, and creates at most one origin-bound draft.
- Refinement and draft submission are rejected after a draft exists.
- Duplicate, concurrent, stale, malformed, timeout, response-loss, restart,
  and ambiguous-write cases remain bounded and safe.
- The resulting pending checkpoint still resumes through T29 approve/confirm
  and reject/cancel without a second graph.

### Authorization

- A manager passes every officer route and service capability: manual scan,
  scan/case reads, refinement, draft submission, audit, and dependency health.
- An officer passes those same shared capabilities.
- Only the manager passes approve and reject.

### Frontend

- Officers and managers see refinement and draft submission before a draft.
- The draft button follows the refinement panel and locks while submitting.
- Pending cases have no refinement or draft button.
- The duplicate manager binding grid is absent.
- The audit precedes the bottom manager action card.
- Reject details are hidden until requested, require a bounded reason, and can
  be cancelled without mutation.
- Budget-exception controls appear only when required.
- Responsive, keyboard, focus, live-status, and safe-error behavior is covered.

### Integration

The real local path proves:

```text
API scan -> LangGraph recommendation interrupt
-> optional refinement thread
-> officer-or-manager draft submission
-> exact LangGraph resume
-> real Streamable HTTP MCP
-> fictional Odoo draft
-> manager approval or rejection
-> exact second resume
-> Odoo confirmation or cancellation
-> immutable audit
```

No supplier contact, payment, legal ordering, autonomous approval, or post-draft
refinement occurs.

## Documentation amendments required before implementation

After this written design is approved, the implementation plan must include
synchronized amendments to:

- `docs/spec.md`: role table, case state diagram, refinement/draft narrative,
  API table, UI description, error behavior, and traceability;
- `docs/plan.md`: T28/T29 boundary and verification steps;
- the approved bounded-refinement and T29 supporting plans where their old
  automatic-draft assumptions would otherwise remain contradictory; and
- `docs/implementation-status.md` after tests establish the corrected behavior.

Implementation remains on `feature/t29-manager-decision-lifecycle`; no dev or
production promotion occurs until the corrected local and real-service suites
pass.
