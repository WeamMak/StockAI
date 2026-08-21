# T29 Manager-Decision Lifecycle Design

**Date:** 2026-08-21

**Status:** Design approved in conversation; written specification awaiting user review

**Parent specification:** `docs/spec.md` sections 6, 7.3, 8.6, 11.3, 13, 19, 20, and 21

**Parent implementation task:** `docs/plan.md` T29

## 1. Objective and fixed scope

T29 completes the manager-controlled lifecycle for the evidence-bound draft
created by T28. An authenticated procurement manager can either approve the
exact current draft for confirmation or reject it for cancellation. Every
decision and transition remains immutable, attributable, environment-bound,
idempotent, revision-aware, and auditable.

This slice includes:

- manager-only approve and reject HTTP endpoints;
- immutable approval and rejection records in application persistence;
- a 30-minute approval-validity window;
- explicit approval of an over-budget exception with justification;
- LangGraph resumption after the durable human decision;
- independently defended MCP confirmation and cancellation tools;
- atomic Odoo confirmation or cancellation through the existing StockAI
  add-on methods;
- reconciliation before any retry of an ambiguous write;
- manager decision controls and a chronological audit timeline in React;
- decision, confirmation, cancellation, conflict, reconciliation, and latency
  observability;
- focused unit, integration, contract, UI, configuration, and live smoke
  verification.

The slice does not add request-change, draft-update, reapproval, supplier
contact, payment, legal ordering, autonomous approval, or a second LLM call.
The existing Odoo `action_stockai_update_draft` primitive remains unwired.

## 2. Decisions and alternatives

### 2.1 Workflow ownership

Three designs were considered:

1. Resume the existing LangGraph thread after persisting the manager decision.
2. Let FastAPI perform confirmation or cancellation directly.
3. Move the whole manager-decision workflow into MCP.

The first design is selected. It preserves the approved human-interrupt state
machine and restart behavior while keeping browser authentication in FastAPI
and ERP authorization defense in MCP. API-only orchestration would bypass the
approved graph lifecycle. MCP-owned orchestration would mix end-user identity
and application workflow with the ERP tool boundary.

### 2.2 Decision records

Approval and rejection use separate immutable typed records. A record is
created with a conditional write under a stable decision identity derived from
the environment, case, decision type, and PO revision. A compatible replay
returns the existing record or terminal result. A different payload using the
same idempotency key, a second decision type for the same revision, or a
decision against a later revision is a conflict rather than an update.

An approval expires 30 minutes after `decided_at`. Expiry controls whether the
approval may authorize confirmation; it does not delete or alter the audit
record. Normal environment retention remains the parent specification's 30
days in dev and one year for production decision/audit records.

### 2.3 Human text

Rejection reasons and budget-exception justifications use the repository's
existing short human-note boundary: after trimming they must be non-empty,
contain no disallowed control characters, and contain at most 280 Unicode code
points. They are untrusted audit text, never prompt input or a policy override,
and must be escaped by normal React rendering. They are persisted for the
authorized audit view but excluded from operational logs and metric labels.

## 3. Domain model and invariants

The decision domain owns typed values for decision type, decision ID,
idempotency binding, and bounded human text.

An immutable approval binds all of the following:

- environment and case ID;
- manager subject and manager role at decision time;
- decision type `approve`;
- case revision expected by the client;
- Odoo PO ID and exact `write_date` revision;
- vendor/partner ID, quantity, currency, unit price, and normalized total;
- budget status, budgeted/committed/remaining values, exact overage, and
  exception-required state;
- explicit exception flag and justification when required;
- immutable evidence digest;
- idempotency key;
- decision timestamp and 30-minute authorization expiry.

An immutable rejection binds the environment, case ID, manager, decision type
`reject`, expected case revision, PO ID and exact PO revision, bounded reason,
idempotency key, decision time, and evidence digest. A rejection does not
authorize confirmation and does not expire as audit evidence.

The following invariants hold:

- only a manager can create either decision;
- the case must be `pending_approval` with a current T28 draft;
- client-supplied binding facts must equal the server's current case,
  recommendation, evidence, budget, and draft facts;
- over-budget approval requires `budget_exception=true` and a non-empty
  justification; in-budget approval rejects an invented exception;
- no approval can be mutated, extended, or reused for another PO revision;
- at most one decision wins for a case revision under concurrency;
- neither the graph nor MCP trusts browser-supplied commercial facts after the
  immutable record has been written;
- all write outcomes are reconciled before another write could be attempted.

## 4. Persistence boundaries

`ApplicationRepository` gains explicit conditional decision operations and
bounded chronological audit reads. The in-memory and DynamoDB adapters expose
the same semantics.

Decision items use the environment-scoped application table and a key shape
that supports:

- conditional creation of one decision for a case/PO revision;
- direct lookup by immutable decision ID;
- strongly consistent approval lookup from MCP;
- idempotency-key conflict detection;
- a case decision guard that makes approve-versus-reject races deterministic;
- retention TTL without using TTL as a correctness mechanism.

The API writes the immutable decision before it resumes LangGraph. Case state
updates continue to use `CaseRecord.revision` optimistic concurrency. Audit
events remain append-only and gain a bounded case query ordered by occurrence
time plus a deterministic tie-breaker. Audit responses contain decision facts
needed by an authorized user, but operational logs retain identifiers and safe
codes only.

The MCP runtime receives a decision-reader port backed by DynamoDB in deployed
mode. Confirmation uses a strongly consistent read. Local and test modes use
the equivalent in-memory implementation; the MCP server never imports API or
agent modules.

## 5. API and graph flow

### 5.1 Approve

1. `POST /api/v1/cases/{case_id}/approve` authenticates a manager, validates
   CSRF, requires a bounded idempotency key, and parses the exact expected case
   and draft binding.
2. The decision service strongly reads the current case and rejects a wrong
   environment, missing draft, non-pending state, stale case/PO revision,
   altered vendor/quantity/amount/currency/budget/evidence, or invalid budget
   exception.
3. A conditional write stores the immutable approval and decision guard.
4. The service appends a sanitized `manager_approved` audit transition and
   resumes the same LangGraph `thread_id` with only the decision record ID.
5. The graph reloads its checkpoint, rechecks the case/decision binding through
   the application service boundary, and calls `confirm_purchase_order` over
   real Streamable HTTP MCP.
6. MCP strongly reads the immutable approval, verifies identity/role,
   environment, decision type, expiry, case, PO, complete commercial and budget
   binding, evidence digest, and idempotency binding.
7. MCP reads the current Odoo PO snapshot immediately before the write. Only an
   exact current draft calls `action_stockai_confirm(expected)`.
8. The Odoo add-on locks and rereads the PO in the same transaction, compares
   its expected snapshot, then delegates to standard `button_confirm`.
9. The graph persists `confirmed` with the final PO reference and appends the
   immutable audit transition.

The endpoint returns `202 Accepted` once the decision is durably accepted;
the existing polling model reports `confirming`, `confirmed`, or a safe
failure/reconciliation state. A compatible replay may return the current
accepted or terminal representation without resuming twice.

### 5.2 Reject

The reject endpoint performs the same authentication, CSRF, idempotency,
revision, environment, and current-draft checks, persists an immutable
rejection plus the decision guard, appends `manager_rejected`, and resumes the
same graph thread with the decision ID. The rejection branch calls
`cancel_draft_purchase_order`; MCP verifies the rejection record and calls only
`action_stockai_cancel_draft(expected)`. A successful or already-cancelled
reconciliation closes the case as `cancelled` while preserving the rejection
record and audit history.

### 5.3 Audit

`GET /api/v1/cases/{case_id}/audit` remains officer-or-manager readable and
returns a bounded oldest-first timeline. Events expose actor, time, transition,
source revision, correlation ID, evidence digest, decision ID/type, PO
revision, and authorized human text when applicable. It never exposes secrets,
raw provider failures, prompt content, or internal exception details.

## 6. MCP and Odoo write safety

The MCP server adds only `confirm_purchase_order` and
`cancel_draft_purchase_order`, both annotated as write-capable. Strict request
and response schemas forbid extra fields and bind every identifier to the MCP
server's configured environment.

The confirmation tool accepts the immutable approval record ID rather than an
untrusted approval payload. The cancellation tool likewise accepts the
immutable rejection record ID. Each tool independently reconstructs and checks
the expected binding from its strong decision read and the current Odoo
snapshot.

Odoo write adapters make one bounded 15-second action attempt with SDK-level
retries disabled. A transport timeout, connection loss, or malformed response
after the request may have committed is ambiguous. The tool then rereads Odoo
by PO ID/origin and the idempotency/decision state:

- expected terminal state found: return an idempotent prior result;
- unchanged matching draft found: one new attempt is permitted only when the
  evidence proves the previous request did not commit;
- changed, incompatible, or unreadable state: return
  `RECONCILIATION_REQUIRED` and do not write again.

Stale state, authorization, validation, policy, and environment failures are
permanent. An expired approval returns `APPROVAL_STALE` and never reaches
Odoo. A standard Odoo method failure rolls back atomically and returns a safe
error.

## 7. React experience

The recommendation page keeps the T28 pending-draft summary and adds one
manager decision panel when the authenticated session role is `manager` and
the case is `pending_approval`.

The panel shows the exact PO ID/revision, vendor, quantity, currency/amount,
evidence digest, budget status, remaining amount, and overage before any
decision. In-budget approval is one explicit action. Over-budget approval
requires a visible exception checkbox and a non-empty justification before the
button becomes eligible. Rejection requires a reason. Both actions show
pending, accepted, conflict, stale, and reconciliation states without claiming
success before polling observes it.

Officers see the same evidence and audit timeline but no decision controls.
After any accepted decision, controls become disabled while the case polls.
Confirmed and cancelled cases show the terminal result and immutable timeline.
There is no request-change control or client contract.

## 8. Errors, concurrency, and recovery

The existing safe error envelope is extended only as needed for stable codes:

- `FORBIDDEN` for non-manager decisions;
- `VALIDATION_FAILED` for malformed or altered bindings;
- `REVISION_CONFLICT` for a stale case revision;
- `APPROVAL_STALE` for an expired or mismatched approval/PO revision;
- `BUDGET_JUSTIFICATION_REQUIRED` for a missing required exception;
- `RECONCILIATION_REQUIRED` for an ambiguous unresolved write;
- existing MCP/Odoo availability codes for pre-write dependency failures.

The conditional decision guard decides concurrent approve/reject calls. The
winner proceeds; a byte-for-byte compatible replay is idempotent; every other
loser receives a conflict. If the process exits after the decision write but
before graph resumption, a retry finds the same record and resumes safely. If
it exits during the MCP/Odoo boundary, the resumed workflow reconciles before
any new write.

## 9. Observability

Bounded metrics include decision requests and outcomes by environment,
decision type, and safe result; approval-to-confirmation and
rejection-to-cancellation latency; confirmation/cancellation attempts and
outcomes; stale/replay/conflict counts; reconciliation-required count; and
write-reconciliation duration. No metric label includes case IDs, users,
vendors, prices, evidence hashes, or human text.

Structured logs include correlation ID, case ID, decision ID, safe state,
operation, retry/reconciliation counts, duration, and safe error code. They
exclude amounts, budget data, manager text, commercial evidence, and secrets.

The existing Grafana application and dependency dashboards gain manager-
decision lifecycle panels. Alerts are limited to actionable sustained
confirmation/cancellation failure or any unresolved write reconciliation;
ordinary manager rejection, stale client input, and low traffic do not alert.

## 10. Testing and verification

Implementation proceeds test-first in these slices:

1. Domain validation and repository contracts for immutable decisions,
   30-minute expiry, environment binding, idempotency, concurrency, and audit
   ordering.
2. Decision-service and FastAPI tests for manager/officer roles, CSRF, altered
   fields, budget exception rules, stale/current revisions, replay, and
   approve-versus-reject races.
3. LangGraph interrupt/resume tests for approve, reject, process restart, and
   duplicate resume.
4. MCP and Odoo adapter tests for strong approval/rejection reads, exact
   binding, atomic actions, response loss, stale revision, idempotent terminal
   state, and reconciliation-required behavior.
5. React tests for role visibility, exact evidence/revision display, in-budget
   approval, over-budget exception, rejection, disabled/pending controls, safe
   errors, terminal states, and chronological audit.
6. Metrics, log-redaction, dashboard, alert, Kubernetes/configuration, and
   release-contract tests.
7. Real Streamable HTTP integration plus disposable Odoo/DynamoDB contract
   tests for happy approval, over-budget approval, rejection, replay, stale,
   concurrency, timeout, and malformed response paths.

Repository verification runs focused suites first, then `make check`,
`make test-integration`, `make odoo-contract`, `make kubernetes-validate`, and
`git diff --check`. Live completion follows the repository workflow: publish
one four-image dev release, reconcile through Argo CD, run authenticated dev
smoke and audit/alert inspection, promote the identical immutable artifact
through the protected `main` approval gate, and run production smoke. No live
or production result is claimed unless it actually passes.

## 11. Requirements traceability

| Requirement | T29 evidence |
|---|---|
| CR-02, business value | Complete approval-to-confirmation and rejection-to-cancellation lifecycle with measurable latency |
| CR-05, HTTP API | Authenticated, CSRF-protected, revisioned approve/reject/audit endpoints |
| CR-06, real MCP | Confirm and cancel tools invoked over Streamable HTTP in integration and live interaction |
| CR-12, testing | Unit, integration, contract, UI, concurrency, replay, timeout, restart, and real-environment coverage |
| CR-13, observability | Bounded metrics, sanitized logs, dashboards, and actionable alerts |
| CR-15, security | Manager-only authority, immutable approvals, strong defense-in-depth revalidation, untrusted text handling, and no blind write retry |

## 12. Completion criteria

T29 is complete only when every confirmed PO is backed by one unexpired,
immutable manager approval matching the current evidence and exact Odoo PO
revision; every accepted rejection is preserved and cancels or safely enters
explicit reconciliation; concurrent, stale, replayed, altered, and ambiguous
requests cannot produce an unauthorized or duplicate write; the bounded UI and
audit timeline work for the correct roles; and the required offline and live
verification has actually passed. No request-change/update/reapproval product
path exists.
