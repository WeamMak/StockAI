# Bounded Case Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an officer attach a short note to an `approval_ready` case and get a fresh, capped (3 attempts) re-evaluation of that one case, with the note passed to the LLM as a secondary, non-authoritative preference.

**Architecture:** A new `ScanService.refine_case` method validates and reserves one refinement attempt (bumping the existing `CaseRecord` to `running` under optimistic concurrency), then schedules a background task (`_run_refinement`) that reconstructs the original `ReplenishmentCandidate` from a newly persisted `CandidateSnapshot`, re-invokes the existing per-case LangGraph workflow under a fresh `thread_id` with `officer_note` threaded into `ScanState` and `RecommendationRequest`, and persists the terminal result exactly like the original run. The frontend adds a small panel to `RecommendationPage.tsx` that posts the note and lets the page's existing poll loop pick up the transition back to `running` and the eventual new result.

**Tech Stack:** Python 3.12 / FastAPI / LangGraph / Pydantic (backend), TypeScript / React / Vitest (frontend), DynamoDB single-table storage.

## Global Constraints

- Refinement cap is exactly 3 attempts per case (`MAX_REFINEMENTS = 3`).
- The LLM call stays at `temperature=0.0` (`adapters/aws/bedrock.py:195`) — unchanged by this plan.
- Refinement applies only to cases whose current result has `outcome == "approval_ready"` and `status == "succeeded"`.
- `officer_note` is bounded to 280 characters, non-blank, control-character-free — same validation shape as the existing `uncertainty` field in `ports/llm.py`.
- No accumulating chat history: each refinement is a fresh graph invocation under a new `thread_id`, seeded only with the current note.
- A refinement replaces the case's result in place (new revision, same `case_id`); no separate "declined" record — the prior result remains reconstructable via the existing `AUDIT#` trail.
- Concurrent refinement attempts are rejected via the existing `RevisionConflictError` → `REVISION_CONFLICT` mapping; no new locking primitive.

---

### Task 1: `RecommendationRequest.officer_note`

**Files:**
- Modify: `src/procurement/ports/llm.py:53-92` (`RecommendationRequest` dataclass)
- Test: `tests/unit/ports/test_llm.py` (new file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `RecommendationRequest.officer_note: str | None` (default `None`), validated in `__post_init__`. Later tasks (3, 4) read this field.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/ports/test_llm.py`:

```python
"""RecommendationRequest.officer_note bounded-text validation."""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.support.recommendations import t27_request

from procurement.ports.llm import RecommendationRequest


def test_officer_note_defaults_to_none() -> None:
    request = t27_request()

    assert request.officer_note is None


def test_officer_note_accepts_bounded_text() -> None:
    request = replace(t27_request(), officer_note="Prioritize delivery speed.")

    assert request.officer_note == "Prioritize delivery speed."


def test_officer_note_rejects_text_over_280_characters() -> None:
    with pytest.raises(ValueError, match="officer_note"):
        replace(t27_request(), officer_note="x" * 281)


def test_officer_note_rejects_blank_text() -> None:
    with pytest.raises(ValueError, match="officer_note"):
        replace(t27_request(), officer_note="   ")


def test_officer_note_rejects_control_characters() -> None:
    with pytest.raises(ValueError, match="officer_note"):
        replace(t27_request(), officer_note="Avoid vendor\x07 please.")


def test_officer_note_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="officer_note"):
        RecommendationRequest.__new__(RecommendationRequest)  # placeholder unused
```

Remove the last placeholder test (`test_officer_note_rejects_non_string`) — it does not construct a real invalid case. Replace it with:

```python
def test_officer_note_accepts_exactly_280_characters() -> None:
    request = replace(t27_request(), officer_note="x" * 280)

    assert request.officer_note == "x" * 280
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/ports/test_llm.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'officer_note'` (via `replace`).

- [ ] **Step 3: Add the field and validation**

In `src/procurement/ports/llm.py`, update the `RecommendationRequest` dataclass (around line 53-92):

```python
@dataclass(frozen=True, slots=True)
class RecommendationRequest:
    """Typed evidence supplied to the walking-skeleton model call."""

    environment: Environment
    candidates: tuple[ReplenishmentCandidate, ...]
    preferences: tuple[AppliedPreferences, ...] = ()
    evidence: tuple[ProcurementEvidence, ...] = ()
    officer_note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise ValueError("environment must be dev or prod")
        if (
            not isinstance(self.candidates, tuple)
            or not 1 <= len(self.candidates) <= 25
            or not all(
                isinstance(candidate, ReplenishmentCandidate)
                for candidate in self.candidates
            )
        ):
            raise ValueError("candidates must contain 1 to 25 candidates")
        if (
            not isinstance(self.preferences, tuple)
            or len(self.preferences) not in {0, len(self.candidates)}
            or not all(
                isinstance(preference, AppliedPreferences)
                for preference in self.preferences
            )
        ):
            raise ValueError("preferences must match the candidate set")
        if (
            not isinstance(self.evidence, tuple)
            or len(self.evidence) not in {0, len(self.candidates)}
            or not all(isinstance(item, ProcurementEvidence) for item in self.evidence)
        ):
            raise ValueError("evidence must match the candidate set")
        if self.evidence and {
            item.product_id for item in self.evidence if item.skip_reason_code is None
        } != {candidate.product_id for candidate in self.candidates}:
            raise ValueError("evidence must describe the eligible candidate set")
        if self.officer_note is not None and (
            not isinstance(self.officer_note, str)
            or not self.officer_note.strip()
            or len(self.officer_note) > 280
            or _CONTROL_CHARACTER_PATTERN.search(self.officer_note) is not None
        ):
            raise ValueError("officer_note must be bounded normal text")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/ports/test_llm.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/procurement/ports/llm.py tests/unit/ports/test_llm.py
git commit -m "feat(llm): add bounded officer_note field to RecommendationRequest"
```

---

### Task 2: `CandidateSnapshot` and `CaseRecord` persistence fields

**Files:**
- Modify: `src/procurement/ports/repositories.py:70-85` (`CaseRecord`, new `CandidateSnapshot`)
- Modify: `src/procurement/adapters/aws/dynamodb.py:22-39,746-824,826-956` (imports, `_case_attributes`, `_case_from_item`)
- Test: `tests/unit/adapters/aws/test_dynamodb.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `CandidateSnapshot(category_id: str, reorder_minimum: Decimal, reorder_maximum: Decimal, projected_quantity: Decimal, projected_trigger_date: date)`; `CaseRecord.candidate_snapshot: CandidateSnapshot | None = None`; `CaseRecord.refinement_count: int = 0`. Task 5 sets `candidate_snapshot` at case creation; Task 6 reads both fields.

- [ ] **Step 1: Write the failing test**

In `tests/unit/adapters/aws/test_dynamodb.py`, add after `test_case_round_trip_preserves_immutable_preference_snapshot` (after line 149):

```python
@pytest.mark.anyio
async def test_case_round_trip_preserves_candidate_snapshot_and_refinement_count() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client, table_name=TABLE_NAME, environment=Environment.DEV
    )
    record = CaseRecord(
        case_id=CASE_ID,
        revision=Revision(2),
        status="succeeded",
        trigger="manual",
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
        candidate_snapshot=CandidateSnapshot(
            category_id="category-safety",
            reorder_minimum=Decimal("10.000000"),
            reorder_maximum=Decimal("40.000000"),
            projected_quantity=Decimal("8.000000"),
            projected_trigger_date=date(2026, 8, 9),
        ),
        refinement_count=2,
    )
    client.queue(
        "get_item",
        {"Item": repository._case_item(record, expires_at=EXPIRES_AT)},
    )

    restored = await repository.get_case(CASE_ID)

    assert restored == record


@pytest.mark.anyio
async def test_case_without_candidate_snapshot_restores_zero_refinement_count() -> None:
    client = RecordingDynamoClient()
    repository = DynamoApplicationRepository(
        client=client, table_name=TABLE_NAME, environment=Environment.DEV
    )
    client.queue("get_item", {"Item": _case_item()})

    restored = await repository.get_case(CASE_ID)

    assert restored is not None
    assert restored.candidate_snapshot is None
    assert restored.refinement_count == 0
```

Add the required new imports at the top of the file — extend the existing `from datetime import UTC, datetime` line and `from procurement.ports.repositories import (...)` block:

```python
from datetime import UTC, date, datetime
```

```python
from procurement.ports.repositories import (
    ApprovalRecord,
    CandidateSnapshot,
    CaseRecord,
    IdempotencyConflictError,
    ImmutableRecordError,
    LoginTransactionRecord,
    RevisionConflictError,
    SessionRecord,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/adapters/aws/test_dynamodb.py -v -k candidate_snapshot`
Expected: FAIL — `ImportError: cannot import name 'CandidateSnapshot'`.

- [ ] **Step 3: Add `CandidateSnapshot` and the two `CaseRecord` fields**

In `src/procurement/ports/repositories.py`, insert before the `CaseRecord` dataclass (before line 70):

```python
@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    """Enough of the original candidate to re-invoke the LLM during a refinement."""

    category_id: str
    reorder_minimum: Decimal
    reorder_maximum: Decimal
    projected_quantity: Decimal
    projected_trigger_date: date
```

Update `CaseRecord` (was lines 70-85):

```python
@dataclass(frozen=True, slots=True)
class CaseRecord:
    """Durable application view of one procurement scan/case."""

    case_id: CaseId
    revision: Revision
    status: str
    trigger: str
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    started_at: UtcTimestamp | None = None
    completed_at: UtcTimestamp | None = None
    evidence: tuple[ProcurementEvidence, ...] = ()
    result: RecommendationRecord | None = None
    error: FailureRecord | None = None
    candidate_snapshot: CandidateSnapshot | None = None
    refinement_count: int = 0
```

- [ ] **Step 4: Serialize the new fields in DynamoDB**

In `src/procurement/adapters/aws/dynamodb.py`, update the import block (lines 22-39) to add `CandidateSnapshot` alphabetically:

```python
from procurement.ports.repositories import (
    ApplicationRepository,
    ApprovalRecord,
    CandidateSnapshot,
    CaseCreateResult,
    CasePage,
    CaseRecord,
    CaseSummary,
    FailureRecord,
    IdempotencyConflictError,
    ImmutableRecordError,
    LoginTransactionRecord,
    RecommendationRecord,
    RevisionConflictError,
    ScanCreateResult,
    ScanPage,
    ScanRecord,
    SessionRecord,
)
```

In `_case_attributes` (around line 808, right after the `if record.evidence:` block and before `if record.error is not None:`), add:

```python
        if record.candidate_snapshot is not None:
            snapshot = record.candidate_snapshot
            values["candidate_snapshot"] = {
                "M": {
                    "category_id": {"S": snapshot.category_id},
                    "reorder_minimum": {"S": format(snapshot.reorder_minimum, "f")},
                    "reorder_maximum": {"S": format(snapshot.reorder_maximum, "f")},
                    "projected_quantity": {
                        "S": format(snapshot.projected_quantity, "f")
                    },
                    "projected_trigger_date": {
                        "S": snapshot.projected_trigger_date.isoformat()
                    },
                }
            }
        values["refinement_count"] = {"N": str(record.refinement_count)}
```

- [ ] **Step 5: Deserialize the new fields**

In `_case_from_item` (around line 826-829), add a second parsed mapping alongside `result_item`/`error_item`:

```python
    def _case_from_item(self, item: Mapping[str, Any]) -> CaseRecord:
        if not item:
            raise ValueError("DynamoDB returned an empty case")
        result_item = cast(Mapping[str, Any] | None, item.get("result", {}).get("M"))
        error_item = cast(Mapping[str, Any] | None, item.get("error", {}).get("M"))
        snapshot_item = cast(
            Mapping[str, Any] | None, item.get("candidate_snapshot", {}).get("M")
        )
```

Then, in the `CaseRecord(...)` construction, add these two fields right before the closing `)` of the call (immediately after the existing `error=(...)` block, around line 946-956):

```python
            error=(
                FailureRecord(
                    error_code=self._string(error_item, "error_code"),
                    message=self._string(error_item, "message"),
                    retryable=bool(error_item["retryable"]["BOOL"]),
                    retry_count=self._number(error_item, "retry_count"),
                )
                if error_item is not None
                else None
            ),
            candidate_snapshot=(
                CandidateSnapshot(
                    category_id=self._string(snapshot_item, "category_id"),
                    reorder_minimum=Decimal(
                        self._string(snapshot_item, "reorder_minimum")
                    ),
                    reorder_maximum=Decimal(
                        self._string(snapshot_item, "reorder_maximum")
                    ),
                    projected_quantity=Decimal(
                        self._string(snapshot_item, "projected_quantity")
                    ),
                    projected_trigger_date=date.fromisoformat(
                        self._string(snapshot_item, "projected_trigger_date")
                    ),
                )
                if snapshot_item is not None
                else None
            ),
            refinement_count=(
                self._number(item, "refinement_count")
                if "refinement_count" in item
                else 0
            ),
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/adapters/aws/test_dynamodb.py -v`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 7: Run the full adapter and repository unit suites for regressions**

Run: `uv run pytest tests/unit/adapters tests/unit/ports -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/procurement/ports/repositories.py src/procurement/adapters/aws/dynamodb.py tests/unit/adapters/aws/test_dynamodb.py
git commit -m "feat(persistence): add CandidateSnapshot and refinement_count to CaseRecord"
```

---

### Task 3: `ScanState.officer_note` and system prompt

**Files:**
- Modify: `src/procurement/agent/state.py:121-131` (`ScanState`)
- Modify: `src/procurement/agent/nodes/walking_skeleton.py:238-262` (`reason_about_candidate`)
- Modify: `src/procurement/agent/prompts/procurement_system.md` (new section)
- Test: `tests/unit/agent/test_walking_skeleton.py` (extend)

**Interfaces:**
- Consumes: `RecommendationRequest.officer_note` (Task 1).
- Produces: `ScanState["officer_note"]: str` (optional key, read only by `reason_about_candidate`). Task 6 seeds this key when invoking the workflow for a refinement.

- [ ] **Step 1: Write the failing test**

In `tests/unit/agent/test_walking_skeleton.py`, add after `test_graph_returns_one_approval_ready_read_only_result` (after line 211):

```python
@pytest.mark.anyio
async def test_officer_note_reaches_the_recommendation_request() -> None:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV,
            candidates=(_candidate(),),
            next_cursor=None,
        )
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    graph = build_walking_skeleton_graph(mcp=mcp, llm=llm, metrics=create_agent_metrics())

    await graph.ainvoke(
        {
            "scan_id": "scan-001",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
            "officer_note": "Prioritize delivery speed this time.",
        }
    )

    assert len(llm.requests) == 1
    assert llm.requests[0].officer_note == "Prioritize delivery speed this time."


@pytest.mark.anyio
async def test_missing_officer_note_leaves_the_recommendation_request_unset() -> None:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV,
            candidates=(_candidate(),),
            next_cursor=None,
        )
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    graph = build_walking_skeleton_graph(mcp=mcp, llm=llm, metrics=create_agent_metrics())

    await graph.ainvoke(
        {
            "scan_id": "scan-001",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        }
    )

    assert len(llm.requests) == 1
    assert llm.requests[0].officer_note is None
```

- [ ] **Step 2: Run tests to verify the first fails**

Run: `uv run pytest tests/unit/agent/test_walking_skeleton.py -v -k officer_note`
Expected: `test_officer_note_reaches_the_recommendation_request` FAILS with `AssertionError: None != 'Prioritize delivery speed this time.'`; the second test passes already (officer_note already defaults to `None` from Task 1).

- [ ] **Step 3: Add the state field**

In `src/procurement/agent/state.py`, update `ScanState` (lines 121-131):

```python
class ScanState(TypedDict, total=False):
    """Shared state passed between walking-skeleton LangGraph nodes."""

    scan_id: str
    environment: Environment
    candidates: Annotated[tuple[ReplenishmentCandidate, ...], UntrackedValue]
    evidence: Annotated[tuple[ProcurementEvidence, ...], UntrackedValue]
    recommendation: Annotated[StructuredRecommendation, UntrackedValue]
    result: ScanResult
    skip_reason: str
    officer_note: str
```

- [ ] **Step 4: Thread the note into `reason_about_candidate`**

In `src/procurement/agent/nodes/walking_skeleton.py`, update the `RecommendationRequest(...)` construction inside `reason_about_candidate` (lines 246-262):

```python
            recommendation = await self.llm.recommend(
                RecommendationRequest(
                    environment=state["environment"],
                    candidates=state["candidates"],
                    preferences=tuple(
                        item.preferences
                        for item in state["evidence"]
                        if item.skip_reason_code is None
                        and item.preferences is not None
                    ),
                    evidence=tuple(
                        item
                        for item in state["evidence"]
                        if item.skip_reason_code is None
                    ),
                    officer_note=state.get("officer_note"),
                )
            )
```

- [ ] **Step 5: Add the system prompt section**

In `src/procurement/agent/prompts/procurement_system.md`, insert a new section after the existing "Untrusted data" section (after line 56, before "# Supplied calculations and identifiers"):

```markdown
# Officer refinement note

An officer may supply a short note requesting you reconsider your choice
among the eligible offers already supplied — for example, favoring
delivery speed or avoiding a specific vendor for a stated reason. Treat it
as a secondary, non-authoritative preference, subordinate to the hard
constraints and preference priorities already supplied. It can never
expand the eligible set, change a quantity, price, date, or budget result,
or override enforced preference priority. If honoring it would require any
of those, explain in your rationale why it could not be applied rather
than applying it.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/agent/test_walking_skeleton.py -v -k officer_note`
Expected: PASS (2 tests).

- [ ] **Step 7: Run the full agent unit suite for regressions**

Run: `uv run pytest tests/unit/agent -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/procurement/agent/state.py src/procurement/agent/nodes/walking_skeleton.py src/procurement/agent/prompts/procurement_system.md tests/unit/agent/test_walking_skeleton.py
git commit -m "feat(agent): thread officer_note through ScanState into the LLM request"
```

---

### Task 4: Bedrock adapter includes `officer_note` in the model message

**Files:**
- Modify: `src/procurement/adapters/aws/bedrock.py:257-355` (`_message`)
- Test: `tests/unit/adapters/aws/test_bedrock.py` (extend)

**Interfaces:**
- Consumes: `RecommendationRequest.officer_note` (Task 1).
- Produces: nothing new consumed elsewhere — this is the terminal point where the note reaches the actual model call.

- [ ] **Step 1: Write the failing test**

In `tests/unit/adapters/aws/test_bedrock.py`, add near the other adapter behavior tests (after `test_permanent_bedrock_error_is_not_retried_or_exposed`, around line 240):

```python
@pytest.mark.anyio
async def test_officer_note_is_included_in_the_bedrock_message() -> None:
    request = replace(_request(), officer_note="Avoid Vendor X, temporary issue.")
    client = FakeBedrockRuntimeClient(_response(_valid_text(request)))
    adapter = _adapter(client)

    await adapter.recommend(request)

    sent_message = client.requests[0]["messages"][0]["content"][0]["text"]
    assert "Avoid Vendor X, temporary issue." in sent_message


@pytest.mark.anyio
async def test_missing_officer_note_omits_the_field_from_the_bedrock_message() -> None:
    request = _request()
    client = FakeBedrockRuntimeClient(_response(_valid_text(request)))
    adapter = _adapter(client)

    await adapter.recommend(request)

    sent_message = client.requests[0]["messages"][0]["content"][0]["text"]
    assert '"officer_note"' not in sent_message
```

`tests/unit/adapters/aws/test_bedrock.py` does not currently import `dataclasses.replace` (it is only used internally by the adapter module, not by the test file). Add a new import line after the existing `import json` line (line 5):

```python
import json
from dataclasses import replace
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/adapters/aws/test_bedrock.py -v -k officer_note`
Expected: `test_officer_note_is_included_in_the_bedrock_message` FAILS — the note text is absent from the sent message.

- [ ] **Step 3: Include `officer_note` in the serialized evidence payload**

In `src/procurement/adapters/aws/bedrock.py`, update the `evidence` dict built in `_message` (lines 327-335):

```python
        evidence = {
            "environment": request.environment.value,
            "eligible_alternatives": alternatives,
            "output_contract": {
                "forbidden_wrapper_fields": ["recommend", "manual_review"],
                "required_top_level_fields": self.output_schema["required"],
                "top_level_decision_field": "decision",
            },
        }
        if request.officer_note is not None:
            evidence["officer_note"] = request.officer_note
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/adapters/aws/test_bedrock.py -v`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/procurement/adapters/aws/bedrock.py tests/unit/adapters/aws/test_bedrock.py
git commit -m "feat(bedrock): include officer_note in the untrusted procurement_data payload"
```

---

### Task 5: `_run_case` captures `candidate_snapshot` at creation

**Files:**
- Modify: `src/procurement/api/services/scans.py:414-440` (`_run_case`)
- Test: `tests/unit/api/test_scans.py` (extend)

**Interfaces:**
- Consumes: `CandidateSnapshot` (Task 2), `ReplenishmentCandidate` (existing, `ports/mcp.py`).
- Produces: every newly created `CaseRecord` now has `candidate_snapshot` populated from the discovery-time candidate. Task 6's `refine_case` depends on this being non-`None` for any refinable case.

- [ ] **Step 1: Write the failing test**

In `tests/unit/api/test_scans.py`, add a new test near `test_manual_scan_returns_202_and_can_be_polled_to_completion` (after line 236):

```python
@pytest.mark.anyio
async def test_a_completed_case_persists_its_candidate_snapshot() -> None:
    workflow = SuccessfulWorkflow()
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
        application_repository=repository,
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
        case_id = cast(list[dict[str, object]], finished["results"])[0]["case_id"]

    record = await repository.get_case(CaseId(Environment.DEV, case_id))
    candidate = _one_candidate()[0]
    assert record is not None
    assert record.candidate_snapshot is not None
    assert record.candidate_snapshot.category_id == candidate.category_id
    assert record.candidate_snapshot.reorder_minimum == candidate.reorder_minimum
    assert record.candidate_snapshot.reorder_maximum == candidate.reorder_maximum
    assert record.candidate_snapshot.projected_quantity == candidate.projected_quantity
    assert (
        record.candidate_snapshot.projected_trigger_date
        == candidate.projected_trigger_date
    )
    assert record.refinement_count == 0
```

This requires `create_app` to accept an `application_repository` keyword so the test can inspect persisted state directly — check `src/procurement/api/app.py`'s `create_app` signature first; if it does not already expose this parameter, add it there passing through to `ScanService(repository=...)` (it likely already does, since `ScanService.__init__` already accepts `repository`). If `create_app` does not forward a repository parameter, extend it minimally to accept `application_repository: ApplicationRepository | None = None` and forward it to `ScanService(repository=application_repository, ...)`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/api/test_scans.py -v -k candidate_snapshot`
Expected: FAIL — `AssertionError: assert None is not None` (candidate_snapshot not yet set).

- [ ] **Step 3: Set `candidate_snapshot` when the case is created**

In `src/procurement/api/services/scans.py`, update the initial `CaseRecord(...)` construction inside `_run_case` (lines 421-430):

```python
    async def _run_case(
        self,
        *,
        scan_id: str,
        trigger: str,
        candidate: ReplenishmentCandidate,
    ) -> CaseSummary | None:
        case_id_value = f"{scan_id}:{candidate.product_id}"
        created_at = UtcTimestamp(datetime.now(tz=UTC))
        record = CaseRecord(
            case_id=CaseId(self._environment, case_id_value),
            revision=Revision(1),
            status=ScanStatus.QUEUED.value,
            trigger=trigger,
            created_at=created_at,
            updated_at=created_at,
            candidate_snapshot=CandidateSnapshot(
                category_id=candidate.category_id,
                reorder_minimum=candidate.reorder_minimum,
                reorder_maximum=candidate.reorder_maximum,
                projected_quantity=candidate.projected_quantity,
                projected_trigger_date=candidate.projected_trigger_date,
            ),
        )
```

Add `CandidateSnapshot` to the existing `from procurement.ports.repositories import (...)` block (lines 33-41):

```python
from procurement.ports.repositories import (
    ApplicationRepository,
    CandidateSnapshot,
    CaseRecord,
    CaseSummary,
    FailureRecord,
    InMemoryApplicationRepository,
    RecommendationRecord,
    ScanRecord,
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/api/test_scans.py -v -k candidate_snapshot`
Expected: PASS.

- [ ] **Step 5: Run the full API unit suite for regressions**

Run: `uv run pytest tests/unit/api -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/procurement/api/services/scans.py tests/unit/api/test_scans.py
git commit -m "feat(scans): persist a CandidateSnapshot on every newly created case"
```

---

### Task 6: `ScanService.refine_case` and `ErrorCode.REFINEMENT_LIMIT_REACHED`

**Files:**
- Modify: `src/procurement/domain/errors.py:17-35` (`ErrorCode`)
- Modify: `src/procurement/api/services/scans.py` (new `refine_case`, `_run_refinement`; `ScanSnapshot.refinement_count`; `_snapshot`)
- Test: `tests/unit/api/test_scans.py` (extend)

**Interfaces:**
- Consumes: `CandidateSnapshot`/`refinement_count` (Task 2, 5), `ScanState["officer_note"]` (Task 3), `RevisionConflictError` (existing, `ports/repositories.py`).
- Produces: `ScanService.refine_case(case_id: str, note: str) -> ScanSnapshot` (public API used by Task 7's route). `ScanSnapshot.refinement_count: int`, populated by `_snapshot`.

- [ ] **Step 1: Add the error code**

In `src/procurement/domain/errors.py`, add to the `ErrorCode` enum (after `RECONCILIATION_REQUIRED`, line 34):

```python
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    REFINEMENT_LIMIT_REACHED = "REFINEMENT_LIMIT_REACHED"
```

This code is intentionally left out of `_RETRYABLE_ERROR_CODES` (lines 37-43) — reaching the cap is permanent for this case.

- [ ] **Step 2: Write the failing tests**

In `tests/unit/api/test_scans.py`, add a `RefinableWorkflow` fake near the other workflow fakes (after `MultiCandidateWorkflow`, around line 105):

```python
class RefinableWorkflow(SuccessfulWorkflow):
    """Record officer notes and let a test control the returned result."""

    def __init__(self) -> None:
        super().__init__()
        self.officer_notes: list[str | None] = []

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        self.configs.append(config)
        self.officer_notes.append(state.get("officer_note"))
        return {
            **state,
            "result": replace(
                t27_approval_result(),
                rationale=f"Refined: {state.get('officer_note')}",
            ),
        }
```

Then add the test functions (after `test_a_completed_case_persists_its_candidate_snapshot` from Task 5):

```python
async def _approval_ready_case(
    client: AsyncClient, csrf_headers: dict[str, str]
) -> tuple[str, str]:
    accepted = await client.post("/api/v1/scans", headers=csrf_headers)
    scan_id = accepted.json()["scan_id"]
    finished = await _poll_until_finished(client, scan_id)
    case_id = cast(list[dict[str, object]], finished["results"])[0]["case_id"]
    return scan_id, case_id


@pytest.mark.anyio
async def test_refine_case_reruns_the_workflow_with_a_fresh_thread_id() -> None:
    workflow = RefinableWorkflow()
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
        application_repository=repository,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
        case_id = cast(list[dict[str, object]], finished["results"])[0]["case_id"]

        refined = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "Prioritize delivery speed this time."},
        )
        assert refined.status_code == 202
        assert refined.json()["status"] == "running"

        completed = await _poll_case_until_finished(client, scan_id, case_id)

    assert completed["refinement_count"] == 1
    assert completed["result"]["rationale"] == (
        "Refined: Prioritize delivery speed this time."
    )
    assert workflow.officer_notes == [None, "Prioritize delivery speed this time."]
    assert workflow.configs[0] == {"configurable": {"thread_id": case_id}}
    assert workflow.configs[1] == {
        "configurable": {"thread_id": f"{case_id}:refine-1"}
    }


@pytest.mark.anyio
async def test_refine_case_is_capped_at_three_attempts() -> None:
    workflow = RefinableWorkflow()
    repository = InMemoryApplicationRepository(environment=Environment.DEV)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
        application_repository=repository,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        scan_id, case_id = await _approval_ready_case(client, csrf_headers)
        for _ in range(3):
            await client.post(
                f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
                headers=csrf_headers,
                json={"note": "Try again."},
            )
            await _poll_case_until_finished(client, scan_id, case_id)

        rejected = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "One more time."},
        )

    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "REFINEMENT_LIMIT_REACHED"


@pytest.mark.anyio
async def test_refine_case_rejects_a_manual_review_case() -> None:
    class ManualReviewWorkflow(SuccessfulWorkflow):
        async def ainvoke(
            self, state: ScanState, *, config: Mapping[str, object]
        ) -> ScanState:
            self.configs.append(config)
            return {
                **state,
                "result": ManualReviewResult(
                    rationale="Evidence is insufficient.",
                    trade_offs=(),
                    risk_flags=("MANUAL_REVIEW_REQUIRED",),
                    uncertainty="No model selection is available.",
                    evidence_limitations=(),
                ),
            }

    application = create_app(
        scan_workflow=ManualReviewWorkflow(),
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        scan_id, case_id = await _approval_ready_case(client, csrf_headers)

        rejected = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "Try again."},
        )

    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "VALIDATION_FAILED"


@pytest.mark.anyio
async def test_concurrent_refinement_attempts_conflict() -> None:
    """A second writer racing the same expected_revision loses, deterministically.

    Two genuinely concurrent HTTP requests would race unpredictably in this
    sandbox; `ConflictingUpdateRepository` instead forces the exact interleaving
    a real race would produce -- the second `update_case` call for this case
    sees a stale `expected_revision` -- so the resulting REVISION_CONFLICT
    translation is tested deterministically.
    """

    workflow = SuccessfulWorkflow()
    repository = ConflictingUpdateRepository()
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
        application_repository=repository,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        scan_id, case_id = await _approval_ready_case(client, csrf_headers)

        repository.raise_next_update = True
        rejected = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "This attempt loses the simulated race."},
        )

    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "REVISION_CONFLICT"
```

Add `ConflictingUpdateRepository` next to the other repository test doubles (near `FailFirstUpdateRepository`, around line 142):

```python
class ConflictingUpdateRepository(InMemoryApplicationRepository):
    """Force the next update_case call to fail as a real race would."""

    def __init__(self) -> None:
        super().__init__(environment=Environment.DEV)
        self.raise_next_update = False

    async def update_case(
        self,
        record: CaseRecord,
        *,
        expected_revision: Revision,
        expires_at: UtcTimestamp,
    ) -> CaseRecord:
        if self.raise_next_update:
            self.raise_next_update = False
            raise RevisionConflictError("simulated concurrent refinement")
        return await super().update_case(
            record, expected_revision=expected_revision, expires_at=expires_at
        )
```

`RevisionConflictError`, `Revision`, and `UtcTimestamp` are already imported at the top of `tests/unit/api/test_scans.py` (lines 23-30) — no new imports are needed for this class.

Add a helper mirroring `_poll_until_finished` but scoped to one case (place it near `_poll_until_finished`, around line 176):

```python
async def _poll_case_until_finished(
    client: AsyncClient, scan_id: str, case_id: str
) -> dict[str, object]:
    for _ in range(500):
        response = await client.get(f"/api/v1/scans/{scan_id}/cases/{case_id}")
        body = cast(dict[str, object], response.json())
        if body["status"] not in {"queued", "running"}:
            return body
        await anyio.sleep(0.01)
    raise AssertionError("case did not finish")
```

`tests/unit/api/test_scans.py` currently imports only `ScanState` from `procurement.agent.state` (line 16). Change that import to also bring in `ManualReviewResult`:

```python
from procurement.agent.state import ManualReviewResult, ScanState
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_scans.py -v -k refine`
Expected: FAIL — `AttributeError: 'ScanService' object has no attribute 'refine_case'` (via 404/500 from the missing route, added in Task 7) or a direct `AttributeError` if called via a route stub. Since the route does not exist until Task 7, these tests will fail with 404s at this point — that is expected; Task 7 is what makes the route resolve to `refine_case`. Proceed to Step 4 to build the service method; the route wiring lands in Task 7 and these tests will only fully pass once both tasks are complete. Run them again at the end of Task 7 to confirm.

- [ ] **Step 4: Add `MAX_REFINEMENTS`, `ScanSnapshot.refinement_count`, and imports**

In `src/procurement/api/services/scans.py`, add the constant near the existing module constants (lines 43-45):

```python
DEFAULT_WORKFLOW_TIMEOUT_SECONDS = 120.0
MAX_SCAN_HISTORY = 100
MAX_REFINEMENTS = 3
_RETENTION_DAYS = {Environment.DEV: 30, Environment.PROD: 365}
```

Update the `ScanSnapshot` dataclass (lines 75-95) to add the new field at the end:

```python
@dataclass(frozen=True, slots=True)
class ScanSnapshot:
    """Immutable API view of one durable case record."""

    scan_id: str
    case_id: str
    status: ScanStatus
    trigger: ScanTrigger
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    evidence: tuple[ProcurementEvidence, ...]
    result: (
        ApprovalReadyResult
        | LegacyApprovalReadyResult
        | ManualReviewResult
        | NoValidOfferResult
        | None
    )
    error: ScanFailure | None
    refinement_count: int
```

Update the `_snapshot` method's `ScanSnapshot(...)` return (lines 792-807) to pass the new field:

```python
        return ScanSnapshot(
            scan_id=scan_id,
            case_id=record.case_id.value,
            status=ScanStatus(record.status),
            trigger=ScanTrigger(record.trigger),
            created_at=record.created_at.value,
            started_at=(
                record.started_at.value if record.started_at is not None else None
            ),
            completed_at=(
                record.completed_at.value if record.completed_at is not None else None
            ),
            evidence=record.evidence,
            result=result,
            error=error,
            refinement_count=record.refinement_count,
        )
```

Add `RevisionConflictError` and `ReplenishmentCandidate` (already imported) to the imports — update the `from procurement.ports.repositories import (...)` block (from Task 5's edit) to add `RevisionConflictError`:

```python
from procurement.ports.repositories import (
    ApplicationRepository,
    CandidateSnapshot,
    CaseRecord,
    CaseSummary,
    FailureRecord,
    InMemoryApplicationRepository,
    RecommendationRecord,
    RevisionConflictError,
    ScanRecord,
)
```

- [ ] **Step 5: Implement `refine_case` and `_run_refinement`**

In `src/procurement/api/services/scans.py`, add these two methods to `ScanService`, immediately after `get_case` (after line 297, before `_run_scan`):

```python
    async def refine_case(self, *, case_id: str, note: str) -> ScanSnapshot:
        """Reserve one bounded refinement attempt and schedule its background work."""

        try:
            id_ = CaseId(self._environment, case_id)
        except DomainError:
            id_ = None
        record = await self._repository.get_case(id_) if id_ is not None else None
        if record is None:
            raise DomainError(
                error_code=ErrorCode.VALIDATION_FAILED,
                safe_message="The requested case was not found.",
            )
        if (
            record.status != ScanStatus.SUCCEEDED.value
            or record.result is None
            or record.result.outcome != "approval_ready"
            or record.candidate_snapshot is None
        ):
            raise DomainError(
                error_code=ErrorCode.VALIDATION_FAILED,
                safe_message="Only an approval-ready case can be refined.",
            )
        if record.refinement_count >= MAX_REFINEMENTS:
            raise DomainError(
                error_code=ErrorCode.REFINEMENT_LIMIT_REACHED,
                safe_message="This case has reached its refinement limit.",
            )
        running_at = UtcTimestamp(datetime.now(tz=UTC))
        running = replace(
            record,
            revision=record.revision.next(),
            status=ScanStatus.RUNNING.value,
            started_at=running_at,
            updated_at=running_at,
        )
        try:
            running = await self._repository.update_case(
                running,
                expected_revision=record.revision,
                expires_at=self._expires_at(record.created_at),
            )
        except RevisionConflictError as error:
            raise DomainError(
                error_code=ErrorCode.REVISION_CONFLICT,
                safe_message="This case was already updated by another request.",
            ) from error
        await self._append_audit(running)
        task = asyncio.create_task(self._run_refinement(running, note=note))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self._snapshot(running)

    async def _run_refinement(self, running: CaseRecord, *, note: str) -> None:
        assert running.candidate_snapshot is not None
        assert running.result is not None
        assert running.result.product_id is not None
        assert running.result.product_name is not None
        scan_id, _, product_id = running.case_id.value.partition(":")
        snapshot = running.candidate_snapshot
        candidate = ReplenishmentCandidate(
            product_id=product_id,
            product_name=running.result.product_name,
            category_id=snapshot.category_id,
            reorder_minimum=snapshot.reorder_minimum,
            reorder_maximum=snapshot.reorder_maximum,
            projected_quantity=snapshot.projected_quantity,
            projected_trigger_date=snapshot.projected_trigger_date,
            skip_reason_code=None,
        )
        next_attempt = running.refinement_count + 1
        thread_id = f"{running.case_id.value}:refine-{next_attempt}"
        try:
            async with asyncio.timeout(self._workflow_timeout_seconds):
                state = await self._workflow.ainvoke(
                    {
                        "scan_id": scan_id,
                        "environment": self._environment,
                        "candidates": (candidate,),
                        "officer_note": note,
                    },
                    config={"configurable": {"thread_id": thread_id}},
                )
            terminal = self._apply_result(running, state)
        except TimeoutError:
            terminal = self._fail(
                running,
                error_code=ErrorCode.MCP_TIMEOUT,
                message="The procurement scan exceeded its workflow deadline.",
                retryable=True,
            )
        except Exception:
            terminal = self._fail(
                running,
                error_code=ErrorCode.LLM_UNAVAILABLE,
                message="The procurement scan could not be completed.",
                retryable=True,
            )
        completed_at = UtcTimestamp(datetime.now(tz=UTC))
        terminal = replace(
            terminal,
            revision=running.revision.next(),
            refinement_count=next_attempt,
            completed_at=completed_at,
            updated_at=completed_at,
        )
        terminal = await self._repository.update_case(
            terminal,
            expected_revision=running.revision,
            expires_at=self._expires_at(running.created_at),
        )
        await self._append_audit(terminal)
```

`create_app` (`src/procurement/api/app.py:41-53`) already accepts an `application_repository: ApplicationRepository | None = None` keyword and already forwards it to `ScanService(repository=application_repository, ...)` (line 71) — no change is needed there. The tests in Steps 2 and in Task 5 already work against the current signature.

- [ ] **Step 6: Add a test confirming a workflow failure during refinement still counts against the cap**

Add to `tests/unit/api/test_scans.py`, near the other refinement tests:

```python
class FailingRefinementWorkflow(SuccessfulWorkflow):
    """Succeed on the initial scan, then raise on every refinement attempt."""

    def __init__(self) -> None:
        super().__init__()
        self._invocations = 0

    async def ainvoke(
        self,
        state: ScanState,
        *,
        config: Mapping[str, object],
    ) -> ScanState:
        self.configs.append(config)
        self._invocations += 1
        if self._invocations == 1:
            return {**state, "result": t27_approval_result()}
        raise RuntimeError("simulated workflow failure during refinement")


@pytest.mark.anyio
async def test_a_failed_refinement_still_counts_against_the_cap() -> None:
    workflow = FailingRefinementWorkflow()
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="https://testserver"
    ) as client:
        csrf_headers = await sign_in(client)
        scan_id, case_id = await _approval_ready_case(client, csrf_headers)

        refined = await client.post(
            f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
            headers=csrf_headers,
            json={"note": "Try again."},
        )
        assert refined.status_code == 202
        completed = await _poll_case_until_finished(client, scan_id, case_id)

    assert completed["status"] == "failed"
    assert completed["refinement_count"] == 1
    assert completed["error"]["error_code"] == "LLM_UNAVAILABLE"
```

- [ ] **Step 7: Run tests to verify they still fail on the missing route (expected at this point)**

Run: `uv run pytest tests/unit/api/test_scans.py -v -k refine`
Expected: FAIL with 404 responses (no route registered yet) — confirms the service layer compiles and only the route is missing. This is expected; continue to Task 7.

- [ ] **Step 8: Commit**

```bash
git add src/procurement/domain/errors.py src/procurement/api/services/scans.py tests/unit/api/test_scans.py
git commit -m "feat(scans): add ScanService.refine_case with a capped, revision-guarded rerun"
```

---

### Task 7: API route and `CaseResponse.refinement_count`

**Files:**
- Modify: `src/procurement/api/errors.py:24-39` (`_HTTP_STATUS_BY_ERROR_CODE`)
- Modify: `src/procurement/api/routes/scans.py` (new `RefineCaseRequest`, new route, `CaseResponse.refinement_count`, `case_response`)
- Test: `tests/unit/api/test_scans.py` (the Task 6 tests now pass)

**Interfaces:**
- Consumes: `ScanService.refine_case` (Task 6).
- Produces: `POST /api/v1/scans/{scan_id}/cases/{case_id}/refine` → `CaseResponse` (202). `CaseResponse.refinement_count: int`. Task 8's frontend client depends on this exact route path and response shape.

- [ ] **Step 1: Map the new error code to an HTTP status**

In `src/procurement/api/errors.py`, add to `_HTTP_STATUS_BY_ERROR_CODE` (after line 38):

```python
    ErrorCode.RECONCILIATION_REQUIRED: status.HTTP_409_CONFLICT,
    ErrorCode.REFINEMENT_LIMIT_REACHED: status.HTTP_422_UNPROCESSABLE_CONTENT,
```

- [ ] **Step 2: Add the request model and `refinement_count` fields**

In `src/procurement/api/routes/scans.py`, update the `pydantic` import (line 9) to include `Field`:

```python
from pydantic import BaseModel, ConfigDict, Field
```

Add a new request model near the other response models (after `ScanErrorResponse`, before `CaseResponse`, around line 137):

```python
class RefineCaseRequest(BaseModel):
    """Bounded officer note submitted to re-evaluate one case."""

    model_config = _RESPONSE_CONFIG

    note: str = Field(min_length=1, max_length=280)
```

Update `CaseResponse` (lines 138-158) to add the new field:

```python
class CaseResponse(BaseModel):
    """Public representation of one case within a scan."""

    model_config = _RESPONSE_CONFIG

    scan_id: str
    case_id: str
    status: str
    trigger: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    evidence: tuple[dict[str, object], ...]
    result: (
        ApprovalReadyResponse
        | LegacyApprovalReadyResponse
        | ManualReviewResponse
        | NoValidOfferResponse
        | None
    )
    error: ScanErrorResponse | None
    refinement_count: int
```

Update `case_response` (lines 219-286) to pass the new field in the `CaseResponse(...)` return:

```python
    return CaseResponse(
        scan_id=snapshot.scan_id,
        case_id=snapshot.case_id,
        status=snapshot.status.value,
        trigger=snapshot.trigger.value,
        created_at=snapshot.created_at,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        evidence=tuple(item.to_dict() for item in snapshot.evidence),
        result=result,
        error=_error_response(snapshot.error),
        refinement_count=snapshot.refinement_count,
    )
```

- [ ] **Step 3: Add the route**

In `src/procurement/api/routes/scans.py`, add after `get_case` (after line 367, the final route in the file):

```python
@router.post(
    "/{scan_id}/cases/{case_id}/refine",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
async def refine_case(
    scan_id: str,
    case_id: str,
    body: RefineCaseRequest,
    request: Request,
) -> CaseResponse:
    """Reserve one bounded refinement attempt for an approval-ready case."""

    if not case_id.startswith(f"{scan_id}:"):
        raise DomainError(
            error_code=ErrorCode.VALIDATION_FAILED,
            safe_message="The requested case was not found.",
        )
    snapshot = await scan_service_from(request).refine_case(
        case_id=case_id, note=body.note
    )
    return case_response(snapshot)
```

- [ ] **Step 4: Run the Task 6 tests to verify they now pass**

Run: `uv run pytest tests/unit/api/test_scans.py -v -k refine`
Expected: PASS (all 5 refinement tests from Task 6, plus Task 5's candidate-snapshot test).

- [ ] **Step 5: Run the full backend unit suite for regressions**

Run: `uv run pytest tests/unit -v`

If this hangs or times out in this sandbox, split into two batches by directory (a known environment flakiness unrelated to this change):

Run: `uv run pytest tests/unit/adapters tests/unit/agent tests/unit/api -v`
Run: `uv run pytest tests/unit/domain tests/unit/observability tests/unit/ports -v`

Expected: PASS.

- [ ] **Step 6: Run static checks**

Run: `uv run ruff format src/procurement tests/unit && uv run ruff check --fix src/procurement tests/unit`
Run: `uv run mypy src/procurement`
Expected: no formatting diffs left uncommitted, no lint errors, no type errors.

- [ ] **Step 7: Commit**

```bash
git add src/procurement/api/errors.py src/procurement/api/routes/scans.py
git commit -m "feat(api): add POST /scans/{scan_id}/cases/{case_id}/refine"
```

---

### Task 8: Frontend `client.ts` — `refinement_count` and `refineCase`

**Files:**
- Modify: `frontend/src/api/client.ts` (`CaseDetail`, `parseCaseDetail`, new `refineCase`)
- Test: `frontend/tests/api-client.test.ts` (extend and fix existing fixtures)

**Interfaces:**
- Consumes: the API route from Task 7.
- Produces: `CaseDetail.refinement_count: number`; `refineCase(scanId: string, caseId: string, note: string, options?: RequestOptions): Promise<CaseDetail>`. Task 9's `RefinementPanel` component depends on both.

- [ ] **Step 1: Write the failing tests and fix existing fixtures**

In `frontend/tests/api-client.test.ts`, update `CASE_DETAIL_PAYLOAD` (lines 37-48) to include the new required field:

```typescript
const CASE_DETAIL_PAYLOAD = {
  scan_id: "scan-queued",
  case_id: "scan-queued:product-101",
  status: "queued",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: null,
  completed_at: null,
  evidence: [],
  result: null,
  error: null,
  refinement_count: 0,
};
```

This fixture is reused (via spread) by the `no_valid_offer` and `legacy` tests (lines 151-196), so this one edit keeps those passing once `refinement_count` becomes a required parsed field.

Add `refineCase` to the import list (line 3-11):

```typescript
import {
  ApiError,
  createManualScan,
  getCase,
  getScanAggregate,
  getSession,
  listRecentCases,
  listScans,
  refineCase,
} from "../src/api/client";
```

Add new tests after `test_officer_note...`-adjacent existing test block — place after the "parses a historical approval..." test (after line 196):

```typescript
it("posts a bounded note and parses the resulting running case", async () => {
  document.cookie = "stockai_csrf=opaque-csrf-token; path=/";
  const runningCase = {
    ...CASE_DETAIL_PAYLOAD,
    status: "running",
    started_at: "2026-08-05T10:05:00Z",
    refinement_count: 0,
  };
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(runningCase, 202));
  vi.stubGlobal("fetch", fetchMock);

  await expect(
    refineCase("scan-queued", "scan-queued:product-101", "Prioritize delivery."),
  ).resolves.toEqual(runningCase);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/scans/scan-queued/cases/scan-queued%3Aproduct-101/refine",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ note: "Prioritize delivery." }),
      headers: expect.objectContaining({
        "X-CSRF-Token": "opaque-csrf-token",
        "Content-Type": "application/json",
      }),
    }),
  );
});

it("rejects a refine response missing refinement_count", async () => {
  const { refinement_count: _omit, ...malformed } = CASE_DETAIL_PAYLOAD;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse(malformed, 202)),
  );

  const error = await refineCase(
    "scan-queued",
    "scan-queued:product-101",
    "Prioritize delivery.",
  ).catch((reason: unknown) => reason);

  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ code: "INVALID_RESPONSE" });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/api-client.test.ts`
Expected: FAIL — `refineCase` is not exported; existing `CASE_DETAIL_PAYLOAD`-based tests fail once `refinement_count` becomes required (until Step 3 lands).

- [ ] **Step 3: Add `refinement_count` to `CaseDetail` and its parser**

In `frontend/src/api/client.ts`, update `CaseDetail` (lines 184-195):

```typescript
export interface CaseDetail {
  scan_id: string;
  case_id: string;
  status: ScanStatus;
  trigger: ScanTrigger;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  evidence: ProcurementEvidence[];
  result: ScanResult | null;
  error: ScanFailure | null;
  refinement_count: number;
}
```

Update `parseCaseDetail` (lines 615-658) to validate and return the new field:

```typescript
function parseCaseDetail(value: unknown): CaseDetail {
  if (
    !isRecord(value) ||
    typeof value.scan_id !== "string" ||
    typeof value.case_id !== "string" ||
    typeof value.status !== "string" ||
    !CASE_STATUSES.includes(value.status) ||
    typeof value.trigger !== "string" ||
    !["manual", "cron"].includes(value.trigger) ||
    typeof value.created_at !== "string" ||
    !isNullableString(value.started_at) ||
    !isNullableString(value.completed_at) ||
    !Number.isInteger(value.refinement_count) ||
    (value.refinement_count as number) < 0
  ) {
    return invalidResponse();
  }

  const result = parseResult(value.result);
  const error = parseFailure(value.error);
  if (!Array.isArray(value.evidence) || value.evidence.length > 50) {
    return invalidResponse();
  }
  const evidence = value.evidence.map(parseEvidence);
  if (
    (value.status === "succeeded" && result === null) ||
    (value.status === "failed" && error === null) ||
    (["queued", "running"].includes(value.status) &&
      (result !== null || error !== null))
  ) {
    return invalidResponse();
  }

  return {
    scan_id: value.scan_id,
    case_id: value.case_id,
    status: value.status as ScanStatus,
    trigger: value.trigger as ScanTrigger,
    created_at: value.created_at,
    started_at: value.started_at,
    completed_at: value.completed_at,
    evidence,
    result,
    error,
    refinement_count: value.refinement_count as number,
  };
}
```

- [ ] **Step 4: Add `refineCase`**

In `frontend/src/api/client.ts`, add after `getCase` (after line 863):

```typescript
export async function refineCase(
  scanId: string,
  caseId: string,
  note: string,
  options: RequestOptions = {},
): Promise<CaseDetail> {
  const csrfToken = cookieValue(CSRF_COOKIE_NAME);
  const response = await request(
    `${SCANS_PATH}/${encodeURIComponent(scanId)}/cases/${encodeURIComponent(caseId)}/refine`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(csrfToken === null ? {} : { "X-CSRF-Token": csrfToken }),
      },
      body: JSON.stringify({ note }),
      signal: options.signal,
    },
  );
  if (response.status !== 202) {
    return invalidResponse();
  }
  return parseCaseDetail(response.body);
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/api-client.test.ts`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 6: Run the full frontend test suite for regressions**

Run: `cd frontend && npx vitest run`
Expected: PASS (other suites, e.g. `recommendation.test.tsx`, will fail until Task 9 updates `BASE_SCAN` — confirm only `api-client.test.ts`-adjacent failures are new and unrelated ones are pre-existing from the missing `refinement_count` field; Task 9 resolves those).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/tests/api-client.test.ts
git commit -m "feat(client): add refinement_count and refineCase to the API client"
```

---

### Task 9: Frontend `RefinementPanel` and `RecommendationPage` wiring

**Files:**
- Create: `frontend/src/components/RefinementPanel.tsx`
- Modify: `frontend/src/pages/RecommendationPage.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/tests/recommendation.test.tsx` (extend and fix `BASE_SCAN`)
- Test: `frontend/tests/refinement-panel.test.tsx` (new file)

**Interfaces:**
- Consumes: `refineCase`, `CaseDetail.refinement_count` (Task 8).
- Produces: `RefinementPanel({ scanId, caseId, refinementCount, onRefined }: RefinementPanelProps)` rendered from `RecommendationPage` for `approval_ready` results only.

- [ ] **Step 1: Fix the existing fixture and write the failing panel unit tests**

In `frontend/tests/recommendation.test.tsx`, update `BASE_SCAN` (lines 7-125) to add the new required field:

```typescript
const BASE_SCAN = {
  scan_id: "scan-101",
  case_id: "scan-101:product-101",
  status: "succeeded",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: "2026-08-05T10:00:01Z",
  completed_at: "2026-08-05T10:00:02Z",
  refinement_count: 0,
  evidence: [
    // ... unchanged ...
```

(Keep the rest of the object exactly as-is — only add the `refinement_count: 0` line.)

Create `frontend/tests/refinement-panel.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RefinementPanel } from "../src/components/RefinementPanel";
import type { CaseDetail } from "../src/api/client";

function jsonResponse(body: unknown, status = 202): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RefinementPanel", () => {
  it("submits a note and reports the running case back to the caller", async () => {
    const user = userEvent.setup();
    const runningCase = { case_id: "scan-101:product-101" } as CaseDetail;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(runningCase)));
    const onRefined = vi.fn();

    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={0}
        onRefined={onRefined}
      />,
    );

    await user.type(
      screen.getByLabelText("Refinement note"),
      "Prioritize delivery speed.",
    );
    await user.click(screen.getByRole("button", { name: "Submit refinement" }));

    expect(await screen.findByText("Submit refinement")).toBeEnabled();
    expect(onRefined).toHaveBeenCalledWith(runningCase);
  });

  it("shows how many of the three refinements have been used", () => {
    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={2}
        onRefined={vi.fn()}
      />,
    );

    expect(screen.getByText("2 of 3 refinements used")).toBeInTheDocument();
  });

  it("disables the input once the cap is reached", () => {
    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={3}
        onRefined={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("Refinement note")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Refinement limit reached (3/3). Run a new scan for a fresh recommendation.",
      ),
    ).toBeInTheDocument();
  });

  it("shows a safe error message when the request fails", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { error_code: "VALIDATION_FAILED", message: "Note is invalid.", retryable: false },
          422,
        ),
      ),
    );

    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={0}
        onRefined={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("Refinement note"), "A note.");
    await user.click(screen.getByRole("button", { name: "Submit refinement" }));

    expect(await screen.findByText("Note is invalid.")).toBeInTheDocument();
  });

  it("disables submit until a note is entered", () => {
    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={0}
        onRefined={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Submit refinement" })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/refinement-panel.test.tsx`
Expected: FAIL — `Failed to resolve import "../src/components/RefinementPanel"`.

- [ ] **Step 3: Create `RefinementPanel.tsx`**

Create `frontend/src/components/RefinementPanel.tsx`:

```tsx
import { useState } from "react";

import { ApiError, refineCase, type CaseDetail } from "../api/client";

const MAX_NOTE_LENGTH = 280;
const MAX_REFINEMENTS = 3;

interface RefinementPanelProps {
  scanId: string;
  caseId: string;
  refinementCount: number;
  onRefined: (scan: CaseDetail) => void;
}

export function RefinementPanel({
  scanId,
  caseId,
  refinementCount,
  onRefined,
}: RefinementPanelProps) {
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const atLimit = refinementCount >= MAX_REFINEMENTS;

  async function submit() {
    const trimmed = note.trim();
    if (trimmed.length === 0 || trimmed.length > MAX_NOTE_LENGTH) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const nextScan = await refineCase(scanId, caseId, trimmed);
      setNote("");
      onRefined(nextScan);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "The request could not be completed.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="refinement-title" className="panel refinement-panel">
      <h3 id="refinement-title">Refine this recommendation</h3>
      {atLimit ? (
        <p className="refinement-limit">
          Refinement limit reached (3/3). Run a new scan for a fresh recommendation.
        </p>
      ) : (
        <>
          <p className="refinement-hint">
            Add situational context, such as favoring delivery speed or avoiding a
            vendor for a temporary reason, and get this case re-evaluated.
          </p>
          <textarea
            aria-label="Refinement note"
            maxLength={MAX_NOTE_LENGTH}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            disabled={submitting}
          />
          <div className="refinement-controls">
            <span className="refinement-count">
              {refinementCount} of {MAX_REFINEMENTS} refinements used
            </span>
            <button
              className="primary-button"
              type="button"
              onClick={() => void submit()}
              disabled={submitting || note.trim().length === 0}
              aria-busy={submitting}
            >
              {submitting ? "Submitting…" : "Submit refinement"}
            </button>
          </div>
          {error ? (
            <p className="notice notice--error" role="alert">
              {error}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/refinement-panel.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Wire the panel into `RecommendationPage.tsx`**

In `frontend/src/pages/RecommendationPage.tsx`, add the import (after line 13):

```typescript
import { RefinementPanel } from "../components/RefinementPanel";
```

Add a `refinementNonce` state variable and `handleRefined` callback inside the `RecommendationPage` component, and include `refinementNonce` in the poll effect's dependency array. Update the component (lines 209-263):

```typescript
export function RecommendationPage({
  scanId,
  caseId,
  onBack,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
}: RecommendationPageProps) {
  const [scan, setScan] = useState<CaseDetail | null>(null);
  const [requestError, setRequestError] = useState<UiError | null>(null);
  const [refinementNonce, setRefinementNonce] = useState(0);

  function handleRefined(nextScan: CaseDetail) {
    setScan(nextScan);
    setRequestError(null);
    setRefinementNonce((value) => value + 1);
  }

  useEffect(() => {
    let active = true;
    let attempts = 0;
    let timer: number | undefined;
    let controller: AbortController | undefined;

    async function poll() {
      attempts += 1;
      controller = new AbortController();
      try {
        const nextScan = await getCase(scanId, caseId, {
          signal: controller.signal,
        });
        if (!active) {
          return;
        }
        setScan(nextScan);
        setRequestError(null);
        if (nextScan.status === "queued" || nextScan.status === "running") {
          if (attempts >= maxPollAttempts) {
            setRequestError({
              code: "POLL_LIMIT_REACHED",
              message:
                "The scan is still running. Return to the overview and check again shortly.",
            });
            return;
          }
          timer = window.setTimeout(() => void poll(), pollIntervalMs);
        }
      } catch (error) {
        if (active && !isAbortError(error)) {
          setRequestError(safeError(error));
        }
      }
    }

    void poll();
    return () => {
      active = false;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      controller?.abort();
    };
  }, [caseId, maxPollAttempts, pollIntervalMs, scanId, refinementNonce]);
```

Then, in the render branch that shows the completed result (lines 308-321), add the panel after the evidence block:

```typescript
      ) : scan.result ? (
        <>
          <RecommendationSummary scan={scan} evidence={findRecommendedEvidence(scan)} />
          {findRecommendedEvidence(scan) ? (
            <ProcurementEvidence
              evidence={findRecommendedEvidence(scan)!}
              selectedOfferId={
                scan.result.outcome === "approval_ready" && scan.result.offer_id !== null
                  ? scan.result.offer_id
                  : null
              }
            />
          ) : null}
          {scan.result.outcome === "approval_ready" ? (
            <RefinementPanel
              scanId={scanId}
              caseId={caseId}
              refinementCount={scan.refinement_count}
              onRefined={handleRefined}
            />
          ) : null}
        </>
      ) : (
```

- [ ] **Step 6: Add a test confirming the panel renders and updates the page**

In `frontend/tests/recommendation.test.tsx`, add a new test after the existing tests (find the end of the `describe` block and add before its closing `});`):

```typescript
it("shows a refinement panel for an approval-ready result and restarts polling after a submission", async () => {
  const user = userEvent.setup();
  const runningScan = {
    ...BASE_SCAN,
    status: "running",
    result: null,
    completed_at: null,
    refinement_count: 0,
  };
  const refinedScan = {
    ...BASE_SCAN,
    refinement_count: 1,
    result: {
      ...BASE_SCAN.result,
      rationale: "Refined: Prioritize delivery speed.",
    },
  };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse(BASE_SCAN))
    .mockResolvedValueOnce(jsonResponse(runningScan, 202))
    .mockResolvedValueOnce(jsonResponse(refinedScan));
  vi.stubGlobal("fetch", fetchMock);

  render(
    <RecommendationPage scanId="scan-101" caseId="scan-101:product-101" onBack={vi.fn()} />,
  );

  await screen.findByLabelText("Refinement note");
  await user.type(
    screen.getByLabelText("Refinement note"),
    "Prioritize delivery speed.",
  );
  await user.click(screen.getByRole("button", { name: "Submit refinement" }));

  expect(await screen.findByText("Scan in progress")).toBeInTheDocument();
  expect(
    await screen.findByText("Refined: Prioritize delivery speed."),
  ).toBeInTheDocument();
  expect(screen.getByText("1 of 3 refinements used")).toBeInTheDocument();
});
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/recommendation.test.tsx tests/refinement-panel.test.tsx`
Expected: PASS.

- [ ] **Step 8: Add CSS for the panel**

In `frontend/src/styles.css`, insert after the `.reasoning-panel ul` block (after line 835):

```css
.refinement-panel {
  padding: 1rem;
  border: 1px solid #dce4fa;
  border-radius: 0.8rem;
  background: #fff;
}

.refinement-panel h3 {
  margin: 0 0 0.5rem;
  color: #3157c8;
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.refinement-hint {
  margin: 0 0 0.6rem;
  color: #51617c;
  font-size: 0.8rem;
}

.refinement-panel textarea {
  width: 100%;
  min-height: 4.5rem;
  padding: 0.6rem;
  border: 1px solid #cbd5f0;
  border-radius: 0.5rem;
  font: inherit;
  resize: vertical;
}

.refinement-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 0.6rem;
}

.refinement-count {
  color: #51617c;
  font-size: 0.75rem;
}

.refinement-limit {
  margin: 0;
  color: #92400e;
  font-size: 0.83rem;
}
```

- [ ] **Step 9: Run the full frontend test suite for regressions**

Run: `cd frontend && npx vitest run`
Expected: PASS.

- [ ] **Step 10: Run frontend type checking and lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint src tests`
Expected: no type errors, no lint errors.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/components/RefinementPanel.tsx frontend/src/pages/RecommendationPage.tsx frontend/src/styles.css frontend/tests/recommendation.test.tsx frontend/tests/refinement-panel.test.tsx
git commit -m "feat(frontend): add a bounded refinement panel to the recommendation page"
```

---

### Task 10: Integration test over the real MCP transport

**Files:**
- Modify: `tests/integration/test_walking_skeleton.py`

**Interfaces:**
- Consumes: the full stack from Tasks 1-7.
- Produces: nothing new consumed elsewhere — this is end-to-end coverage.

- [ ] **Step 1: Write the test**

In `tests/integration/test_walking_skeleton.py`, add after `test_local_scan_evaluates_multiple_candidates_as_isolated_cases` (after line 119):

```python
def test_local_case_can_be_refined_once_with_an_officer_note(
    tmp_path: Path,
) -> None:
    with run_local_skeleton(tmp_path) as skeleton:
        with httpx.Client(base_url=skeleton.api_url, timeout=5) as client:
            auth_headers = sign_in_sync(client)
            accepted = client.post("/api/v1/scans", headers=auth_headers)
            detail = _poll_scan(
                client,
                accepted.headers["location"],
                headers=auth_headers,
            )
            scan_id = detail.json()["scan_id"]
            case_id = detail.json()["results"][0]["case_id"]

            refined = client.post(
                f"/api/v1/scans/{scan_id}/cases/{case_id}/refine",
                headers=auth_headers,
                json={"note": "Prioritize delivery speed this time."},
            )
            refined_detail = _poll_scan(
                client,
                f"/api/v1/scans/{scan_id}/cases/{case_id}",
                headers=auth_headers,
            )

    assert refined.status_code == 202
    assert refined.json()["status"] == "running"
    assert refined_detail.status_code == 200
    assert refined_detail.json()["status"] == "succeeded"
    assert refined_detail.json()["refinement_count"] == 1
    assert refined_detail.json()["result"]["outcome"] == "approval_ready"
```

- [ ] **Step 2: Run the test to verify it fails or passes for the right reason**

Run: `uv run pytest tests/integration/test_walking_skeleton.py -v -k refined`
Expected: PASS if Tasks 1-7 are complete and committed (this test exercises no new code of its own — it is a regression guard over the real local API + MCP processes). If it fails, the failure indicates a gap in Tasks 1-7, not in this test; investigate there before treating this task as done.

- [ ] **Step 3: Run the full integration suite in two batches (known sandbox flakiness workaround)**

Run: `uv run pytest tests/integration/test_api_agent_mcp.py tests/integration/test_dynamodb_local.py tests/integration/test_mcp_real_odoo.py tests/integration/test_walking_skeleton.py -v`
Run: `uv run pytest tests/integration/test_walking_skeleton_failure.py -v` (plus any remaining files in the directory not covered above)

Expected: PASS. If the full-directory single invocation hangs, this is the established sandbox-flakiness workaround from prior sub-projects in this project — not a code issue.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_walking_skeleton.py
git commit -m "test(integration): cover a bounded case refinement over the real MCP transport"
```

---

### Task 11: Final self-review and manual verification

**Files:** none (verification only).

- [ ] **Step 1: Re-run the complete backend and frontend suites**

Run: `uv run pytest tests/unit -v` (split into two batches by directory if it hangs, per the established workaround)
Run: `uv run pytest tests/integration -v` (split into two batches by file, per the established workaround)
Run: `cd frontend && npx vitest run`

Expected: PASS across all three.

- [ ] **Step 2: Re-run static checks**

Run: `uv run ruff format src/procurement tests && uv run ruff check src/procurement tests`
Run: `uv run mypy src/procurement`
Run: `cd frontend && npx tsc --noEmit && npx eslint src tests`

Expected: clean.

- [ ] **Step 3: Manual browser verification**

Start the local docker-compose stack (per this project's established local-run workflow) and, through the browser:

1. Run a manual scan to completion, reaching an `approval_ready` case.
2. Open its recommendation page and confirm the "Refine this recommendation" panel appears with "0 of 3 refinements used".
3. Submit a note (e.g. "Avoid the current vendor, temporary issue.") and confirm the page shows the in-progress state, then a new result and "1 of 3 refinements used".
4. Repeat twice more to reach the cap; confirm the panel switches to the disabled "Refinement limit reached (3/3)" message and the input disappears.
5. Confirm a `manual_review` or `no_valid_offer` case never shows the refinement panel (spec non-goal).

Note: as documented in the `no-valid-offer-improvements` sub-project's final verification, the local docker-compose fake-odoo cannot easily produce every scenario on demand (its `PROCUREMENT_FAKE_ODOO_SCENARIO` toggle affects candidate discovery too broadly to isolate individual outcomes). If reaching a specific outcome through the browser is impractical, rely on the automated backend/frontend test coverage from Tasks 1-10 for that scenario and note the gap explicitly rather than claiming an untested manual check passed.

- [ ] **Step 4: Note the known `case_summaries` staleness limitation**

`ScanRecord.case_summaries` (the parent scan's aggregate view, used by `GET /api/v1/scans` and `GET /api/v1/scans/{scan_id}`) is populated once when the owning scan completes and is never updated by a refinement. After a refinement, the scan-level list will still show the case's pre-refinement outcome/amount until a new scan runs, even though `GET /api/v1/scans/{scan_id}/cases/{case_id}` and `GET /api/v1/cases` (case-level views) correctly reflect the refined result. This matches the approved spec, which scopes refinement to the case-level record only and does not mention updating scan aggregates. Confirm this is still acceptable; if not, it is out of scope for this plan and would need its own follow-up design.

- [ ] **Step 5: Report completion**

Summarize what changed across all 10 implementation tasks, confirm every automated test suite passes, and flag the `case_summaries` staleness note from Step 4 to the user as a known, spec-consistent limitation rather than a defect.
