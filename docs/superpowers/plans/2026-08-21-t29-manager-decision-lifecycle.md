# T29 Manager-Decision Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the manager-only approve/confirm and reject/cancel lifecycle for the evidence-bound Odoo draft created by T28.

**Architecture:** FastAPI validates the authenticated manager and exact current case binding, conditionally persists one immutable decision, and resumes the existing paused LangGraph thread with the decision ID. The graph loads the durable decision, routes to one of two write-capable MCP tools, and the MCP server independently rereads the decision and current Odoo snapshot before calling the existing atomic StockAI Odoo confirm or cancel method; ambiguous outcomes reconcile before any repeat write.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, LangGraph `interrupt()`/`Command`, Python MCP SDK over Streamable HTTP, DynamoDB, Odoo 19 JSON-2, React, TypeScript, Vite, Prometheus, Grafana, Kubernetes/Kustomize, pytest, Vitest.

## Global Constraints

- Work only on `feature/t29-manager-decision-lifecycle`, created from the latest protected `main`; never push feature work directly to `main`.
- Preserve the approved T28 idempotent draft and checkpoint. Persist and resume its exact draft-owning `workflow_thread_id`; this is the case ID for the initial run and `{case_id}:refine-{n}` when refinement produced the draft. Do not create a second graph or decision agent.
- Approval authorization expires exactly 30 minutes after `decided_at`; retention TTL is not a correctness check and must not erase the immutable audit early.
- Rejection reasons and budget-exception justifications are trimmed, nonblank, control-character-free, and at most 280 Unicode code points.
- Every state-changing HTTP endpoint requires an authenticated manager, CSRF, `Idempotency-Key`, and an expected case/PO revision.
- Confirmation independently performs a strongly consistent approval read and matches environment, case, PO, vendor, quantity, amount, budget, evidence digest, and exact current PO revision.
- Over-budget confirmation requires `budget_exception=true` and a non-empty justification. In-budget requests cannot invent an exception.
- Odoo writes use only `action_stockai_confirm(expected)` and `action_stockai_cancel_draft(expected)`; do not assign `purchase.order.state` directly.
- Never blindly retry an Odoo write. Reconcile DynamoDB and Odoo after a timeout, response loss, or malformed post-write response.
- Treat MCP output and all human text as untrusted. Do not send decision text to Bedrock or interpolate it into the system prompt.
- Operational logs and metric labels must not contain commercial amounts, budget values, evidence hashes, manager text, user email, or secrets.
- Do not create a request-change API, graph branch, MCP update tool, React action, or reapproval path. Keep the dormant `CaseState` model unwired, as T28 explicitly decided; the active `ScanStatus`/API/graph/UI contract has no request-change path.
- Supplier contact, payment, email, EDI, real legal ordering, and autonomous approval remain excluded.
- Use test-first red-green-refactor cycles. Do not claim Docker, live dev, or production verification unless the command actually passes.

---

## File Structure

### New focused modules

- `src/procurement/domain/decisions.py` — immutable approval/rejection values and validation.
- `src/procurement/ports/decisions.py` — decision read/write protocol shared without importing API or MCP internals.
- `src/procurement/api/services/decisions.py` — manager authorization-independent application orchestration, binding checks, persistence, resume, and terminal case updates.
- `src/procurement/api/routes/decisions.py` — Pydantic HTTP contracts and manager/CSRF/idempotency dependencies.
- `src/procurement/mcp_server/tools/confirm.py` — strong approval validation and confirmation reconciliation.
- `src/procurement/mcp_server/tools/cancel_draft.py` — strong rejection validation and cancellation reconciliation.
- `frontend/src/components/ManagerDecisionPanel.tsx` — manager-only approve/exception/reject controls.
- `frontend/src/components/AuditTimeline.tsx` — bounded chronological case audit.

### Existing modules changed at their established seams

- `src/procurement/domain/audit.py` — decision audit reference. Leave dormant `src/procurement/domain/states.py` unchanged and unwired.
- `src/procurement/ports/repositories.py`, `src/procurement/adapters/aws/dynamodb.py` — application records plus in-memory/DynamoDB semantics.
- `src/procurement/ports/erp.py`, `src/procurement/adapters/odoo/client.py`, `src/procurement/adapters/odoo/draft.py` — one-shot atomic action and snapshot mapping.
- `src/procurement/ports/mcp.py`, `src/procurement/mcp_server/schemas.py`, `src/procurement/mcp_server/server.py` — strict consumer/provider write contracts.
- `src/procurement/agent/state.py`, `src/procurement/agent/graph.py`, `src/procurement/agent/nodes/walking_skeleton.py` — resume, load decision, route, confirm/cancel.
- `src/procurement/bootstrap/api.py`, `src/procurement/bootstrap/mcp.py`, `src/procurement/api/app.py` — compose the same environment-bound decision repository into both processes.
- `src/procurement/api/routes/scans.py`, `src/procurement/api/routes/cases.py`, `src/procurement/api/services/scans.py` — terminal status, decision summary, and audit projection while preserving recommendation evidence.
- `frontend/src/api/client.ts`, `frontend/src/pages/RecommendationPage.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css` — typed requests, role propagation, terminal polling, and UI.
- `src/procurement/observability/metrics.py`, `src/procurement/mcp_server/observability.py`, Grafana dashboard JSON, alert rules, Compose/config tests — bounded signals and runtime wiring.

---

### Task 1: Freeze the decision domain and its bounded active contract

**Files:**
- Create: `src/procurement/domain/decisions.py`
- Create: `tests/unit/domain/test_decisions.py`

**Interfaces:**
- Consumes: `Environment`, `CaseId`, `Revision`, `UtcTimestamp`, `DraftRecord`, and authoritative recommendation/evidence values.
- Produces: `DecisionId`, `DecisionType`, `DecisionText`, `ApprovalRecord`, `RejectionRecord`, `DecisionRecord`, `APPROVAL_VALIDITY = timedelta(minutes=30)`, and `decision_id_for(environment: Environment, case_id: CaseId, decision_type: DecisionType, po_id: int, po_write_date: str) -> DecisionId`.

- [ ] **Step 1: Write failing domain tests**

```python
def test_approval_binds_exact_facts_and_expires_after_thirty_minutes() -> None:
    record = approval_record(decided_at=NOW)
    assert record.expires_at == UtcTimestamp(NOW.value + timedelta(minutes=30))
    assert record.quantity == Decimal("25.000000")
    assert record.normalized_cost == Decimal("312.500000")
    assert record.evidence_digest == "sha256:" + "a" * 64


@pytest.mark.parametrize("text", ["", "   ", "x" * 281, "bad\x07text"])
def test_decision_text_rejects_blank_oversized_or_control_text(text: str) -> None:
    with pytest.raises(DomainValidationError):
        DecisionText(text)


```

- [ ] **Step 2: Run the focused tests and verify red**

Run: `uv run pytest tests/unit/domain/test_decisions.py -v`

Expected: FAIL because `procurement.domain.decisions` does not exist.

- [ ] **Step 3: Implement strict immutable decision values**

Use separate dataclasses, not an optional-field catch-all:

```python
APPROVAL_VALIDITY = timedelta(minutes=30)
MAX_DECISION_TEXT_LENGTH = 280


class DecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DecisionText:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip() if isinstance(self.value, str) else ""
        if (
            not normalized
            or len(normalized) > MAX_DECISION_TEXT_LENGTH
            or any(ord(character) < 32 for character in normalized)
        ):
            raise DomainValidationError("The manager text is invalid.")
        object.__setattr__(self, "value", normalized)


DecisionRecord = ApprovalRecord | RejectionRecord
```

`ApprovalRecord` must contain decision/case IDs, manager subject and role,
case revision, full T28 draft snapshot, offer/vendor, quantity, unit price,
currency, normalized cost, complete budget snapshot, evidence digest,
exception flag/justification, idempotency key, `decided_at`, and `expires_at`.
`RejectionRecord` contains the same identity/revision boundary, evidence digest,
bounded reason, idempotency key, and `decided_at` but no authorization expiry.
Derive a bounded stable decision ID from SHA-256 of environment, case, decision
type, PO ID, and PO `write_date`; never include the manager text in the ID.

Do not import or activate the dormant `domain/states.py::CaseState` model. The
implemented lifecycle continues through `CaseRecord.status`/`ScanStatus`, as
approved in T28. Request-change remains absent from every active service, API,
graph, MCP, and React contract.

- [ ] **Step 4: Run domain tests and typing**

Run: `uv run pytest tests/unit/domain/test_decisions.py -v && uv run mypy src/procurement/domain`

Expected: PASS.

- [ ] **Step 5: Commit the domain boundary**

```bash
git add src/procurement/domain/decisions.py tests/unit/domain/test_decisions.py
git commit -m "feat(decisions): define immutable manager decisions"
```

### Task 2: Add decision persistence and chronological audit contracts

**Files:**
- Create: `src/procurement/ports/decisions.py`
- Modify: `src/procurement/domain/audit.py:36-95`
- Modify: `src/procurement/ports/repositories.py:179-500`
- Modify: `src/procurement/api/services/scans.py`
- Create: `tests/unit/ports/test_decisions.py`
- Test: `tests/unit/domain/test_audit.py`
- Test: `tests/unit/api/test_scans.py`

**Interfaces:**
- Consumes: Task 1 `DecisionRecord` and `DecisionId`.
- Produces: `DecisionReader.get_decision`, `DecisionRepository.create_decision`, `DecisionCreateResult`, `ApplicationRepository.list_audit`, `CaseRecord.workflow_thread_id`, and in-memory atomic decision-guard semantics.

- [ ] **Step 1: Write failing port and in-memory repository tests**

```python
async def test_one_decision_wins_and_compatible_replay_is_idempotent() -> None:
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    approval = approval_record()
    first = await repository.create_decision(approval, retention_expires_at=RETENTION)
    replay = await repository.create_decision(approval, retention_expires_at=RETENTION)
    assert first.created is True
    assert replay == DecisionCreateResult(record=approval, created=False)
    with pytest.raises(DecisionConflictError):
        await repository.create_decision(
            rejection_record_for_same_po_revision(),
            retention_expires_at=RETENTION,
        )


async def test_audit_is_oldest_first_with_event_id_tie_breaker() -> None:
    await repository.append_audit(event(event_id="b", occurred_at=NOW), expires_at=RETENTION)
    await repository.append_audit(event(event_id="a", occurred_at=NOW), expires_at=RETENTION)
    assert [row.event_id for row in await repository.list_audit(CASE_ID, limit=20)] == ["a", "b"]


def test_pending_case_retains_the_exact_draft_owning_thread() -> None:
    original = pending_case(workflow_thread_id=CASE_ID.value, refinement_count=0)
    refined = pending_case(
        workflow_thread_id=f"{CASE_ID.value}:refine-2", refinement_count=2
    )
    assert original.workflow_thread_id == CASE_ID.value
    assert refined.workflow_thread_id == f"{CASE_ID.value}:refine-2"
```

- [ ] **Step 2: Run the tests and verify red**

Run: `uv run pytest tests/unit/ports/test_decisions.py tests/unit/domain/test_audit.py -v`

Expected: FAIL because decision repository methods and audit query/reference fields do not exist.

- [ ] **Step 3: Define narrow decision ports**

```python
@dataclass(frozen=True, slots=True)
class DecisionCreateResult:
    record: DecisionRecord
    created: bool


class DecisionReader(Protocol):
    async def get_decision(self, decision_id: DecisionId) -> DecisionRecord | None:
        """Strongly read one immutable decision by ID."""
        raise NotImplementedError


class DecisionRepository(DecisionReader, Protocol):
    async def create_decision(
        self, record: DecisionRecord, *, retention_expires_at: UtcTimestamp
    ) -> DecisionCreateResult:
        """Conditionally create one decision, guard, and idempotency binding."""
        raise NotImplementedError
```

Make `ApplicationRepository` extend `DecisionRepository`. Replace the minimal
T05 approval record in `repositories.py` with imports from the new domain. Add
`workflow_thread_id: str | None = None` to `CaseRecord` and set it only when
`ScanService` persists T28's interrupted draft: `_run_case` passes the original
case thread, while `_run_refinement` passes the exact
`{case_id}:refine-{n}` thread it invoked. Require a non-empty persisted thread
before T29 can accept a pending decision; do not derive it later from mutable
state. Add `decision_id: str | None` to `AuditEvent`; human text remains only
in the decision record. Implement in-memory storage under one `asyncio.Lock`
with maps for decision ID, idempotency key, and a `(case_id, po_id,
po_write_date)` decision guard. Implement `list_audit(case_id, limit)` with
`1 <= limit <= 100` and deterministic oldest-first ordering.

- [ ] **Step 4: Run focused repository and audit tests**

Run: `uv run pytest tests/unit/ports/test_decisions.py tests/unit/domain/test_audit.py tests/unit/api/test_scans.py -v`

Expected: PASS with existing scan/audit behavior unchanged.

- [ ] **Step 5: Commit the persistence interfaces**

```bash
git add src/procurement/ports/decisions.py src/procurement/ports/repositories.py src/procurement/api/services/scans.py src/procurement/domain/audit.py tests/unit/ports/test_decisions.py tests/unit/domain/test_audit.py tests/unit/api/test_scans.py
git commit -m "feat(persistence): add immutable decision contracts"
```

### Task 3: Persist decision guards and records atomically in DynamoDB

**Files:**
- Modify: `src/procurement/adapters/aws/dynamodb.py:402-470`
- Modify: `tests/unit/adapters/aws/test_dynamodb.py`
- Modify: `tests/integration/test_dynamodb_local.py`

**Interfaces:**
- Consumes: Task 2 `DecisionRepository` and `DecisionCreateResult`.
- Produces: production `create_decision`, strongly consistent `get_decision`, and bounded `list_audit` using the existing application table.

- [ ] **Step 1: Write failing request-shape and round-trip tests**

Assert that one `TransactWriteItems` contains exactly:

```python
{
    "Put": {"SK": f"DECISION#{record.decision_id.value}", "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"},
    "PutGuard": {"SK": f"DECISION_GUARD#{case_id}#{po_id}#{po_write_date_digest}"},
    "PutIdempotency": {"SK": f"DECISION_IDEMPOTENCY#{idempotency_digest}"},
}
```

The tests must also assert:

- a compatible transaction cancellation rereads and returns `created=False`;
- a changed payload/key or approve-versus-reject race raises `DecisionConflictError`;
- `get_item` uses `ConsistentRead=True`;
- approval expiry and retention TTL are separate attributes;
- decimals serialize as canonical strings, never JSON floats;
- original and refinement-specific `workflow_thread_id` values round-trip on case records;
- `list_audit` queries only `AUDIT#{case_id}#` and reverses no data client-side.

- [ ] **Step 2: Run unit tests and verify red**

Run: `uv run pytest tests/unit/adapters/aws/test_dynamodb.py -v -k 'decision or audit'`

Expected: FAIL because the adapter has only the old minimal approval read and append-only audit write.

- [ ] **Step 3: Implement DynamoDB serialization and conditional replay resolution**

Use `TransactWriteItems`, canonical SHA-256 digests for idempotency/guard keys,
and a full record serializer/deserializer selected by `decision_type`. On
`TransactionCanceledException` or conditional failure, strongly read the
decision ID and idempotency binding: return only an exactly equal record;
otherwise raise `DecisionConflictError`. Never rely on DynamoDB TTL deletion
for approval validity.

Implement:

```python
async def get_decision(self, decision_id: DecisionId) -> DecisionRecord | None:
    response = self._client.get_item(
        TableName=self._table_name,
        Key={"PK": {"S": self._partition_key}, "SK": {"S": f"DECISION#{decision_id.value}"}},
        ConsistentRead=True,
    )
    return self._decision_from_item(response["Item"]) if "Item" in response else None
```

- [ ] **Step 4: Run unit and DynamoDB Local tests**

Run: `uv run pytest tests/unit/adapters/aws/test_dynamodb.py -v`

Run when Docker is available: `uv run pytest tests/integration/test_dynamodb_local.py -v -k 'decision or audit'`

Expected: PASS; if Docker is unavailable, record the integration test as unverified rather than passing.

- [ ] **Step 5: Commit DynamoDB decision persistence**

```bash
git add src/procurement/adapters/aws/dynamodb.py tests/unit/adapters/aws/test_dynamodb.py tests/integration/test_dynamodb_local.py
git commit -m "feat(dynamodb): persist guarded manager decisions"
```

### Task 4: Add one-shot Odoo confirm/cancel actions and reconciliation

**Files:**
- Modify: `src/procurement/ports/erp.py:130-180`
- Modify: `src/procurement/adapters/odoo/draft.py`
- Modify: `src/procurement/adapters/odoo/client.py:363-436,900-1005`
- Test: `tests/unit/adapters/odoo/test_draft.py`
- Test: `tests/unit/adapters/odoo/test_client.py`
- Test: `tests/contract/test_stockai_odoo_addon.py`

**Interfaces:**
- Consumes: existing `PurchaseOrderDraft` exact snapshot and add-on methods.
- Produces: `PurchaseOrderAction`, `PurchaseOrderActionResult`, `PurchaseOrderWriteAmbiguousError`, `ErpPort.read_purchase_order`, and `ErpPort.apply_purchase_order_action_once`.

- [ ] **Step 1: Write failing Odoo client/adapter tests**

```python
@pytest.mark.parametrize(
    ("action", "method", "terminal_state"),
    [(PurchaseOrderAction.CONFIRM, "action_stockai_confirm", "purchase"),
     (PurchaseOrderAction.CANCEL, "action_stockai_cancel_draft", "cancel")],
)
async def test_action_calls_only_allowlisted_atomic_method_once(action, method, terminal_state):
    result = await adapter.apply_purchase_order_action_once(
        po_id=41, expected=DRAFT, action=action
    )
    assert client.calls == [("purchase.order", method, {"ids": [41], "expected": expected_dict(DRAFT)})]
    assert result.state == terminal_state
```

Also cover stale Odoo `422` as permanent `ApprovalStaleError`, timeout/status/
oversize/malformed response as `PurchaseOrderWriteAmbiguousError`, and
`read_purchase_order` mapping of `draft`, `sent`, `purchase`, and `cancel`.

- [ ] **Step 2: Run adapter tests and verify red**

Run: `uv run pytest tests/unit/adapters/odoo/test_draft.py tests/unit/adapters/odoo/test_client.py -v -k 'action or purchase_order'`

Expected: FAIL because the ERP port has draft creation only.

- [ ] **Step 3: Implement a fixed allowlist and single-attempt write**

```python
class PurchaseOrderAction(StrEnum):
    CONFIRM = "confirm"
    CANCEL = "cancel"


_ACTION_METHOD = {
    PurchaseOrderAction.CONFIRM: "action_stockai_confirm",
    PurchaseOrderAction.CANCEL: "action_stockai_cancel_draft",
}
```

Construct `expected` from exactly `write_date`, `state`, `partner_id`,
`currency_id`, and `amount_total`. The low-level client uses one `httpx`
stream request with normal retries bypassed. Map the returned StockAI snapshot
strictly. Include the Odoo order `name` as a display-only `po_reference` when
available, but never add it to the add-on's `_EXPECTED_FIELDS` compare set.

- [ ] **Step 4: Run focused unit and existing atomic add-on contracts**

Run: `uv run pytest tests/unit/adapters/odoo/test_draft.py tests/unit/adapters/odoo/test_client.py -v`

Run when Docker is available: `uv run pytest tests/contract/test_stockai_odoo_addon.py -v -k 'atomic_confirm or atomic_cancel'`

Expected: PASS and the existing Odoo row-lock/rollback tests remain unchanged.

- [ ] **Step 5: Commit the ERP action boundary**

```bash
git add src/procurement/ports/erp.py src/procurement/adapters/odoo/draft.py src/procurement/adapters/odoo/client.py tests/unit/adapters/odoo/test_draft.py tests/unit/adapters/odoo/test_client.py tests/contract/test_stockai_odoo_addon.py
git commit -m "feat(odoo): add revision-bound PO actions"
```

### Task 5: Implement independently defended confirm and cancel MCP tools

**Files:**
- Create: `src/procurement/mcp_server/tools/confirm.py`
- Create: `src/procurement/mcp_server/tools/cancel_draft.py`
- Modify: `src/procurement/mcp_server/schemas.py:227-305`
- Modify: `src/procurement/mcp_server/server.py:21-247`
- Modify: `src/procurement/mcp_server/observability.py`
- Modify: `src/procurement/bootstrap/mcp.py`
- Modify: `compose.yaml`
- Modify: `compose.test.yaml`
- Create: `tests/unit/mcp_server/test_confirm.py`
- Create: `tests/unit/mcp_server/test_cancel_draft.py`
- Create: `tests/unit/bootstrap/test_mcp.py`
- Test: `tests/integration/test_mcp_transport.py`

**Interfaces:**
- Consumes: Task 2 `DecisionReader`, Task 4 `ErpPort` action/read operations, configured environment, and injected clock.
- Produces: MCP tools `confirm_purchase_order(environment, decision_id, idempotency_key)` and `cancel_draft_purchase_order(environment, decision_id, idempotency_key)` returning `PurchaseOrderActionOutput`.

- [ ] **Step 1: Write failing authorization, binding, and reconciliation tests**

Create a shared test matrix covering:

```python
CASES = (
    "wrong_environment", "missing_decision", "wrong_decision_type",
    "expired_approval", "altered_vendor", "altered_quantity",
    "altered_amount", "altered_budget", "altered_evidence",
    "stale_po_revision", "idempotency_conflict",
    "already_terminal", "ambiguous_then_terminal", "ambiguous_unresolved",
)
```

Assert no ERP write occurs for every invalid case. Assert confirmation reads
the approval with the strongly consistent decision port, validates the full
record, reads the current Odoo snapshot immediately before action, and calls
only confirm. Assert cancellation accepts only a rejection and calls only
cancel. Assert ambiguous outcomes reread first and never blindly resend.

- [ ] **Step 2: Run MCP tests and verify red**

Run: `uv run pytest tests/unit/mcp_server/test_confirm.py tests/unit/mcp_server/test_cancel_draft.py -v`

Expected: FAIL because both tool modules are absent.

- [ ] **Step 3: Implement strict schemas and shared reconciliation helper**

Provider input stays intentionally small:

```python
class ApplyDecisionInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    environment: Literal["dev", "prod"]
    decision_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)


class PurchaseOrderActionOutput(BaseModel):
    po_id: int = Field(strict=True, gt=0)
    po_reference: str = Field(min_length=1, max_length=128)
    state: Literal["purchase", "cancel"]
    write_date: str = Field(min_length=1, max_length=32)
    reconciled: bool
```

The approval tool must compare every approval field against its bound
recommendation/evidence snapshot stored in the record, verify
`now < expires_at`, and then match the current Odoo snapshot. The rejection
tool validates its immutable record and exact current draft snapshot. Factor
only mechanical action/reconciliation behavior into a private helper; keep
approval and rejection policy explicit in their own modules.

Register both tools with `readOnlyHint=False`, `idempotentHint=True`,
`openWorldHint=False`; cancellation has `destructiveHint=True`, confirmation
has `destructiveHint=False`. Add both names to strict argument hardening and
bounded metrics.

Extend `LocalMcpSettings` with AWS region, application-table name, and optional
DynamoDB endpoint. Deployed Odoo mode constructs `DynamoApplicationRepository`
as the `DecisionReader`. Compose MCP receives the same DynamoDB Local settings
and dummy local AWS credentials as API. Do not add a new table or AWS service.

- [ ] **Step 4: Run unit, bootstrap, transport, and configuration tests**

Run: `uv run pytest tests/unit/mcp_server/test_confirm.py tests/unit/mcp_server/test_cancel_draft.py tests/unit/bootstrap/test_mcp.py tests/integration/test_mcp_transport.py -v`

Expected: PASS, including strict schemas and real Streamable HTTP invocation.

- [ ] **Step 5: Commit the MCP decision tools**

```bash
git add src/procurement/mcp_server/tools/confirm.py src/procurement/mcp_server/tools/cancel_draft.py src/procurement/mcp_server/schemas.py src/procurement/mcp_server/server.py src/procurement/mcp_server/observability.py src/procurement/bootstrap/mcp.py compose.yaml compose.test.yaml tests/unit/mcp_server/test_confirm.py tests/unit/mcp_server/test_cancel_draft.py tests/unit/bootstrap/test_mcp.py tests/integration/test_mcp_transport.py
git commit -m "feat(mcp): defend confirmation and cancellation"
```

### Task 6: Add consumer MCP actions and resume the paused LangGraph thread

**Files:**
- Modify: `src/procurement/ports/mcp.py`
- Modify: `src/procurement/bootstrap/api.py`
- Modify: `src/procurement/agent/state.py`
- Modify: `src/procurement/agent/nodes/walking_skeleton.py:412-535`
- Modify: `src/procurement/agent/graph.py:38-123`
- Create: `tests/unit/ports/test_mcp.py`
- Test: `tests/unit/bootstrap/test_api.py`
- Test: `tests/unit/agent/test_walking_skeleton.py`

**Interfaces:**
- Consumes: Task 5 MCP tool contracts and Task 2 `DecisionReader`.
- Produces: `ProcurementMcpPort.confirm_purchase_order`, `cancel_draft_purchase_order`, `DecisionOutcome`, graph `load_decision`/`confirm`/`cancel` nodes, and `WalkingSkeletonWorkflow.aresume_decision(workflow_thread_id, decision_id)`.

- [ ] **Step 1: Write failing consumer parser and graph resume tests**

```python
async def test_approve_resume_routes_to_confirm_once() -> None:
    paused = await graph.ainvoke(INITIAL_STATE, config=CONFIG)
    assert paused["__interrupt__"]
    resumed = await graph.ainvoke(
        Command(resume=APPROVAL.decision_id.value), config=CONFIG
    )
    assert mcp.confirm_calls == [(Environment.DEV, APPROVAL.decision_id.value, APPROVAL.idempotency_key)]
    assert mcp.cancel_calls == []
    assert resumed["decision_outcome"].outcome == "confirmed"


async def test_reject_resume_routes_to_cancel_once() -> None:
    decision_reader.records[REJECTION.decision_id.value] = REJECTION
    paused = await graph.ainvoke(INITIAL_STATE, config=CONFIG)
    assert paused["__interrupt__"]
    resumed = await graph.ainvoke(
        Command(resume=REJECTION.decision_id.value), config=CONFIG
    )
    assert mcp.confirm_calls == []
    assert mcp.cancel_calls == [
        (Environment.DEV, REJECTION.decision_id.value, REJECTION.idempotency_key)
    ]
    assert resumed["decision_outcome"].outcome == "cancelled"
```

The concrete test must also cover expired/missing decision, graph restart with
the same checkpointer/thread ID, duplicate resume returning the terminal state,
MCP stale error, and reconciliation-required output. Add two service-facing
regressions: an unrefined draft resumes `case_id`, while a draft created by the
second bounded refinement resumes `{case_id}:refine-2`; after reconstructing
the service/repository, the persisted thread still selects the same checkpoint.
Each case uses the same explicit arrange/act/assert structure as the approve
and reject tests above.

- [ ] **Step 2: Run consumer and graph tests and verify red**

Run: `uv run pytest tests/unit/ports/test_mcp.py tests/unit/agent/test_walking_skeleton.py tests/unit/bootstrap/test_api.py -v -k 'decision or confirm or cancel or resume'`

Expected: FAIL because the consumer and graph have no decision actions or resume method.

- [ ] **Step 3: Implement strict consumer calls and graph routing**

Add:

```python
@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    decision_id: str
    decision_type: DecisionType
    outcome: Literal["confirmed", "cancelled", "reconciliation_required"]
    po_id: int
    po_reference: str
    write_date: str
    odoo_state: str
    reconciled: bool
```

Change `create_draft` so `decision_id = interrupt(payload)` is returned into
state after resume. Add `load_decision`, `confirm`, and `cancel` nodes and route
using the loaded typed record, never browser payload fields. Build the graph as:

```text
reason -> create_draft -> load_decision -> confirm -> END
                                      \-> cancel  -> END
```

`WalkingSkeletonWorkflow.aresume_decision(workflow_thread_id, decision_id)` invokes:

```python
return await self._graph.ainvoke(
    Command(resume=decision_id),
    config={"configurable": {"thread_id": workflow_thread_id}},
)
```

Use only the exact `CaseRecord.workflow_thread_id` persisted beside T28's draft.
The original T28 case ID is correct for an initial recommendation, but bounded
refinement deliberately invokes a fresh `{case_id}:refine-{n}` thread and T28
can create/pause the draft there. Never reconstruct or guess the thread during
decision handling.

- [ ] **Step 4: Run graph, typing, and existing T28 regressions**

Run: `uv run pytest tests/unit/ports/test_mcp.py tests/unit/agent/test_walking_skeleton.py tests/unit/api/test_scans.py tests/unit/bootstrap/test_api.py -v && uv run mypy src/procurement`

Expected: PASS with one draft before and after resume on both original and
refinement-specific checkpoints.

- [ ] **Step 5: Commit graph resumption**

```bash
git add src/procurement/ports/mcp.py src/procurement/bootstrap/api.py src/procurement/agent/state.py src/procurement/agent/nodes/walking_skeleton.py src/procurement/agent/graph.py tests/unit/ports/test_mcp.py tests/unit/bootstrap/test_api.py tests/unit/agent/test_walking_skeleton.py tests/unit/api/test_scans.py
git commit -m "feat(agent): resume manager decisions through MCP"
```

### Task 7: Implement the decision service and manager HTTP endpoints

**Files:**
- Create: `src/procurement/api/services/decisions.py`
- Create: `src/procurement/api/routes/decisions.py`
- Modify: `src/procurement/api/app.py`
- Modify: `src/procurement/bootstrap/api.py`
- Modify: `src/procurement/api/services/scans.py`
- Modify: `src/procurement/domain/errors.py`
- Create: `tests/unit/api/test_decisions.py`
- Test: `tests/unit/api/test_errors.py`

**Interfaces:**
- Consumes: Tasks 1-3 decision repository, Task 6 `aresume_decision`, authenticated `ManagerPrincipalDep`, CSRF, and current `CaseRecord`.
- Produces: `DecisionService.approve`, `DecisionService.reject`, `POST /api/v1/cases/{case_id}/approve`, and `POST /api/v1/cases/{case_id}/reject`.

- [ ] **Step 1: Write failing API safety tests**

Use the authenticated API support and assert the complete matrix:

```python
async def test_officer_cannot_approve(client, officer_session, pending_case):
    response = await client.post(
        f"/api/v1/cases/{pending_case.case_id}/approve",
        headers=decision_headers("approve-1"),
        json=approve_payload(pending_case),
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    "field",
    ["environment", "case_revision", "po_id", "po_revision", "vendor_id",
     "quantity", "amount", "currency", "budget_status", "overage",
     "evidence_digest"],
)
async def test_altered_approval_binding_is_rejected_without_resume(
    field: str,
    client: AsyncClient,
    manager_headers: dict[str, str],
    pending_case: CaseRecord,
    workflow: RecordingDecisionWorkflow,
) -> None:
    payload = approve_payload(pending_case)
    payload[field] = altered_value_for(field)
    response = await client.post(
        f"/api/v1/cases/{pending_case.case_id.value}/approve",
        headers={**manager_headers, "Idempotency-Key": f"alter-{field}"},
        json=payload,
    )
    assert response.status_code == (409 if field == "case_revision" else 422)
    assert response.json()["error_code"] == (
        "REVISION_CONFLICT" if field == "case_revision" else "VALIDATION_FAILED"
    )
    assert workflow.resume_calls == []
```

`altered_value_for` returns one validly typed but mismatching value per field,
so the test reaches binding validation instead of failing JSON parsing.

Add explicit tests for missing/invalid CSRF, missing/oversized/unsafe
`Idempotency-Key`, over-budget missing flag, missing/blank/281-character/
control-character justification, invented in-budget exception, stale PO,
expired decision replay, compatible replay, conflicting replay, simultaneous
approve/reject, and process failure after decision persistence but before
resume.

- [ ] **Step 2: Run API tests and verify red**

Run: `uv run pytest tests/unit/api/test_decisions.py tests/unit/api/test_errors.py -v`

Expected: FAIL with 404 routes and missing `DecisionService`.

- [ ] **Step 3: Implement server-derived binding validation and durable resume**

Define strict request bodies with `extra="forbid"`; decimals cross JSON as
canonical strings. Header validation is a dependency:

```python
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=IDENTIFIER_PATTERN),
]
```

`DecisionService` must:

1. parse an environment-bound `CaseId` and read the current case;
2. require `pending_approval`, T27 recommendation evidence, a T28 draft, and
   its persisted non-empty `workflow_thread_id`;
3. derive the authoritative selected offer and budget from stored evidence;
4. compare every submitted field using typed decimals and exact strings;
5. build the immutable decision with the injected clock;
6. conditionally persist it before changing case state;
7. transition the case to `approved` or `rejected` with optimistic revision;
8. append a decision audit event containing only `decision_id` as its detail reference;
9. schedule `_resume_decision` with the persisted `workflow_thread_id` and
   return `202`;
10. on compatible replay, repair any missing case transition/resume rather than writing a second record.

The background resume maps graph outcomes to `confirming` then `confirmed`,
`cancelled`, or `reconciliation_required`, persists `DecisionOutcomeRecord`
alongside (not over) the original recommendation, and appends each immutable
transition. Add these values to `ScanStatus` and the API status parser. Map
conditional losers to `REVISION_CONFLICT`; map budget failures to
`BUDGET_JUSTIFICATION_REQUIRED`; do not make either retryable. Do not migrate
to or wire the dormant `CaseState` model.

- [ ] **Step 4: Run API, auth, scan, and typing tests**

Run: `uv run pytest tests/unit/api/test_decisions.py tests/unit/api/test_errors.py tests/unit/api/test_scans.py tests/unit/api/auth -v && uv run mypy src/procurement`

Expected: PASS.

- [ ] **Step 5: Commit the manager API**

```bash
git add src/procurement/api/services/decisions.py src/procurement/api/routes/decisions.py src/procurement/api/app.py src/procurement/bootstrap/api.py src/procurement/api/services/scans.py src/procurement/domain/errors.py tests/unit/api/test_decisions.py tests/unit/api/test_errors.py tests/unit/api/test_scans.py
git commit -m "feat(api): accept guarded manager decisions"
```

### Task 8: Expose terminal decision and immutable audit views

**Files:**
- Modify: `src/procurement/api/routes/cases.py`
- Modify: `src/procurement/api/routes/scans.py`
- Modify: `src/procurement/ports/repositories.py`
- Modify: `src/procurement/adapters/aws/dynamodb.py`
- Test: `tests/unit/api/test_cases.py`
- Test: `tests/unit/api/test_scans.py`
- Test: `tests/unit/adapters/aws/test_dynamodb.py`

**Interfaces:**
- Consumes: Task 7 persisted `DecisionOutcomeRecord` and Task 2 audit list.
- Produces: `CaseDecisionResponse`, `AuditEventResponse`, `GET /api/v1/cases/{case_id}/audit`, and live case/scan status summaries.

- [ ] **Step 1: Write failing projection tests**

```python
async def test_case_keeps_recommendation_and_adds_confirmed_decision(client, confirmed_case):
    body = (await client.get(f"/api/v1/scans/{SCAN}/cases/{CASE}")).json()
    assert body["result"]["outcome"] == "approval_ready"
    assert body["decision"] == {
        "decision_id": DECISION_ID,
        "decision_type": "approve",
        "status": "confirmed",
        "po_id": 41,
        "po_reference": "P00041",
        "reconciled": False,
    }


async def test_audit_is_oldest_first_and_officer_readable(client, officer_session):
    response = await client.get(f"/api/v1/cases/{CASE}/audit")
    assert [row["event_type"] for row in response.json()["events"]] == [
        "pending_approval", "manager_approved", "confirming", "confirmed"
    ]
```

Also assert cross-environment IDs are rejected, decision text appears only to
authorized case readers, raw DynamoDB/provider data never appears, and recent
case/scan aggregates update from pending to confirmed/cancelled.

- [ ] **Step 2: Run projection tests and verify red**

Run: `uv run pytest tests/unit/api/test_cases.py tests/unit/api/test_scans.py -v -k 'decision or audit or confirmed or cancelled'`

Expected: FAIL because case responses have no decision or audit route.

- [ ] **Step 3: Implement bounded response models without overwriting recommendation evidence**

Add `decision: CaseDecisionResponse | None` to `CaseResponse` and remove the
never-produced `ConfirmedResponse` type-only shape from the recommendation union.
Return at most 100 oldest-first audit events. For an event with `decision_id`,
join the immutable decision through `DecisionReader`; expose reason or
justification only in this authorized response. Use status, not recommendation
outcome, for aggregate labels after T28.

- [ ] **Step 4: Run API, adapter, and response compatibility tests**

Run: `uv run pytest tests/unit/api/test_cases.py tests/unit/api/test_scans.py tests/unit/adapters/aws/test_dynamodb.py -v`

Expected: PASS, including historical T25/T27 records with `decision=null`.

- [ ] **Step 5: Commit decision/audit projections**

```bash
git add src/procurement/api/routes/cases.py src/procurement/api/routes/scans.py src/procurement/ports/repositories.py src/procurement/adapters/aws/dynamodb.py tests/unit/api/test_cases.py tests/unit/api/test_scans.py tests/unit/adapters/aws/test_dynamodb.py
git commit -m "feat(api): expose decision audit timeline"
```

### Task 9: Add bounded manager controls and audit timeline in React

**Files:**
- Create: `frontend/src/components/ManagerDecisionPanel.tsx`
- Create: `frontend/src/components/AuditTimeline.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/RecommendationPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/presentation.ts`
- Modify: `frontend/src/styles.css`
- Create: `frontend/tests/manager-decision.test.tsx`
- Create: `frontend/tests/audit-timeline.test.tsx`
- Test: `frontend/tests/api-client.test.ts`
- Test: `frontend/tests/recommendation.test.tsx`

**Interfaces:**
- Consumes: Task 7 approve/reject endpoints, Task 8 decision/audit responses, and current `Session.role`.
- Produces: typed `approveCase`, `rejectCase`, `getCaseAudit`, manager-only controls, over-budget exception form, rejection form, and chronological audit.

- [ ] **Step 1: Write failing client and component tests**

Cover:

```tsx
it("requires an explicit over-budget exception and justification", async () => {
  render(<ManagerDecisionPanel session={MANAGER} caseDetail={OVER_BUDGET_CASE} />);
  expect(screen.getByRole("button", { name: "Approve and confirm" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: /approve budget exception/i }));
  await user.type(screen.getByLabelText(/justification/i), "Avoid a projected stockout.");
  expect(screen.getByRole("button", { name: "Approve and confirm" })).toBeEnabled();
});

it("does not render decision controls for an officer", () => {
  render(<ManagerDecisionPanel session={OFFICER} caseDetail={PENDING_CASE} />);
  expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /reject/i })).not.toBeInTheDocument();
});
```

Client tests assert CSRF and a generated/stable per-submit `Idempotency-Key`,
the exact binding body, strict parsing of decision/audit responses, 280-character
`maxLength`, and safe handling of `APPROVAL_STALE`, `REVISION_CONFLICT`,
`BUDGET_JUSTIFICATION_REQUIRED`, and `RECONCILIATION_REQUIRED`.

- [ ] **Step 2: Run frontend tests and verify red**

Run: `cd frontend && npm test -- --run manager-decision.test.tsx audit-timeline.test.tsx api-client.test.ts recommendation.test.tsx`

Expected: FAIL because the new components/client methods do not exist.

- [ ] **Step 3: Implement the decision panel and timeline**

Pass `session` from `App` to `RecommendationPage`. Display exact PO ID/revision,
selected vendor, quantity, amount/currency, evidence digest, remaining budget,
and overage. Keep approve and reject as separate explicit forms; confirmation
language must say fictional Odoo PO and must not claim supplier contact.

After `202`, disable both forms, announce the accepted decision with
`aria-live`, and restart polling for `approved`, `rejected`, `confirming`,
`confirmed`, `cancelled`, or `reconciliation_required`. Render audit events by
server order and use ordinary React text nodes for manager text. Do not use
`dangerouslySetInnerHTML`.

- [ ] **Step 4: Run the complete frontend gate**

Run: `cd frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build`

Expected: PASS.

- [ ] **Step 5: Commit the manager experience**

```bash
git add frontend/src/components/ManagerDecisionPanel.tsx frontend/src/components/AuditTimeline.tsx frontend/src/api/client.ts frontend/src/pages/RecommendationPage.tsx frontend/src/App.tsx frontend/src/presentation.ts frontend/src/styles.css frontend/tests/manager-decision.test.tsx frontend/tests/audit-timeline.test.tsx frontend/tests/api-client.test.ts frontend/tests/recommendation.test.tsx
git commit -m "feat(frontend): add manager decision controls"
```

### Task 10: Add bounded lifecycle metrics, dashboards, alerts, and redaction

**Files:**
- Modify: `src/procurement/observability/metrics.py`
- Modify: `src/procurement/mcp_server/observability.py`
- Modify: `src/procurement/observability/logging.py`
- Modify: `deploy/kubernetes/base/observability/dashboards/agent-health.json`
- Modify: `deploy/kubernetes/base/observability/dashboards/llm-mcp.json`
- Modify: `deploy/kubernetes/base/observability/rules/stockai-alerts.yaml`
- Modify: `tests/unit/observability/test_redaction.py`
- Modify: `tests/kubernetes/test_observability_content.py`
- Modify: `tests/kubernetes/test_application_overlays.py`
- Modify: `tests/config/test_container_contracts.py`

**Interfaces:**
- Consumes: Tasks 5-9 lifecycle result codes.
- Produces: low-cardinality decision/action/reconciliation counters and latency histograms, two dashboard sections, and actionable reconciliation/failure alerts.

- [ ] **Step 1: Write failing metric, redaction, dashboard, and alert tests**

Assert exact bounded collectors:

```text
procurement_manager_decisions_total{decision="approve|reject",result="accepted|replay|conflict|stale|error"}
procurement_decision_completion_seconds{decision="approve|reject"}
procurement_purchase_order_actions_total{action="confirm|cancel",result="success|stale|reconciliation_required|error"}
procurement_purchase_order_reconciliation_seconds{action="confirm|cancel"}
```

Assert forbidden **metric label names** include `case_id`, `decision_id`,
`manager_id`, `manager_email`, `vendor_id`, `amount`, `overage`,
`evidence_digest`, `reason`, and `justification`. Structured-log tests may keep
bounded `case_id` and `decision_id` for traceability, matching the design, but
must redact manager identity/email, vendor/commercial values, evidence digests,
reasons, and justifications. Dashboard tests must find panels for pending
decisions, completion latency, action failures, and reconciliation. Alert tests
must require sustained action failure and immediate unresolved reconciliation,
with runbook annotations and no alert on ordinary rejection/stale input.

- [ ] **Step 2: Run observability tests and verify red**

Run: `uv run pytest tests/unit/observability/test_redaction.py tests/kubernetes/test_observability_content.py tests/kubernetes/test_application_overlays.py tests/config/test_container_contracts.py -v`

Expected: FAIL because T29 metrics/panels/alerts are absent and MCP Compose lacks decision persistence configuration.

- [ ] **Step 3: Implement bounded signals and runtime contracts**

Add enum-allowlisted label sanitizers; unknown values become `unknown`. Record
latencies with histograms, not IDs. Extend recursive redaction with all manager
text and commercial aliases. Add dashboard PromQL using rates and histogram
quantiles without high-cardinality grouping. Add alerts:

- `StockAIPurchaseOrderActionFailures`: nonzero failure rate for 10 minutes;
- `StockAIDecisionReconciliationRequired`: any increase over 5 minutes,
  linked to the existing alert runbook.

Update Kustomize/Compose tests only where actual T29 runtime wiring requires it;
do not add a new deployment, table, secret, AWS service, or direct `kubectl`
path.

- [ ] **Step 4: Run observability and Kubernetes validation**

Run: `uv run pytest tests/unit/observability/test_redaction.py tests/kubernetes/test_observability_content.py tests/kubernetes/test_application_overlays.py tests/config/test_container_contracts.py -v`

Run: `make kubernetes-validate`

Expected: PASS, including strict Kubeconform for dev and prod overlays.

- [ ] **Step 5: Commit observability**

```bash
git add src/procurement/observability/metrics.py src/procurement/mcp_server/observability.py src/procurement/observability/logging.py deploy/kubernetes/base/observability/dashboards/agent-health.json deploy/kubernetes/base/observability/dashboards/llm-mcp.json deploy/kubernetes/base/observability/rules/stockai-alerts.yaml tests/unit/observability/test_redaction.py tests/kubernetes/test_observability_content.py tests/kubernetes/test_application_overlays.py tests/config/test_container_contracts.py
git commit -m "feat(observability): monitor manager decisions"
```

### Task 11: Prove the full API → LangGraph → MCP → Odoo lifecycle

**Files:**
- Modify: `tests/integration/test_api_agent_mcp.py`
- Modify: `tests/integration/test_mcp_real_odoo.py`
- Modify: `tests/integration/test_dynamodb_local.py`
- Modify: `tests/contract/test_stockai_odoo_addon.py`
- Modify: `tests/support/fake_odoo/app.py`
- Modify: `scripts/smoke/authenticated_prod.py`
- Modify: `tests/unit/smoke/test_authenticated_prod.py`
- Modify: `docs/implementation-status.md`

**Interfaces:**
- Consumes: the complete T29 application slice.
- Produces: executable happy, exception, rejection, stale, replay, concurrency, response-loss, restart, reconciliation, and no-out-of-scope-action evidence.

- [ ] **Step 1: Extend integration fixtures and write failing end-to-end cases**

Create real Streamable HTTP cases that:

1. scan to `pending_approval`, approve, resume the same thread, call confirm,
   and finish `confirmed` with one immutable approval;
2. approve an over-budget case only with the explicit exception and
   justification;
3. reject, call cancel, and finish `cancelled` with rejection evidence;
4. alter each bound field and prove no MCP/Odoo write;
5. expire the approval at exactly 30 minutes and prove no write;
6. send compatible and conflicting replays;
7. race approve against reject and prove one terminal write;
8. lose the confirm/cancel response, restart the API/graph, reconcile, and
   prove no second Odoo transition/receipt;
9. produce unresolved ambiguity and remain `reconciliation_required`;
10. inspect tool calls and prove no update, supplier contact, payment, email,
    or autonomous decision occurs.

- [ ] **Step 2: Run the focused end-to-end tests and verify red**

Run: `uv run pytest tests/integration/test_api_agent_mcp.py -v -k 'approve or reject or decision'`

Expected: FAIL until all fixtures support the T29 tools and resume path.

- [ ] **Step 3: Complete fake Odoo, smoke assertions, and status evidence**

The fake Odoo endpoint must emulate the exact StockAI snapshot/action contract,
including configurable commit-then-drop-response behavior. Public smoke must:

- authenticate as manager;
- start a fresh eligible scan and wait for `pending_approval`;
- approve the exact current binding;
- wait for `confirmed`;
- fetch the audit timeline;
- assert one `manager_approved` and one `confirmed` event;
- query Prometheus for confirm MCP traffic;
- query sanitized Loki evidence without matching manager text or amounts.

Add a separate rejection smoke helper only if it can use a fresh seeded case
without mutating the same demo case; otherwise retain rejection as real Odoo
contract/integration evidence and document that choice accurately.

Update `docs/implementation-status.md` only with commands actually run and
limitations still present.

- [ ] **Step 4: Run all offline verification gates**

Run in order:

```bash
uv run pytest tests/integration/test_api_agent_mcp.py -v
make test-integration
make odoo-contract
make kubernetes-validate
make check
git diff --check
```

Expected: every available gate passes. If Docker or network prerequisites are
unavailable, keep those gates explicitly pending and do not mark T29 complete.

- [ ] **Step 5: Commit the complete offline slice**

```bash
git add tests/integration/test_api_agent_mcp.py tests/integration/test_mcp_real_odoo.py tests/integration/test_dynamodb_local.py tests/contract/test_stockai_odoo_addon.py tests/support/fake_odoo/app.py scripts/smoke/authenticated_prod.py tests/unit/smoke/test_authenticated_prod.py docs/implementation-status.md
git commit -m "test(t29): prove manager decision lifecycle"
```

### Task 12: Validate dev and promote the exact immutable artifact

**Files:**
- Modify through existing release tooling only: `deploy/kubernetes/overlays/dev/kustomization.yaml`
- Modify through existing release tooling only: `deploy/releases/*.json`
- Modify after exact promotion only: `deploy/kubernetes/overlays/prod/kustomization.yaml`
- Modify: `docs/implementation-status.md`

**Interfaces:**
- Consumes: one clean, fully verified T29 feature branch and the existing T22-T24 release/promotion scripts.
- Produces: one four-image immutable release validated in dev and, after protected approval, promoted byte-for-byte to production.

- [ ] **Step 1: Rebase/check the branch against current `main` without discarding work**

Run: `git status --short && git log --oneline --decorate -12 && git diff main HEAD --check`

Expected: only reviewed T29 commits and a clean worktree. Resolve conflicts with the dedicated merge-conflict workflow; never reset or checkout away user changes.

- [ ] **Step 2: Merge the feature locally into `dev` and publish dev desired state**

Use the repository's approved branch workflow and `make promote-dev`; do not
deploy with `kubectl`. A push to `dev` must build all four images, run Docker
Scout reporting, create immutable release metadata, and update only dev desired
state for Argo CD.

Expected: the dev workflow publishes one release and Argo CD reports `Synced`
and `Healthy` for its exact revision/digests.

- [ ] **Step 3: Run authenticated dev acceptance**

Run: `make smoke-dev`

Inspect the authorized audit timeline, Grafana decision panels, Prometheus
metrics, sanitized Loki fields, and both T29 alerts. Exercise happy approval,
over-budget approval, and one fresh rejection without reusing a PO revision.

Expected: smoke and inspection pass; no supplier contact, payment, legal order,
or autonomous approval occurs. Record release ID, source revision, four image
digests, smoke ID, and sanitized evidence digest in `docs/implementation-status.md`.

- [ ] **Step 4: Open the protected feature-to-`main` pull request**

The PR must run the complete tests and Docker Scout checks. Do not merge until
required checks pass and the protected production promotion is explicitly
approved. The main workflow must promote the exact dev-tested image digests
without rebuild and must not deploy with `kubectl`.

- [ ] **Step 5: Verify production and finish T29 evidence**

After the protected merge and Argo reconciliation, run: `make smoke-prod`.

Expected: production reports the exact promoted release, authenticated manager
approval confirms one fresh fictional PO, immutable audit/metrics/log evidence
is present, and Argo remains `Synced`/`Healthy`. Update
`docs/implementation-status.md`, commit only accurate evidence, and mark T29
complete only after all Task 12 checks pass.

---

## Requirements and completion mapping

| T29 requirement | Implemented by | Verified by |
|---|---|---|
| Manager role, CSRF, environment and exact binding | Tasks 1, 7 | Domain/API authorization and tamper matrix |
| Immutable 30-minute approval and rejection evidence | Tasks 1-3 | In-memory, DynamoDB unit, and DynamoDB Local tests |
| Over-budget exception | Tasks 1, 7, 9 | Domain, API, and React tests plus dev acceptance |
| Strong independent MCP revalidation | Tasks 4-6 | MCP matrix, transport tests, real Odoo tests |
| Atomic confirmation/cancellation | Tasks 4-6 | Odoo add-on contract and full integration |
| Idempotency/concurrency/restart/reconciliation | Tasks 2-7, 11 | Race, response-loss, restart, and replay tests |
| Bounded UI and immutable audit | Tasks 8-9 | API projection and React accessibility tests |
| Metrics, logs, dashboards, alerts | Task 10 | Redaction/configuration/Kubernetes tests and live inspection |
| Real end-to-end interaction | Tasks 11-12 | Streamable HTTP, Odoo contract, dev/prod smoke |
| No request-change or external side effect | Tasks 1, 5-7, 9, 11 | State/tool/API/UI absence and call inspection |

T29 is not complete merely because local unit tests pass. Completion requires
the exact current immutable approval before every confirmation, safe rejection
cancellation, no blind write retry, preserved recommendation evidence and
chronological audit, bounded manager UI, passing offline suites, dev validation,
protected exact-artifact promotion, and production smoke evidence.
