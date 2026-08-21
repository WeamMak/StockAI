# Pre-Draft Refinement and Manager Inheritance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a durable pre-draft refinement window, add an explicit officer-or-manager draft handoff, make manager permissions a tested superset of officer permissions, and replace the duplicated manager panel with compact bottom-of-page actions.

**Architecture:** The existing LangGraph gains a first human interrupt between validated reasoning and draft creation. `CaseRecord.workflow_thread_id` identifies the latest authoritative pre-draft checkpoint and continues to identify the same thread after draft creation reaches T29's decision interrupt. A narrow `DraftSubmissionService` validates and reserves the one-way handoff, resumes that exact thread, and persists the resulting draft before the existing manager decision lifecycle continues.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, LangGraph `interrupt()`/`Command`, DynamoDB, Python MCP SDK over Streamable HTTP, Odoo 19 JSON-2, React 19, TypeScript, Vite, Vitest, Testing Library, Prometheus, Grafana, pytest.

## Global Constraints

- Work only on `feature/t29-manager-decision-lifecycle`; do not push or promote during this correction.
- A valid recommendation must stop before draft creation until an officer or manager explicitly submits it.
- Refinement remains capped at three attempts, is allowed only before a draft exists, and keeps the 280-character untrusted-note boundary.
- Managers inherit every officer capability; approve and reject remain manager-only.
- The draft request body contains only the expected case revision. Commercial and evidence fields come from durable case/checkpoint state.
- Every state-changing HTTP action requires authentication, CSRF, optimistic revision validation, and an `Idempotency-Key` of 1–128 allowlisted identifier characters.
- Draft creation remains origin-idempotent and reconciles ambiguous Odoo writes before retry.
- No request-change, draft-edit, reapproval, supplier contact, payment, legal-order, or autonomous-approval path is added.
- Operational logs and metric labels never include free text, manager/officer identity, vendor identity, evidence digests, amounts, budgets, or credentials.
- Use TDD for behavior changes and keep the system runnable after every commit.

---

### Task 1: Synchronize the approved source-of-truth documents

**Files:**
- Modify: `docs/spec.md`
- Modify: `docs/plan.md`
- Modify: `docs/superpowers/specs/2026-08-18-bounded-case-refinement-design.md`
- Modify: `docs/superpowers/plans/2026-08-18-bounded-case-refinement.md`
- Modify: `docs/superpowers/specs/2026-08-21-t29-manager-decision-lifecycle-design.md`
- Modify: `docs/superpowers/plans/2026-08-21-t29-manager-decision-lifecycle.md`

**Interfaces:**
- Consumes: approved corrective design `docs/superpowers/specs/2026-08-21-pre-draft-refinement-and-manager-inheritance-design.md`.
- Produces: one non-contradictory specification and task boundary used by all later implementation tasks.

- [ ] **Step 1: Amend the main role and lifecycle specification**

Replace the manager role text with “All procurement-officer actions, plus approve, approve a budget exception, and reject.” Change the state flow to:

```text
GatheringEvidence -> RecommendationReady
RecommendationReady -> GatheringEvidence: bounded refinement
RecommendationReady -> CreatingDraft: officer or manager submits
CreatingDraft -> PendingApproval: one verified draft
PendingApproval -> Approved | Rejected
```

Add `POST /api/v1/scans/{scan_id}/cases/{case_id}/draft` to the HTTP table with officer-or-manager authorization, CSRF, idempotency, and expected revision. State that recommendation-ready UI shows refinement followed by **Create draft and send to manager**, while pending UI shows audit followed by manager-only bottom actions.

- [ ] **Step 2: Correct the T28/T29 task boundary in the main plan**

Change T28 completion from automatic draft creation after every valid recommendation to:

```text
T28 creates at most one evidence-bound draft only after an explicit
officer-or-manager submission of the latest recommendation checkpoint.
```

Retain T29's approval/rejection scope and add this correction as a prerequisite to T29 release verification. Remove statements that an initial/refined run creates its draft automatically.

- [ ] **Step 3: Reconcile the supporting specs and plans**

In the bounded-refinement documents, state that each successful attempt pauses before a draft and becomes the latest selectable checkpoint. In the T29 documents, state that `workflow_thread_id` is persisted at recommendation readiness and retained through the draft and manager-decision pauses.

- [ ] **Step 4: Verify the documentation is internally consistent**

Run:

```bash
rg -n "automatically creates|immediately creates|read actions plus|reason -> create_draft" \
  docs/spec.md docs/plan.md docs/superpowers/specs docs/superpowers/plans
```

Expected: no active requirement says a valid recommendation automatically creates a draft or that managers inherit only officer reads. Historical context may remain only when explicitly labeled superseded.

- [ ] **Step 5: Commit the synchronized requirements**

```bash
git add docs/spec.md docs/plan.md
git add -f docs/superpowers/specs/2026-08-18-bounded-case-refinement-design.md \
  docs/superpowers/plans/2026-08-18-bounded-case-refinement.md \
  docs/superpowers/specs/2026-08-21-t29-manager-decision-lifecycle-design.md \
  docs/superpowers/plans/2026-08-21-t29-manager-decision-lifecycle.md
git commit -m "docs: restore the pre-draft handoff"
```

### Task 2: Pause and safely resume LangGraph before draft creation

**Files:**
- Modify: `src/procurement/agent/nodes/walking_skeleton.py`
- Modify: `src/procurement/agent/graph.py`
- Modify: `tests/unit/agent/test_walking_skeleton.py`
- Create: `tests/unit/agent/test_graph.py`

**Interfaces:**
- Consumes: an `ApprovalReadyResult`, a configured LangGraph checkpointer, and the existing `create_draft`/`load_decision` nodes.
- Produces: `WalkingSkeletonNodes.await_draft_submission(state)`, graph node `await_draft_submission`, and `WalkingSkeletonWorkflow.aensure_draft(workflow_thread_id) -> ScanState`.

- [ ] **Step 1: Write the failing initial-pause test**

Add a checkpointer-backed graph test that invokes one approval-ready case and asserts:

```python
paused = await graph.ainvoke(
    INITIAL_STATE,
    config={"configurable": {"thread_id": CASE_ID}},
)

assert isinstance(paused["result"], ApprovalReadyResult)
assert "draft" not in paused
assert paused["__interrupt__"][0].value == {
    "phase": "recommendation_ready",
    "case_id": CASE_ID,
}
assert mcp.draft_requests == []
```

- [ ] **Step 2: Run the initial-pause test and verify red**

Run:

```bash
uv run pytest tests/unit/agent/test_walking_skeleton.py -v -k pre_draft
```

Expected: FAIL because the graph calls `create_draft` before any pre-draft interrupt.

- [ ] **Step 3: Add the server-owned pre-draft interrupt**

Add this bounded node behavior:

```python
async def await_draft_submission(self, state: ScanState) -> dict[str, object]:
    result = state.get("result")
    if not isinstance(result, ApprovalReadyResult):
        return {}
    action = interrupt(
        {
            "phase": "recommendation_ready",
            "case_id": f"{state['scan_id']}:{result.product_id}",
        }
    )
    if action != "create_draft":
        return {
            "result": UnresolvedResult(
                error_code=ErrorCode.VALIDATION_FAILED,
                message="The draft submission command was invalid.",
                retryable=False,
            )
        }
    return {}
```

When a checkpointer exists, route `reason -> await_draft_submission -> create_draft`. Without a checkpointer, end at the read-only recommendation; never perform an uncheckpointed draft write. Keep `create_draft -> load_decision` for the decision-enabled production graph.

- [ ] **Step 4: Write the failing idempotent workflow-resume tests**

Cover both the first resume and a repeat after the graph has reached `load_decision`:

```python
first = await workflow.aensure_draft(CASE_ID)
second = await workflow.aensure_draft(CASE_ID)

assert first["draft"].po_id == 41
assert second["draft"].po_id == 41
assert len(mcp.draft_requests) == 1
snapshot = await graph.aget_state(
    {"configurable": {"thread_id": CASE_ID}}
)
assert tuple(snapshot.next) == ("load_decision",)
```

Also assert an unknown thread or a thread paused at any node other than `await_draft_submission` raises a safe internal `DraftCheckpointError` without invoking MCP.

- [ ] **Step 5: Implement `aensure_draft` using checkpoint phase inspection**

Add a dedicated internal exception and implement:

```python
class DraftCheckpointError(RuntimeError):
    pass

async def aensure_draft(self, workflow_thread_id: str) -> ScanState:
    config = {"configurable": {"thread_id": workflow_thread_id}}
    snapshot = await self._graph.aget_state(cast(RunnableConfig, config))
    values = cast(ScanState, snapshot.values)
    if values.get("draft") is not None:
        return values
    if tuple(snapshot.next) != ("await_draft_submission",):
        raise DraftCheckpointError("workflow is not awaiting draft submission")
    result = await self._graph.ainvoke(
        Command(resume="create_draft"),
        config=cast(RunnableConfig, config),
    )
    return cast(ScanState, result)
```

Import `Command` at module scope and keep `aresume_decision` unchanged except for sharing the validated config helper.

- [ ] **Step 6: Run the graph suite and commit**

Run:

```bash
uv run pytest tests/unit/agent/test_walking_skeleton.py tests/unit/agent/test_graph.py -v
uv run mypy
```

Expected: all graph tests pass and mypy reports no issues.

```bash
git add src/procurement/agent/nodes/walking_skeleton.py src/procurement/agent/graph.py \
  tests/unit/agent/test_walking_skeleton.py \
  tests/unit/agent/test_graph.py
git commit -m "feat(agent): pause before draft creation"
```

### Task 3: Persist the latest recommendation-ready workflow thread

**Files:**
- Modify: `src/procurement/api/services/scans.py`
- Modify: `src/procurement/adapters/aws/dynamodb.py`
- Modify: `tests/unit/api/test_scans.py`
- Modify: `tests/unit/adapters/aws/test_dynamodb.py`

**Interfaces:**
- Consumes: Task 2's `recommendation_ready` interrupt and existing case/refinement thread naming.
- Produces: `succeeded/approval_ready` cases with `draft=None` and the exact latest `CaseRecord.workflow_thread_id` persisted in memory and DynamoDB.

- [ ] **Step 1: Write failing scan-service tests for the restored window**

Add tests proving:

```python
class RecommendationPauseWorkflow(RefinableWorkflow):
    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        completed = await super().ainvoke(state, config=config)
        candidate = state["candidates"][0]
        return {
            **completed,
            "__interrupt__": (
                Interrupt(
                    value={
                        "phase": "recommendation_ready",
                        "case_id": f"{state['scan_id']}:{candidate.product_id}",
                    }
                ),
            ),
        }

scan_id, case_id = await _approval_ready_case(client, csrf_headers)
initial_response = await client.get(
    f"/api/v1/scans/{scan_id}/cases/{case_id}"
)
initial = initial_response.json()
assert initial["status"] == "succeeded"
assert initial["result"]["outcome"] == "approval_ready"
assert initial["draft"] is None

refine = await client.post(
    f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
    headers=csrf_headers,
    json={"note": "Prioritize delivery."},
)
assert refine.status_code == 202
refined = await _poll_case_until_finished(client, scan_id, case_id)
assert refined["status"] == "succeeded"
stored = await repository.get_case(CaseId(Environment.DEV, case_id))
assert stored is not None
assert stored.workflow_thread_id == f"{case_id}:refine-1"
```

Assert the third refinement still ends without a draft. Assert reserving a new refinement clears the previously selectable `workflow_thread_id` while status is `running` so a concurrent draft submission cannot resume stale evidence.

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
uv run pytest tests/unit/api/test_scans.py -v -k "recommendation_ready or refinement_thread or third_refinement"
```

Expected: FAIL because the current service interprets the graph pause as a pending draft or fails to persist a pre-draft thread.

- [ ] **Step 3: Recognize and persist recommendation-ready pauses**

Add a helper that accepts only one interrupt with the exact bounded payload:

```python
def _is_recommendation_ready(state: ScanState, *, case_id: str) -> bool:
    interrupts = cast(Mapping[str, object], state).get("__interrupt__")
    if not interrupts or len(cast(Sequence[object], interrupts)) != 1:
        return False
    payload = cast(Any, interrupts)[0].value
    return payload == {"phase": "recommendation_ready", "case_id": case_id}
```

In `_run_case` and `_run_refinement`, apply the validated recommendation as `succeeded` and set the exact invoked thread ID when this pause is present. Clear `workflow_thread_id`, `draft`, `decision`, and stale errors when reserving a refinement.

- [ ] **Step 4: Preserve historical DynamoDB compatibility**

Keep `workflow_thread_id` optional when reading older case items. Add round-trip tests for:

```python
CaseRecord(
    status="succeeded",
    result=approval_ready_record(),
    draft=None,
    workflow_thread_id="scan-001:product-101:refine-3",
)
```

Assert historical records with no field still deserialize as `None`.

- [ ] **Step 5: Run persistence and API regressions, then commit**

Run:

```bash
uv run pytest tests/unit/api/test_scans.py tests/unit/adapters/aws/test_dynamodb.py -v
uv run mypy
```

Expected: all selected tests pass.

```bash
git add src/procurement/api/services/scans.py src/procurement/adapters/aws/dynamodb.py \
  tests/unit/api/test_scans.py tests/unit/adapters/aws/test_dynamodb.py
git commit -m "feat(scans): retain the pre-draft checkpoint"
```

### Task 4: Add the idempotent draft-submission API

**Files:**
- Create: `src/procurement/api/services/drafts.py`
- Create: `src/procurement/api/routes/drafts.py`
- Create: `tests/unit/api/test_drafts.py`
- Modify: `src/procurement/api/app.py`
- Modify: `src/procurement/api/services/scans.py`
- Modify: `src/procurement/ports/repositories.py`
- Modify: `src/procurement/adapters/aws/dynamodb.py`
- Modify: `tests/unit/adapters/aws/test_dynamodb.py`
- Test: `tests/unit/api/test_cases.py`

**Interfaces:**
- Consumes: Task 2 `WalkingSkeletonWorkflow.aensure_draft`, Task 3 pre-draft `workflow_thread_id`, `ApplicationRepository`, and existing audit/persistence semantics.
- Produces: `DraftWorkflow.aensure_draft(workflow_thread_id)`, `DraftSubmissionService.submit(...) -> AcceptedDraftSubmission`, `POST /api/v1/scans/{scan_id}/cases/{case_id}/draft`, and `ScanStatus.CREATING_DRAFT`.

- [ ] **Step 1: Write failing authorization, binding, and replay tests**

Define these public contracts in tests:

```python
response = await client.post(
    f"/api/v1/scans/{scan_id}/cases/{case_id}/draft",
    headers={**csrf_headers, "Idempotency-Key": "draft-submit-001"},
    json={"case_revision": 3},
)
assert response.status_code == 202
assert response.json() == {
    "case_id": case_id,
    "status": "creating_draft",
    "created": True,
}
```

Cover officer success, manager success, unauthenticated/CSRF denial, scan/case mismatch, strict-extra-field rejection, missing/invalid key, stale revision, non-approval result, missing checkpoint, existing draft, concurrent requests, same-key replay, and changed-key conflict.

- [ ] **Step 2: Run the route tests and verify red**

Run:

```bash
uv run pytest tests/unit/api/test_drafts.py -v
```

Expected: FAIL because the route and service do not exist.

- [ ] **Step 3: Add the durable request fields and transient status**

Extend the case model compatibly:

```python
class ScanStatus(StrEnum):
    CREATING_DRAFT = "creating_draft"

@dataclass(frozen=True, slots=True)
class CaseRecord:
    # existing fields remain unchanged
    draft_request_idempotency_key: str | None = None
```

Serialize the key only on the application case item, never into logs or API responses. Missing historical values deserialize as `None`.

- [ ] **Step 4: Implement the narrow service boundary**

Create exact public types:

```python
class DraftWorkflow(Protocol):
    async def aensure_draft(self, workflow_thread_id: str) -> ScanState:
        """Return the one existing or newly created checkpoint draft."""

@dataclass(frozen=True, slots=True)
class AcceptedDraftSubmission:
    case_id: str
    status: ScanStatus
    created: bool

class DraftSubmissionService:
    async def submit(
        self,
        *,
        case_id: str,
        expected_revision: int,
        actor_subject: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> AcceptedDraftSubmission:
        """Reserve and schedule one exact-checkpoint draft submission."""
```

`submit` must conditionally reserve `succeeded -> creating_draft`, retain the recommendation/evidence/thread, persist the idempotency key, append `draft_requested`, schedule the bounded resume, and return `202` semantics. A matching replay repairs an incomplete `creating_draft` operation; a mismatched key or revision raises `REVISION_CONFLICT`.

- [ ] **Step 5: Persist the resumed draft and audit transitions**

The background resume must accept only an existing checkpoint `PurchaseOrderDraft` and persist:

```python
pending = replace(
    current,
    revision=current.revision.next(),
    status=ScanStatus.PENDING_APPROVAL.value,
    draft=DraftRecord(
        po_id=draft.po_id,
        write_date=draft.write_date,
        state=draft.state,
        partner_id=draft.partner_id,
        currency_id=draft.currency_id,
        amount_total=draft.amount_total,
    ),
    updated_at=UtcTimestamp(now()),
)
```

Append `draft_created` after persistence. Map checkpoint mismatch to safe `VALIDATION_FAILED`, bounded MCP/provider failure to the existing safe failure record, and ambiguous unresolved creation to `RECONCILIATION_REQUIRED`. Never synthesize a draft or thread ID.

- [ ] **Step 6: Implement the strict FastAPI route and composition wiring**

Use:

```python
class DraftSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    case_revision: int = Field(strict=True, ge=1)

class AcceptedDraftSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    status: Literal["creating_draft", "pending_approval"]
    created: bool
```

The router depends on `OfficerPrincipalDep` and `CsrfDep`, validates the same allowlisted `Idempotency-Key` contract as decisions, verifies `case_id.startswith(f"{scan_id}:")`, and passes only `principal.user_id` plus server correlation ID to the service. Wire one shared repository/workflow/metrics instance in `create_app`.

- [ ] **Step 7: Run the backend slice and commit**

Run:

```bash
uv run pytest tests/unit/api/test_drafts.py tests/unit/api/test_scans.py \
  tests/unit/api/test_cases.py tests/unit/adapters/aws/test_dynamodb.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all selected checks pass.

```bash
git add src/procurement/api/services/drafts.py src/procurement/api/routes/drafts.py \
  src/procurement/api/app.py src/procurement/api/services/scans.py \
  src/procurement/ports/repositories.py src/procurement/adapters/aws/dynamodb.py \
  tests/unit/api/test_drafts.py tests/unit/api/test_cases.py \
  tests/unit/adapters/aws/test_dynamodb.py
git commit -m "feat(api): submit recommendations for draft creation"
```

### Task 5: Freeze manager inheritance and add the pre-draft client control

**Files:**
- Create: `frontend/src/components/DraftSubmissionPanel.tsx`
- Create: `frontend/tests/draft-submission.test.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/RecommendationPage.tsx`
- Modify: `frontend/src/presentation.ts`
- Modify: `frontend/tests/api-client.test.ts`
- Modify: `frontend/tests/recommendation.test.tsx`
- Modify: `tests/unit/api/auth/test_rbac.py`
- Modify: `tests/unit/api/test_scans.py`

**Interfaces:**
- Consumes: Task 4 draft endpoint and `creating_draft` status.
- Produces: `submitCaseForDraft(caseDetail, idempotencyKey, options?) -> AcceptedDraftSubmission`, `DraftSubmissionPanel`, and explicit manager coverage for every officer API capability.

- [ ] **Step 1: Write failing manager-inheritance tests**

Parameterize the existing manual-scan/read tests over both roles:

```python
@pytest.mark.parametrize("role", [UserRole.OFFICER, UserRole.MANAGER])
@pytest.mark.anyio
async def test_operator_can_scan_read_and_check_dependencies(role: UserRole) -> None:
    application = create_app(
        scan_workflow=SuccessfulWorkflow(),
        identity_provider=LocalIdentityProvider(role=role),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://testserver",
    ) as client:
        headers = await sign_in(client)
        created = await client.post("/api/v1/scans", headers=headers)
        scan_id = created.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
        case_id = finished["results"][0]["case_id"]
        detail = await client.get(
            f"/api/v1/scans/{scan_id}/cases/{case_id}"
        )
        dependencies = await client.get("/health/dependencies")

    assert created.status_code == 202
    assert detail.status_code == 200
    assert dependencies.status_code == 200
```

Parameterize the existing refinement success test over both roles and the Task
4 draft-submission success test over both roles. Retain separate tests asserting
officers receive `403 FORBIDDEN` from approve and reject while managers receive
the normal domain response. Together these tests enumerate scan creation,
list/detail reads, refinement, draft submission, audit, and dependency health.

- [ ] **Step 2: Run the RBAC tests and verify red**

Run:

```bash
uv run pytest tests/unit/api/auth/test_rbac.py tests/unit/api/test_scans.py -v -k manager
```

Expected: FAIL until the new draft capability and complete manager matrix exist.

- [ ] **Step 3: Write failing client and component tests**

Client tests assert an exact request:

```typescript
await submitCaseForDraft(caseDetail, "draft-submit-001");

expect(fetch).toHaveBeenCalledWith(
  `/api/v1/scans/${scanId}/cases/${encodeURIComponent(caseId)}/draft`,
  expect.objectContaining({
    method: "POST",
    headers: expect.objectContaining({
      "Content-Type": "application/json",
      "Idempotency-Key": "draft-submit-001",
      "X-CSRF-Token": "csrf-token",
    }),
    body: JSON.stringify({ case_revision: caseDetail.revision }),
  }),
);
```

Component tests render the control for both `officer` and `manager`, assert it follows the refinement panel, show the locking explanation, disable during submission, and exclude it for `pending_approval`.

- [ ] **Step 4: Run the frontend tests and verify red**

Run:

```bash
npm --prefix frontend run test -- --run api-client.test.ts \
  draft-submission.test.tsx recommendation.test.tsx
```

Expected: FAIL because the status, client method, and component do not exist.

- [ ] **Step 5: Implement strict client parsing and stable retry identity**

Add `creating_draft` to `ScanStatus` and define:

```typescript
export interface AcceptedDraftSubmission {
  case_id: string;
  status: "creating_draft" | "pending_approval";
  created: boolean;
}

export async function submitCaseForDraft(
  caseDetail: CaseDetail,
  idempotencyKey: string,
  options: RequestOptions = {},
): Promise<AcceptedDraftSubmission>;
```

Strictly require `caseDetail.revision`, exact response keys/types, CSRF, JSON content type, and the supplied stable key. The component stores `draft-${crypto.randomUUID()}` in `sessionStorage` under `stockai:draft:{case_id}:{revision}` before the call so a retry after response loss reuses it. Remove that entry only after polling observes `pending_approval` or a terminal safe failure.

- [ ] **Step 6: Implement the pre-draft handoff panel**

`DraftSubmissionPanel` accepts:

```typescript
interface DraftSubmissionPanelProps {
  caseDetail: CaseDetail;
  onAccepted: () => void;
}
```

Render **Create draft and send to manager** only for T27 `approval_ready`, `succeeded`, no draft, and a defined revision. Copy states that refinement becomes locked. On success invoke `onAccepted`; on safe API failure retain the stable key and show the bounded message. In `RecommendationPage`, render it immediately after `RefinementPanel`, poll `creating_draft`, and show “Creating fictional Odoo draft…” rather than the generic scan message.

- [ ] **Step 7: Run the role/client/UI slice and commit**

Run:

```bash
uv run pytest tests/unit/api/auth/test_rbac.py tests/unit/api/test_scans.py -v
npm --prefix frontend run test -- --run api-client.test.ts \
  draft-submission.test.tsx recommendation.test.tsx
npm --prefix frontend run typecheck
npm --prefix frontend run lint
```

Expected: all selected checks pass.

```bash
git add tests/unit/api/auth/test_rbac.py tests/unit/api/test_scans.py \
  frontend/src/api/client.ts frontend/src/components/DraftSubmissionPanel.tsx \
  frontend/src/pages/RecommendationPage.tsx frontend/src/presentation.ts \
  frontend/tests/api-client.test.ts frontend/tests/draft-submission.test.tsx \
  frontend/tests/recommendation.test.tsx
git commit -m "feat(frontend): add the pre-draft handoff"
```

### Task 6: Redesign the bottom manager action experience

**Files:**
- Modify: `frontend/src/components/ManagerDecisionPanel.tsx`
- Modify: `frontend/src/pages/RecommendationPage.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/manager-decision.test.tsx`
- Modify: `frontend/tests/recommendation.test.tsx`
- Test: `frontend/tests/audit-timeline.test.tsx`

**Interfaces:**
- Consumes: existing T29 `approveCase`, `rejectCase`, current session, case decision binding, and audit timeline.
- Produces: a compact manager-only bottom action card with progressive rejection and budget-exception forms.

- [ ] **Step 1: Write failing layout and interaction tests**

Assert:

```typescript
expect(screen.queryByText("Evidence digest", { selector: "dt" })).toBeNull();
expect(screen.queryByLabelText("Rejection reason")).toBeNull();

await user.click(screen.getByRole("button", { name: "Reject" }));
expect(screen.getByLabelText("Rejection reason")).toHaveFocus();
expect(screen.getByRole("button", { name: "Confirm rejection" })).toBeDisabled();

await user.click(screen.getByRole("button", { name: "Cancel rejection" }));
expect(screen.queryByLabelText("Rejection reason")).toBeNull();
```

In the page test, compare DOM order so `Decision audit` precedes `Manager actions` and `Manager actions` is the final case-page section. Assert the pending summary shows `PO #41` and its `write_date` revision.

- [ ] **Step 2: Run manager UI tests and verify red**

Run:

```bash
npm --prefix frontend run test -- --run manager-decision.test.tsx \
  recommendation.test.tsx audit-timeline.test.tsx
```

Expected: FAIL because the current duplicated grid and always-visible rejection field remain.

- [ ] **Step 3: Reduce `ManagerDecisionPanel` to action state**

Keep the evidence lookup internally for strict API binding, but remove `decision-binding` markup. Use explicit modes:

```typescript
type DecisionForm = "idle" | "reject" | "budget_exception";
const [form, setForm] = useState<DecisionForm>("idle");
```

For an in-budget case, **Approve and confirm** submits immediately. For an over-budget case, selecting it opens the checkbox and justification form; confirmation remains disabled until both are valid. **Reject** opens the reason form, moves focus to its textarea, and exposes **Confirm rejection** plus **Cancel rejection**. Any accepted action disables the competing action.

- [ ] **Step 4: Put audit and manager actions at the page bottom**

Render recommendation/evidence first, then pending/decision audit, then `ManagerDecisionPanel`. Keep the manager card absent for officers and non-pending states. Change its accessible heading from “Manager decision” to “Manager actions” and preserve the fictional/no-supplier-contact statement in compact copy without repeating commercial fields.

- [ ] **Step 5: Implement the modern responsive styles**

Replace the old grid rules with bounded classes:

```css
.manager-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.25rem;
  border: 1px solid var(--border);
  border-radius: 1rem;
  background: linear-gradient(145deg, #ffffff, #f7f9fd);
  box-shadow: 0 0.75rem 2rem rgb(31 50 81 / 8%);
}

.danger-button {
  border: 1px solid #b42318;
  color: #b42318;
  background: #fff;
}

@media (max-width: 42rem) {
  .manager-actions,
  .manager-actions__buttons {
    align-items: stretch;
    flex-direction: column;
  }
}
```

Use existing design tokens where they already express the same values. Add clear focus-visible, hover, disabled, error, and expanded-form styles without animations that ignore reduced-motion preferences.

- [ ] **Step 6: Run the complete frontend suite and commit**

Run:

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run test -- --coverage.enabled
npm --prefix frontend run build
```

Expected: all frontend checks pass.

```bash
git add frontend/src/components/ManagerDecisionPanel.tsx \
  frontend/src/pages/RecommendationPage.tsx frontend/src/styles.css \
  frontend/tests/manager-decision.test.tsx frontend/tests/recommendation.test.tsx \
  frontend/tests/audit-timeline.test.tsx
git commit -m "feat(frontend): modernize manager actions"
```

### Task 7: Add bounded draft-handoff observability

**Files:**
- Modify: `src/procurement/observability/metrics.py`
- Modify: `src/procurement/api/services/drafts.py`
- Modify: `deploy/kubernetes/base/observability/dashboards/agent-health.json`
- Modify: `tests/unit/observability/test_redaction.py`
- Modify: `tests/kubernetes/test_observability_content.py`

**Interfaces:**
- Consumes: Task 4 draft-submission outcomes and duration.
- Produces: `procurement_draft_submissions_total{result}` and `procurement_draft_submission_seconds`, both with bounded labels.

- [ ] **Step 1: Write failing metric and dashboard tests**

Assert only these result labels are accepted:

```python
metrics.observe_draft_submission(result="accepted", duration_seconds=0.2)
metrics.observe_draft_submission(result="untrusted-value", duration_seconds=0.3)

assert sample_value(registry, "procurement_draft_submissions_total", {"result": "accepted"}) == 1
assert sample_value(registry, "procurement_draft_submissions_total", {"result": "error"}) == 1
```

Dashboard tests require “Draft submission outcomes” and “Draft creation latency” panels and ensure recommendation-ready cases are not queried as pending manager decisions.

- [ ] **Step 2: Run observability tests and verify red**

Run:

```bash
uv run pytest tests/unit/observability/test_redaction.py \
  tests/kubernetes/test_observability_content.py -v -k draft
```

Expected: FAIL because the collectors and panels do not exist.

- [ ] **Step 3: Add and record bounded collectors**

Extend `AgentMetrics` with:

```python
draft_submissions: Counter
draft_submission_duration: Histogram

def observe_draft_submission(self, *, result: str, duration_seconds: float) -> None:
    safe_result = result if result in {"accepted", "replay", "conflict", "error"} else "error"
    self.draft_submissions.labels(result=safe_result).inc()
    if safe_result in {"accepted", "replay"}:
        self.draft_submission_duration.observe(duration_seconds)
```

Record accepted, compatible replay, conflict, and bounded error paths in `DraftSubmissionService`; do not label by case, actor, environment, vendor, or reason.

- [ ] **Step 4: Add the two Grafana panels**

Use:

```promql
sum by (result) (rate(procurement_draft_submissions_total[5m]))
```

and:

```promql
histogram_quantile(0.95, sum by (le) (rate(procurement_draft_submission_seconds_bucket[5m])))
```

Keep “Pending decisions” based only on accepted manager decisions minus successful terminal PO actions; do not mix `succeeded/approval_ready` cases into that panel.

- [ ] **Step 5: Run observability validation and commit**

Run:

```bash
uv run pytest tests/unit/observability/test_redaction.py tests/kubernetes -v
make kubernetes-validate
```

Expected: tests pass and Kubeconform reports zero invalid resources and zero errors for dev and prod.

```bash
git add src/procurement/observability/metrics.py src/procurement/api/services/drafts.py \
  deploy/kubernetes/base/observability/dashboards/agent-health.json \
  tests/unit/observability/test_redaction.py tests/kubernetes/test_observability_content.py
git commit -m "feat(observability): monitor draft submissions"
```

### Task 8: Prove the corrected lifecycle over real transports

**Files:**
- Modify: `tests/integration/test_api_agent_mcp.py`
- Modify: `tests/integration/test_dynamodb_local.py`
- Modify: `tests/e2e/test_local_stack.py`
- Modify: `tests/support/fake_odoo/adapter.py`
- Modify: `docs/implementation-status.md`

**Interfaces:**
- Consumes: Tasks 2–7 and the existing fake Odoo, real Streamable HTTP MCP server, DynamoDB Local, and Compose fixtures.
- Produces: executable evidence that initial and refined handoffs both survive persistence and continue through T29 without duplicate drafts or widened authority.

- [ ] **Step 1: Rewrite the real-transport happy path around the explicit handoff**

Change the integration sequence to assert:

```python
case = await poll_case(client, scan_id, case_id, expected="succeeded")
assert case["draft"] is None

refined = await client.post(
    f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
    headers=officer_headers,
    json={"note": "Prioritize delivery for this run."},
)
assert refined.status_code == 202

ready = await poll_case(client, scan_id, case_id, expected="succeeded")
submitted = await client.post(
    f"/api/v1/scans/{scan_id}/cases/{case_id}/draft",
    headers={**officer_headers, "Idempotency-Key": "draft-submit-integration-001"},
    json={"case_revision": ready["revision"]},
)
assert submitted.status_code == 202

pending = await poll_case(client, scan_id, case_id, expected="pending_approval")
assert pending["draft"] is not None
assert fake_odoo.create_draft_calls == 1
```

Then sign in as manager and run the existing approve/confirm path. Add a second case for reject/cancel if the current test does not already exercise it.

- [ ] **Step 2: Add persistence and response-loss recovery coverage**

In DynamoDB Local, persist a `succeeded` case with a refinement-specific thread, replace the API/service process, submit the draft with one key, simulate a lost response, repeat the same key, and assert one draft plus `pending_approval`. Repeat process replacement at the manager-decision interrupt and assert the existing T29 resume still reaches its terminal state.

- [ ] **Step 3: Run focused integration and verify red before final wiring fixes**

Run:

```bash
uv run pytest tests/integration/test_api_agent_mcp.py \
  tests/integration/test_dynamodb_local.py -v
```

Expected before completing fixture updates: FAIL where tests still expect immediate `pending_approval` or omit explicit submission.

- [ ] **Step 4: Update Compose E2E expectations**

Make the successful E2E scenario stop at recommendation readiness, submit the draft through the public API as an officer or manager, observe `pending_approval`, then complete the manager decision. Keep one safe failure scenario proving officers cannot call approve/reject.

- [ ] **Step 5: Install the pinned workflow linter when it is absent**

Run the same checksum-verified installation used by CI:

```bash
mkdir -p .tools
curl --fail --location --silent --show-error \
  --output /tmp/actionlint.tar.gz \
  https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz
printf '%s  %s\n' \
  8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 \
  /tmp/actionlint.tar.gz | sha256sum --check --strict
tar --extract --gzip --file /tmp/actionlint.tar.gz \
  --directory .tools actionlint
```

Expected: checksum validation succeeds and `.tools/actionlint -version`
reports `1.7.12`. Do not commit `.tools` or the downloaded archive.

- [ ] **Step 6: Run all local acceptance gates**

Run:

```bash
ACTIONLINT=.tools/actionlint make check
make test-integration
make test-e2e
make odoo-contract
make compose-validate
make kubernetes-validate
git diff --check
```

Expected: every available command exits zero. Record exact counts from command output; do not reuse older counts.

- [ ] **Step 7: Update truthful implementation status**

Record the corrected initial/refined workflow, manager-superset authorization evidence, exact test counts, Docker/Kubeconform/Odoo/DynamoDB results, and any genuinely unrun live dev/prod checks. Do not mark `make smoke-dev`, `make smoke-prod`, Argo reconciliation, or promotion complete unless executed successfully.

- [ ] **Step 8: Commit the acceptance evidence**

```bash
git add tests/integration/test_api_agent_mcp.py tests/integration/test_dynamodb_local.py \
  tests/e2e/test_local_stack.py tests/support/fake_odoo/adapter.py \
  docs/implementation-status.md
git commit -m "test(t29): prove explicit draft handoff"
```

## Completion criteria

- A real successful scan exposes refinement before any draft is created.
- Either an officer or manager can optionally refine and explicitly submit the latest recommendation.
- Submission resumes only the latest persisted checkpoint and creates at most one fictional Odoo draft.
- A pending draft permanently closes refinement and continues through unchanged T29 approval/rejection safety.
- Managers pass every officer capability test; officers still fail manager-only decisions.
- The manager action section contains no duplicated detail grid, appears at the bottom after audit, and progressively reveals rejection/exception fields.
- All deterministic, Docker-backed, real-transport, Odoo, DynamoDB, frontend, observability, and Kubernetes checks available locally pass.
- The worktree contains no secret or unrelated change, and dev/prod deployment remains a separate explicit release action.
