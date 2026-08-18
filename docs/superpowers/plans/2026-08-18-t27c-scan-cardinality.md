# T27C Scan-Cardinality Implementation Plan

> **For agentic workers:** The `superpowers:subagent-driven-development` and
> `superpowers:executing-plans` sub-skills are not present in this
> repository's skill set. Execute tasks in order, one at a time, running
> each task's verification steps before moving to the next. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one scan evaluate every replenishment candidate independently
— deterministic evidence gathering then LLM reasoning per candidate, each
producing its own case and result — instead of arbitrating one winner across
all candidates. Add the scan-detail results table + outcome donut
(`Scan_details.png`), reusing the recommendation-detail page built in
sub-project 2 unchanged for each case.

**Architecture:** `discover_candidates` moves out of the LangGraph graph
into a plain call made once per scan by `ScanService`. The graph itself
starts from `gather_evidence`, now seeded with exactly one candidate per
invocation, and is invoked once per candidate with its own `thread_id`
(`{scan_id}:{product_id}`). A new `ScanRecord` aggregates the resulting case
summaries; a new case-detail API route serves one case's full detail to the
unmodified `RecommendationPage`.

**Tech Stack:** Python 3.12, LangGraph, FastAPI/Pydantic, DynamoDB
single-table adapter (+ in-memory fake), React/TypeScript/Vitest.

## Global Constraints

- No LLM prompt or `StructuredLlmPort`/`RecommendationRequest` shape changes
  — `RecommendationRequest.candidates` already accepts 1..25 items
  (`ports/llm.py:67`), so passing a 1-tuple is sufficient; only the caller
  changes.
- No draft/approval/confirmation logic (T28/T29's own work). `ConfirmedResult`
  is added as a type-only placeholder, never produced by any code path here.
- No home page changes (sub-project 3, separate spec/plan).
- Reuse every component built in sub-project 2
  (`RecommendationHeader`, `OfferComparison`, `BudgetPanel`,
  `ProcurementEvidence`, `RecommendationPage`) unmodified for case detail.
- Spec reference: `docs/superpowers/specs/2026-08-18-t27c-scan-cardinality-design.md`
- Run `make check` (or the focused `pytest`/`ruff`/`mypy` subset touched)
  after each backend task, and `npm run typecheck && npm run lint && npm test`
  after each frontend task, from their respective directories.

---

## Task 1: Add `NoValidOfferResult` to the agent result union

**Files:**
- Modify: `src/procurement/agent/state.py:69-101`
- Create: `tests/unit/agent/test_state.py`

**Interfaces:**
- Produces: `NoValidOfferResult(product_id: str, product_name: str,
  rationale: str, evidence_limitations: tuple[str, ...] = ())` — frozen
  dataclass, `read_only` property returns `True`, matching the shape of
  `ManualReviewResult`/`ApprovalReadyResult`.
- `ScanResult` union gains this variant.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agent/test_state.py`:

```python
from procurement.agent.state import NoValidOfferResult


def test_no_valid_offer_result_is_frozen_and_read_only() -> None:
    result = NoValidOfferResult(
        product_id="product-1",
        product_name="Fictional Widget",
        rationale="No approved vendor offer is eligible for this product.",
    )
    assert result.product_id == "product-1"
    assert result.evidence_limitations == ()
    assert result.read_only is True
    with pytest.raises(AttributeError):
        result.product_id = "product-2"  # type: ignore[misc]
```

Add `import pytest` at the top of the file.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/agent/test_state.py -v
```

Expected: FAIL with `ImportError: cannot import name 'NoValidOfferResult'`.

- [ ] **Step 3: Add the type**

In `src/procurement/agent/state.py`, insert after the `UnresolvedResult`
class (currently lines 69-76) and before `ManualReviewResult`:

```python
@dataclass(frozen=True, slots=True)
class NoValidOfferResult:
    """A candidate correctly evaluated with zero eligible vendor offers."""

    product_id: str
    product_name: str
    rationale: str
    evidence_limitations: tuple[str, ...] = ()

    @property
    def read_only(self) -> bool:
        return True
```

Update the `ScanResult` union (currently lines 96-101):

```python
ScanResult = (
    ApprovalReadyResult
    | LegacyApprovalReadyResult
    | ManualReviewResult
    | NoValidOfferResult
    | UnresolvedResult
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/agent/test_state.py -v
```

Expected: PASS.

- [ ] **Step 5: Run focused quality checks**

```bash
cd /home/weam/StockAI && uv run ruff check src/procurement/agent/state.py tests/unit/agent/test_state.py
uv run mypy src/procurement/agent/state.py
```

Expected: both pass with no errors.

- [ ] **Step 6: Commit**

```bash
git add src/procurement/agent/state.py tests/unit/agent/test_state.py
git commit -m "feat(agent): add NoValidOfferResult outcome variant"
```

---

## Task 2: Restructure the graph for one candidate per invocation

**Files:**
- Modify: `src/procurement/agent/nodes/walking_skeleton.py`
- Modify: `src/procurement/agent/state.py` (add `skip_reason` field)
- Modify: `src/procurement/agent/graph.py`
- Modify: `tests/unit/agent/test_walking_skeleton.py`

**Interfaces:**
- Consumes: `ScanState["candidates"]` now seeded by the caller with exactly
  one `ReplenishmentCandidate` before invocation (no longer populated by a
  `discover_candidates` graph node).
- Produces: `discover_candidates` becomes a plain async method still on
  `WalkingSkeletonNodes`, callable directly (not a graph node) —
  `async def discover_candidates(self, *, environment: Environment) ->
  tuple[ReplenishmentCandidate, ...] | UnresolvedResult`. `gather_evidence`
  now fetches evidence for the one seeded candidate and branches on its
  `skip_reason_code`: `NO_SHORTAGE`/`FULLY_COVERED` → sets
  `state["skip_reason"]` (no case should be persisted for this candidate);
  `NO_VALID_OFFER` → sets `state["result"]` to `NoValidOfferResult`;
  `BUDGET_UNAVAILABLE` → sets `state["result"]` to
  `UnresolvedResult(error_code=ErrorCode.ODOO_UNAVAILABLE, retryable=True,
  ...)`; `None` → proceeds normally. The compiled graph's entry point moves
  from `discover_candidates` to `gather_evidence`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/agent/test_walking_skeleton.py`, add (following the existing
`FakeMcp`/`FakeStructuredLlm` fixture patterns already in that file — reuse
the existing `_candidate()` helper and `FakeMcp` class, just change what
`get_procurement_evidence` returns per test):

```python
@pytest.mark.anyio
async def test_discover_candidates_is_callable_directly_without_the_graph() -> None:
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV,
            candidates=(_candidate(),),
            next_cursor=None,
        )
    )
    nodes = WalkingSkeletonNodes(
        mcp=mcp,
        llm=FakeStructuredLlm(response=t27_recommendation()),
        metrics=create_agent_metrics(),
        logger=configure_json_logging(
            service="procurement-api", environment="dev", version="test"
        ),
        company_id="1",
    )
    candidates = await nodes.discover_candidates(environment=Environment.DEV)
    assert candidates == (_candidate(),)


@pytest.mark.anyio
async def test_discover_candidates_returns_unresolved_when_empty() -> None:
    mcp = FakeMcp(
        page=CandidatePage(environment=Environment.DEV, candidates=(), next_cursor=None)
    )
    nodes = WalkingSkeletonNodes(
        mcp=mcp,
        llm=FakeStructuredLlm(response=t27_recommendation()),
        metrics=create_agent_metrics(),
        logger=configure_json_logging(
            service="procurement-api", environment="dev", version="test"
        ),
        company_id="1",
    )
    result = await nodes.discover_candidates(environment=Environment.DEV)
    assert isinstance(result, UnresolvedResult)
    assert result.error_code is ErrorCode.NO_VALID_OFFER


@pytest.mark.anyio
async def test_graph_produces_no_valid_offer_result_for_zero_eligible_offers() -> None:
    evidence = t27_evidence()
    no_offer_evidence = dataclasses.replace(
        evidence, offers=(), skip_reason_code="NO_VALID_OFFER"
    )
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV, candidates=(_candidate(),), next_cursor=None
        ),
        evidence=no_offer_evidence,
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    graph = build_walking_skeleton_graph(
        mcp=mcp,
        llm=llm,
        metrics=create_agent_metrics(),
        logger=configure_json_logging(
            service="procurement-api", environment="dev", version="test"
        ),
    )
    final_state = await graph.ainvoke(
        {
            "scan_id": "scan-1",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        },
        config={"configurable": {"thread_id": "scan-1:product-1"}},
    )
    assert isinstance(final_state["result"], NoValidOfferResult)
    assert llm.requests == []  # the LLM must never be called for a deterministic skip


@pytest.mark.anyio
async def test_graph_skips_silently_for_no_shortage() -> None:
    evidence = t27_evidence()
    covered_evidence = dataclasses.replace(evidence, skip_reason_code="FULLY_COVERED")
    mcp = FakeMcp(
        page=CandidatePage(
            environment=Environment.DEV, candidates=(_candidate(),), next_cursor=None
        ),
        evidence=covered_evidence,
    )
    llm = FakeStructuredLlm(response=t27_recommendation())
    graph = build_walking_skeleton_graph(
        mcp=mcp,
        llm=llm,
        metrics=create_agent_metrics(),
        logger=configure_json_logging(
            service="procurement-api", environment="dev", version="test"
        ),
    )
    final_state = await graph.ainvoke(
        {
            "scan_id": "scan-1",
            "environment": Environment.DEV,
            "candidates": (_candidate(),),
        },
        config={"configurable": {"thread_id": "scan-1:product-1"}},
    )
    assert "result" not in final_state
    assert final_state["skip_reason"] == "FULLY_COVERED"
    assert llm.requests == []
```

Check the existing `FakeMcp` fixture at the top of
`tests/unit/agent/test_walking_skeleton.py` — if it does not yet accept an
`evidence` override parameter for `get_procurement_evidence`'s return value,
add one (it currently likely returns a fixed evidence value per the
`t27_evidence()` helper already imported in that file; extend its
constructor with `evidence: ProcurementEvidence | None = None` and have
`get_procurement_evidence` return `self.evidence or t27_evidence()` if not
already parameterized this way — check the actual current fixture body
before editing, since the research for this plan did not capture its full
implementation, only its role).

Add `import dataclasses` and `from procurement.agent.state import
NoValidOfferResult, UnresolvedResult` (if not already imported) at the top
of the test file.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/agent/test_walking_skeleton.py -v -k "discover_candidates_is_callable or discover_candidates_returns_unresolved or no_valid_offer_result_for_zero or skips_silently_for_no_shortage"
```

Expected: FAIL — `discover_candidates` still requires a full `ScanState`
positional argument and is still wired as a graph node; `gather_evidence`
does not yet branch on `skip_reason_code` beyond the existing all-or-nothing
check.

- [ ] **Step 3: Add `skip_reason` to `ScanState`**

In `src/procurement/agent/state.py`, update the `ScanState` TypedDict
(currently lines 104-112):

```python
class ScanState(TypedDict, total=False):
    scan_id: str
    environment: Environment
    candidates: Annotated[tuple[ReplenishmentCandidate, ...], UntrackedValue]
    evidence: Annotated[tuple[ProcurementEvidence, ...], UntrackedValue]
    recommendation: Annotated[StructuredRecommendation, UntrackedValue]
    result: ScanResult
    skip_reason: str
```

- [ ] **Step 4: Extract `discover_candidates` into a plain callable**

In `src/procurement/agent/nodes/walking_skeleton.py`, replace the
`discover_candidates` method (currently lines 45-125) with:

```python
    async def discover_candidates(
        self, *, environment: Environment
    ) -> tuple[ReplenishmentCandidate, ...] | UnresolvedResult:
        """Call the MCP port and retain only validated candidate data.

        Called once per scan by the orchestration layer, not as a graph
        node — its result seeds one graph invocation per candidate.
        """

        started_at = perf_counter()
        try:
            page = await self.mcp.list_replenishment_candidates(
                environment=environment,
                horizon_days=14,
                limit=50,
            )
        except McpReadError as error:
            error_code = (
                ErrorCode.MCP_TIMEOUT
                if isinstance(error, McpTimeoutError)
                else ErrorCode.ODOO_UNAVAILABLE
            )
            self._record_mcp_completion(
                environment=environment,
                started_at=started_at,
                status="error",
                error_code=error_code,
                retry_count=error.retry_count,
            )
            if isinstance(error, McpTimeoutError):
                self.metrics.record_mcp_timeout(tool="list_replenishment_candidates")
            return UnresolvedResult(
                error_code=error_code,
                message=error.safe_message,
                retryable=True,
                retry_count=error.retry_count,
            )
        except Exception:
            self._record_mcp_completion(
                environment=environment,
                started_at=started_at,
                status="error",
                error_code=ErrorCode.ODOO_UNAVAILABLE,
            )
            return UnresolvedResult(
                error_code=ErrorCode.ODOO_UNAVAILABLE,
                message="The procurement source is unavailable.",
                retryable=True,
            )

        if page.environment is not environment:
            self._record_mcp_completion(
                environment=environment,
                started_at=started_at,
                status="error",
                error_code=ErrorCode.ODOO_UNAVAILABLE,
            )
            return UnresolvedResult(
                error_code=ErrorCode.ODOO_UNAVAILABLE,
                message="The procurement source returned an invalid response.",
                retryable=True,
            )
        self._record_mcp_completion(
            environment=environment,
            started_at=started_at,
            status="success",
        )
        candidates = tuple(
            candidate
            for candidate in page.candidates
            if candidate.skip_reason_code is None
        )
        if not candidates:
            return UnresolvedResult(
                error_code=ErrorCode.NO_VALID_OFFER,
                message="No approval-ready replenishment candidate was found.",
                retryable=False,
            )
        return candidates
```

Note: `_record_mcp_completion`'s current signature takes `state: ScanState`
(used only to read `state["environment"]` and `state["scan_id"]` for
logging/metrics tags — verify its exact body before this edit). Change its
signature to accept `environment: Environment` directly instead of `state`,
and update its two remaining call sites in `gather_evidence` (Step 5 below)
and `reason_about_candidate`'s `_record_llm_completion` sibling method
similarly if it has the same `state`-based signature. Read the method
bodies first; if they also log `state["scan_id"]`, thread a `scan_id: str`
parameter through instead of removing that observability field.

- [ ] **Step 5: Restructure `gather_evidence` for one candidate**

Replace the `gather_evidence` method (currently lines 127-211) with:

```python
    async def gather_evidence(self, state: ScanState) -> dict[str, object]:
        """Gather authoritative evidence for this case's one candidate."""

        if "result" in state:
            return {}
        candidate = state["candidates"][0]
        started_at = perf_counter()
        try:
            item = await self.mcp.get_procurement_evidence(
                environment=state["environment"],
                product_id=candidate.product_id,
                horizon_days=14,
            )
        except McpReadError as error:
            error_code = (
                ErrorCode.MCP_TIMEOUT
                if isinstance(error, McpTimeoutError)
                else ErrorCode.ODOO_UNAVAILABLE
            )
            self._record_mcp_completion(
                environment=state["environment"],
                started_at=started_at,
                status="error",
                error_code=error_code,
                retry_count=error.retry_count,
                tool_name="get_procurement_evidence",
            )
            return {
                "result": UnresolvedResult(
                    error_code=error_code,
                    message=error.safe_message,
                    retryable=True,
                    retry_count=error.retry_count,
                )
            }
        except Exception:
            self._record_mcp_completion(
                environment=state["environment"],
                started_at=started_at,
                status="error",
                error_code=ErrorCode.ODOO_UNAVAILABLE,
                tool_name="get_procurement_evidence",
            )
            return {
                "result": UnresolvedResult(
                    error_code=ErrorCode.ODOO_UNAVAILABLE,
                    message="The procurement source returned invalid evidence.",
                    retryable=True,
                )
            }
        self._record_mcp_completion(
            environment=state["environment"],
            started_at=started_at,
            status="success",
            tool_name="get_procurement_evidence",
        )

        if item.skip_reason_code in ("NO_SHORTAGE", "FULLY_COVERED"):
            return {"evidence": (item,), "skip_reason": item.skip_reason_code}
        if item.skip_reason_code == "NO_VALID_OFFER":
            return {
                "evidence": (item,),
                "result": NoValidOfferResult(
                    product_id=candidate.product_id,
                    product_name=candidate.product_name,
                    rationale=(
                        "No approved vendor offer is eligible for this product."
                    ),
                ),
            }
        if item.skip_reason_code == "BUDGET_UNAVAILABLE":
            return {
                "evidence": (item,),
                "result": UnresolvedResult(
                    error_code=ErrorCode.ODOO_UNAVAILABLE,
                    message="Budget information is unavailable for this product.",
                    retryable=True,
                ),
            }
        return {"evidence": (item,), "candidates": (candidate,)}
```

Add `NoValidOfferResult` to the `from procurement.agent.state import (...)`
block at the top of the file.

- [ ] **Step 6: Update `reason_about_candidate`'s guard**

In `reason_about_candidate` (currently lines 213-217), change:

```python
        if "result" in state:
            return {}
```

to:

```python
        if "result" in state or state.get("skip_reason") is not None:
            return {}
```

The rest of `reason_about_candidate` (lines 218-380) is unchanged — it
already only reads `state["candidates"]` and `state["evidence"]`, both of
which are now single-item tuples rather than multi-item ones, and
`RecommendationRequest` already accepts as few as one candidate
(`ports/llm.py:67`).

- [ ] **Step 7: Update the graph's entry point**

In `src/procurement/agent/graph.py`, remove the `discover_candidates` node
and its edge from `START`. Change:

```python
builder.add_node("discover_candidates", nodes.discover_candidates)
builder.add_node("gather_evidence", nodes.gather_evidence)
builder.add_node("resolve_preferences", nodes.resolve_preferences)
builder.add_node("reason", nodes.reason_about_candidate)
builder.add_edge(START, "discover_candidates")
builder.add_edge("discover_candidates", "gather_evidence")
builder.add_edge("gather_evidence", "resolve_preferences")
builder.add_edge("resolve_preferences", "reason")
builder.add_edge("reason", END)
```

to:

```python
builder.add_node("gather_evidence", nodes.gather_evidence)
builder.add_node("resolve_preferences", nodes.resolve_preferences)
builder.add_node("reason", nodes.reason_about_candidate)
builder.add_edge(START, "gather_evidence")
builder.add_edge("gather_evidence", "resolve_preferences")
builder.add_edge("resolve_preferences", "reason")
builder.add_edge("reason", END)
```

Check `resolve_preferences` (currently `walking_skeleton.py:395-471`) for
any assumption that `state["candidates"]`/`state["evidence"]` has more than
one item (e.g. iterating and building a tuple of preferences per candidate)
— if so, it already works correctly for a 1-tuple with no change needed,
since it's iterating either way; only confirm it does not special-case
`len(candidates) > 1`.

- [ ] **Step 8: Run tests to verify they pass**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/agent/test_walking_skeleton.py -v
```

Expected: PASS — the four new tests plus every pre-existing test in this
file, which will need their own call-site updates if they invoke
`nodes.discover_candidates(state)` directly or construct a graph and feed it
multiple candidates expecting one arbitrated winner. Update any such
existing test to either call the new `discover_candidates(environment=...)`
signature, or seed the graph with a single candidate and assert on that one
candidate's result, matching the new per-candidate contract. Read each
failure's assertion before changing it — do not weaken an assertion just to
make it pass; if a pre-existing test asserted cross-candidate arbitration
behavior that no longer exists, replace it with an equivalent single-candidate
assertion that preserves the original test's intent (e.g. "the model's
declined-decision path still produces ManualReviewResult" stays meaningful
per-candidate).

- [ ] **Step 9: Run focused quality checks**

```bash
cd /home/weam/StockAI && uv run ruff check src/procurement/agent tests/unit/agent
uv run mypy src/procurement/agent
```

Expected: both pass.

- [ ] **Step 10: Commit**

```bash
git add src/procurement/agent/state.py src/procurement/agent/nodes/walking_skeleton.py \
  src/procurement/agent/graph.py tests/unit/agent/test_walking_skeleton.py
git commit -m "refactor(agent): evaluate one candidate per graph invocation"
```

---

## Task 3: `ScanRecord` persistence and per-product `CaseId`

**Files:**
- Modify: `src/procurement/ports/repositories.py`
- Modify: `src/procurement/adapters/aws/dynamodb.py`
- Modify: `tests/unit/adapters/test_dynamodb.py` (or wherever the existing
  DynamoDB adapter unit tests live — confirm exact path before editing;
  `tests/unit/adapters/` is the expected location per the repo's test
  directory naming, matching `tests/unit/agent`, `tests/unit/api`)
- Modify: `tests/unit/repositories` or equivalent for
  `InMemoryApplicationRepository` tests — confirm exact existing path

**Interfaces:**
- Produces: `ScanRecord(scan_id: str, status: str, trigger: str, created_at:
  UtcTimestamp, started_at: UtcTimestamp | None, completed_at: UtcTimestamp
  | None, case_summaries: tuple[CaseSummary, ...])` and `CaseSummary
  (case_id: str, product_id: str, product_name: str, outcome: str,
  amount: Decimal | None, need_by_date: date | None)` — enough for the
  results table without fetching every full `CaseRecord`.
- `CaseId` values become `f"{scan_id}:{product_id}"` instead of
  `== scan_id`.
- `ApplicationRepository` Protocol gains `create_scan`, `update_scan`,
  `get_scan`, `append_case_summary` (or equivalent — confirm exact naming
  against the Protocol's existing verb conventions, `create_case`/
  `update_case`/`get_case`/`list_cases`, before finalizing).

- [ ] **Step 1: Write the failing tests**

First locate the exact existing test files for `InMemoryApplicationRepository`
and the DynamoDB adapter:

```bash
cd /home/weam/StockAI && grep -rl "InMemoryApplicationRepository\|class.*DynamoDB.*Repository" tests/unit/
```

Add tests to whichever files that search reveals (following their existing
fixture/assertion patterns), covering:

```python
async def test_scan_record_aggregates_case_summaries() -> None:
    repository = InMemoryApplicationRepository()
    scan = await repository.create_scan(
        scan_id="scan-1", trigger="manual", environment=Environment.DEV
    )
    assert scan.status == "running"
    assert scan.case_summaries == ()

    await repository.append_case_summary(
        scan_id="scan-1",
        summary=CaseSummary(
            case_id="scan-1:product-1",
            product_id="product-1",
            product_name="Fictional Widget",
            outcome="approval_ready",
            amount=Decimal("120.50"),
            need_by_date=date(2026, 8, 20),
        ),
    )
    updated = await repository.get_scan("scan-1")
    assert len(updated.case_summaries) == 1
    assert updated.case_summaries[0].product_id == "product-1"


async def test_case_id_is_scoped_to_scan_and_product() -> None:
    repository = InMemoryApplicationRepository()
    case = await repository.create_case(
        case_id=CaseId(value="scan-1:product-1"),
        trigger="manual",
        environment=Environment.DEV,
    )
    assert case.case_id.value == "scan-1:product-1"
```

Adapt these to the Protocol's actual existing method signatures (e.g.
`create_case` may already take different parameters — match its current
shape exactly rather than inventing a new one) once Step 1's grep locates
the real current test file and its imports.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/ -k "scan_record_aggregates or case_id_is_scoped" -v
```

Expected: FAIL — `create_scan`/`append_case_summary`/`get_scan`/
`CaseSummary`/`ScanRecord` do not exist yet.

- [ ] **Step 3: Add `ScanRecord`/`CaseSummary` to `ports/repositories.py`**

Insert after the `CaseRecord` class (currently `repositories.py:68-82`):

```python
@dataclass(frozen=True, slots=True)
class CaseSummary:
    """Enough of one case's result to render a scan's results table."""

    case_id: str
    product_id: str
    product_name: str
    outcome: str
    amount: Decimal | None
    need_by_date: date | None


@dataclass(frozen=True, slots=True)
class ScanRecord:
    """Aggregates the cases spawned by one scan run."""

    scan_id: str
    status: str
    trigger: str
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    started_at: UtcTimestamp | None = None
    completed_at: UtcTimestamp | None = None
    case_summaries: tuple[CaseSummary, ...] = ()
```

Add `date` to the `from datetime import ...` import if not already present.

Extend the `ApplicationRepository` Protocol (currently lines 135-195) with:

```python
    async def create_scan(
        self, *, scan_id: str, trigger: str, environment: Environment
    ) -> ScanRecord: ...

    async def append_case_summary(
        self, *, scan_id: str, summary: CaseSummary
    ) -> ScanRecord: ...

    async def update_scan(
        self, *, scan_id: str, status: str, completed_at: UtcTimestamp | None = None
    ) -> ScanRecord: ...

    async def get_scan(self, scan_id: str) -> ScanRecord: ...
```

- [ ] **Step 4: Implement on `InMemoryApplicationRepository`**

Add a `self._scans: dict[str, ScanRecord] = {}` field alongside the existing
case dict in `__init__`, and implement the four new Protocol methods
following the exact style of the existing `create_case`/`update_case`
methods in that class (conditional creation, `updated_at` bump, raising the
same not-found exception type the existing `get_case` raises for a missing
key — match it exactly rather than inventing a new exception type).

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/ -k "scan_record_aggregates or case_id_is_scoped" -v
```

Expected: PASS.

- [ ] **Step 6: Extend the DynamoDB adapter**

In `src/procurement/adapters/aws/dynamodb.py`, add a new SK scheme for scan
aggregate items: `f"SCAN#{scan_id}"` (following the exact naming pattern of
the existing `_case_key` at lines 655-659, `f"CASE#{case_id.value}"`). Case
items already key by `case_id.value`, which under the new `{scan_id}:
{product_id}` scheme naturally sorts/queries together per scan via
`begins_with(SK, :case_prefix)` with `case_prefix = f"CASE#{scan_id}:"` —
confirm `list_cases` (currently `dynamodb.py:217-222`) can be parameterized
this way (add a `scan_id: str | None = None` filter parameter that narrows
the `begins_with` prefix when provided) rather than needing a wholly new
query path. Add `create_scan`/`append_case_summary`/`update_scan`/`get_scan`
implementations writing/reading the new `SCAN#{scan_id}` item, following the
same conditional-write and item-shape conventions as the adapter's existing
case methods (read the surrounding 50 lines around `_case_key` for the exact
`put_item`/`ConditionExpression` pattern to mirror before writing these).

- [ ] **Step 7: Write and pass DynamoDB adapter tests**

Add adapter-level tests (using DynamoDB Local, matching the existing
adapter test file's fixture setup — confirm its exact table-creation fixture
before adding to it) mirroring Step 1's two tests but against the real
adapter instead of the in-memory fake, plus one confirming
`list_cases(scan_id="scan-1")` returns only that scan's cases.

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/adapters -k dynamodb -v
```

Expected: PASS (requires DynamoDB Local running per the existing test
file's documented setup — check its header/fixture for how it's started,
matching whatever the pre-existing DynamoDB adapter tests already require).

- [ ] **Step 8: Run focused quality checks**

```bash
cd /home/weam/StockAI && uv run ruff check src/procurement/ports/repositories.py src/procurement/adapters/aws/dynamodb.py
uv run mypy src/procurement/ports/repositories.py src/procurement/adapters/aws/dynamodb.py
```

Expected: both pass.

- [ ] **Step 9: Commit**

```bash
git add src/procurement/ports/repositories.py src/procurement/adapters/aws/dynamodb.py \
  tests/unit/
git commit -m "feat(persistence): add ScanRecord aggregating per-product cases"
```

---

## Task 4: Restructure `ScanService` orchestration

**Files:**
- Modify: `src/procurement/api/services/scans.py`
- Modify: `tests/unit/api/test_scan_service.py` (confirm exact existing
  path — `tests/unit/api/test_scans.py` covers the route layer per prior
  research; the service layer's own unit tests, if separate from the route
  tests, need locating first)

**Interfaces:**
- Consumes: `nodes.discover_candidates(environment=...)` (Task 2),
  `repository.create_scan`/`append_case_summary`/`update_scan`/`get_scan`
  (Task 3).
- Produces: `ScanService.start_scan` now creates one `ScanRecord` and N
  `CaseRecord`s (one per discovered candidate) instead of one `CaseRecord`.
  `ScanService.get_scan` returns the scan aggregate; a new
  `ScanService.get_case(scan_id, case_id)` returns one case's full detail.

- [ ] **Step 1: Locate the existing service-level tests**

```bash
cd /home/weam/StockAI && grep -rl "ScanService(" tests/unit/
```

Read whichever file(s) that finds in full before writing new tests, to
match its exact fixture/workflow-fake conventions (likely reusing the
`SuccessfulWorkflow`/similar fakes seen in `tests/unit/api/test_scans.py`'s
research, or a service-level equivalent).

- [ ] **Step 2: Write the failing tests**

Following that file's conventions, add tests asserting:
- `start_scan` calls the (now directly-callable) discovery step once, then
  invokes the workflow once per discovered candidate with
  `thread_id=f"{scan_id}:{candidate.product_id}"`.
- Each candidate's graph outcome is persisted as its own `CaseRecord`, and
  summarized into the scan's `ScanRecord.case_summaries`.
- A candidate whose graph run set `skip_reason` (Task 2) produces no case
  and no summary row at all.
- One candidate's case-level failure (graph run raises, or times out) does
  not prevent the remaining candidates from being evaluated and persisted;
  the scan's own `status` stays `succeeded` as long as discovery itself
  succeeded.
- Zero discovered candidates → scan `status: succeeded` with an empty
  `case_summaries` tuple (not a scan-level failure — this is a real,
  necessary behavior change from today's "zero candidates -> whole-scan
  NO_VALID_OFFER failure," a direct consequence of no candidate outcome
  ever being allowed to fail the scan itself).

Write these using the exact fake-workflow injection pattern the located
file already uses — do not invent a new one.

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/weam/StockAI && uv run pytest <located test file> -v -k "one_case_per_candidate or skip_reason_produces_no_case or one_failure_does_not_block_siblings or zero_candidates"
```

Expected: FAIL against today's single-invocation `_run_scan`.

- [ ] **Step 4: Restructure `_run_scan`**

Read `services/scans.py:220-330` in full first (the exact current
`_run_scan` body plus `_apply_result`, both of which need restructuring —
the prior research summarized their line ranges and general shape but not
every line; re-read before editing to preserve every existing metrics/log
call and timeout-handling branch, only changing the single-invocation loop
into a per-candidate loop). The core shape:

```python
async def _run_scan(self, scan_id: str, environment: Environment) -> None:
    scan = await self._repository.update_scan(scan_id=scan_id, status=ScanStatus.RUNNING)
    discovery = await self._nodes.discover_candidates(environment=environment)
    if isinstance(discovery, UnresolvedResult):
        # scan-level failure: discovery itself could not run at all
        await self._repository.update_scan(
            scan_id=scan_id, status=ScanStatus.FAILED, completed_at=now(),
        )
        # existing scan-level failure persistence path, adapted to ScanRecord
        return

    for candidate in discovery:
        case_id = f"{scan_id}:{candidate.product_id}"
        await self._repository.create_case(
            case_id=CaseId(value=case_id), trigger=..., environment=environment,
        )
        try:
            async with asyncio.timeout(self._workflow_timeout_seconds):
                state = await self._workflow.ainvoke(
                    {
                        "scan_id": scan_id,
                        "environment": environment,
                        "candidates": (candidate,),
                    },
                    config={"configurable": {"thread_id": case_id}},
                )
        except TimeoutError:
            await self._apply_case_failure(case_id, ...)  # isolated to this case
            continue
        if state.get("skip_reason") is not None:
            continue  # no case summary — this candidate needed no action
        await self._apply_result(case_id=case_id, state=state)
        await self._repository.append_case_summary(
            scan_id=scan_id, summary=self._summarize(candidate, state["result"]),
        )

    await self._repository.update_scan(
        scan_id=scan_id, status=ScanStatus.SUCCEEDED, completed_at=now(),
    )
```

This is a shape sketch, not literal final code — reconcile it against the
exact existing `_run_scan`/`_apply_result` bodies you read in this step
(preserve their real metrics/logging calls, exception types, and the
existing `self._active_scan_id` single-active-scan guard, which stays
scan-level and unchanged). Add a small `_summarize(candidate, result) ->
CaseSummary` helper matching each `ScanResult` variant to the amount/
need-by fields `CaseSummary` needs (e.g. `ApprovalReadyResult.normalized_cost`
for `amount`, `None` for `NoValidOfferResult`/`ManualReviewResult`).

- [ ] **Step 5: Add `get_case`**

```python
async def get_case(self, scan_id: str, case_id: str) -> CaseSnapshot:
    record = await self._repository.get_case(CaseId(value=case_id))
    return self._case_snapshot(record)  # reuse/rename existing _snapshot logic
```

Rename the existing `_snapshot` method (currently `services/scans.py:452-563`,
`CaseRecord` → `ScanSnapshot`) to `_case_snapshot` returning a
`CaseSnapshot` (identical shape to today's `ScanSnapshot`, just renamed to
reflect that it now describes one case, not a whole scan) — update its
call sites accordingly. Add a new, much smaller `_scan_snapshot(record:
ScanRecord) -> ScanAggregateSnapshot` for the scan-aggregate shape.

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /home/weam/StockAI && uv run pytest <located test file> -v
```

Expected: PASS — the new tests plus every pre-existing test in this file,
updated for the renamed/restructured methods where they touched them
directly.

- [ ] **Step 7: Run focused quality checks**

```bash
cd /home/weam/StockAI && uv run ruff check src/procurement/api/services/scans.py
uv run mypy src/procurement/api/services/scans.py
```

Expected: both pass.

- [ ] **Step 8: Commit**

```bash
git add src/procurement/api/services/scans.py tests/unit/
git commit -m "refactor(api): orchestrate one independent case per candidate"
```

---

## Task 5: New API response shapes

**Files:**
- Modify: `src/procurement/api/routes/scans.py`
- Modify: `tests/unit/api/test_scans.py`

**Interfaces:**
- `GET /api/v1/scans/{scan_id}` → new `ScanAggregateResponse` (status,
  trigger, timestamps, `results: tuple[CaseSummaryResponse, ...]`, and
  outcome counts for the donut).
- New `GET /api/v1/scans/{scan_id}/cases/{case_id}` → today's exact
  `ScanResponse` shape (renamed `CaseResponse` for clarity, fields
  unchanged), so `RecommendationPage`'s existing fetch/parse code needs
  only a URL change, not a shape change.
- `POST /api/v1/scans` keeps its 202-Accepted shape but the body becomes
  `ScanAggregateResponse` instead of the old single-case `ScanResponse`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/api/test_scans.py`, following the exact `SuccessfulWorkflow`/
`sign_in`/`ASGITransport` pattern from the existing tests (research above
has a full representative example), add:

```python
@pytest.mark.anyio
async def test_scan_aggregate_lists_every_case_result() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=3)  # new fake, see Step 3
    application = create_app(scan_workflow=workflow, identity_provider=LocalIdentityProvider())
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
    assert len(finished.json()["results"]) == 3
    assert set(row["outcome"] for row in finished.json()["results"]) <= {
        "approval_ready", "manual_review", "no_valid_offer",
    }


@pytest.mark.anyio
async def test_case_detail_route_returns_one_case_full_shape() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=2)
    application = create_app(scan_workflow=workflow, identity_provider=LocalIdentityProvider())
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        csrf_headers = await sign_in(client)
        accepted = await client.post("/api/v1/scans", headers=csrf_headers)
        scan_id = accepted.json()["scan_id"]
        finished = await _poll_until_finished(client, scan_id)
        case_id = finished.json()["results"][0]["case_id"]
        case = await client.get(f"/api/v1/scans/{scan_id}/cases/{case_id}")
    assert case.status_code == 200
    assert "evidence" in case.json()
    assert "result" in case.json()
```

`_poll_until_finished` needs its return-shape assertions (currently checking
for a single `result`/`error` key per the existing helper at
`test_scans.py:104-114`) updated to poll on the new aggregate's `status`
field instead — read its current body before changing it, and update every
existing call site in this file consistently rather than forking a second
polling helper.

- [ ] **Step 2: Add a `MultiCandidateWorkflow` test fake**

Add to the same test file, next to the existing `SuccessfulWorkflow` fake,
matching its exact style (implementing `ScanWorkflow`'s `ainvoke` Protocol
method):

```python
@dataclass
class MultiCandidateWorkflow:
    candidate_count: int
    configs: list[Mapping[str, object]] = field(default_factory=list)

    async def ainvoke(
        self, state: ScanState, *, config: Mapping[str, object]
    ) -> ScanState:
        self.configs.append(config)
        # Return a distinct ApprovalReadyResult per thread_id so each
        # invocation produces a different, identifiable case.
        index = len(self.configs)
        return {
            **state,
            "result": ApprovalReadyResult(
                product_id=f"product-{index}",
                product_name=f"Fictional Product {index}",
                ...  # remaining required fields — mirror t27_approval_result()'s
                     # shape from tests/support/recommendations.py exactly
            ),
        }
```

Match every required field of `ApprovalReadyResult` (Task 1's research has
the full field list) using `tests/support/recommendations.py`'s existing
`t27_approval_result()` helper as the base, varying only `product_id`/
`product_name` per call — do not hand-roll the field list from scratch when
a working helper already exists.

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/api/test_scans.py -v -k "scan_aggregate_lists_every_case or case_detail_route_returns"
```

Expected: FAIL — the new route and response shape don't exist yet.

- [ ] **Step 4: Add the new response models and routes**

In `src/procurement/api/routes/scans.py`, add alongside the existing
`ApprovalReadyResponse`/`ManualReviewResponse`/`ScanResponse` (currently
lines 31-120):

```python
class NoValidOfferResponse(BaseModel):
    outcome: Literal["no_valid_offer"]
    product_id: str
    product_name: str
    rationale: str
    evidence_limitations: tuple[str, ...]
    read_only: Literal[True]


class ConfirmedResponse(BaseModel):
    """Placeholder shape — no code path in this repository produces this
    outcome yet. Exists so T29's future confirmation work only needs to
    start returning it, with no API contract change required then."""

    outcome: Literal["confirmed"]
    product_id: str
    product_name: str
    po_reference: str
    po_amount: str
    read_only: Literal[True]


class CaseSummaryResponse(BaseModel):
    case_id: str
    product_id: str
    product_name: str
    outcome: str
    amount: str | None
    need_by_date: str | None


class ScanAggregateResponse(BaseModel):
    scan_id: str
    status: str
    trigger: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    results: tuple[CaseSummaryResponse, ...]
    outcome_counts: dict[str, int]


CaseResponse = ScanResponse  # today's exact shape, renamed for one-case use
```

Rename `scan_response` (currently `scans.py:138-206`) to `case_response`
and update it to also handle `NoValidOfferResult` → `NoValidOfferResponse`
(new branch) — `ConfirmedResult` never needs a branch here since nothing
produces it, but the response model exists for the frontend/OpenAPI
contract per the spec's placeholder decision.

Add a new `scan_aggregate_response(record: ScanAggregateSnapshot) ->
ScanAggregateResponse` function computing `outcome_counts` by tallying
`results` by `outcome`.

Update the three route handlers (currently lines 209-238):

```python
@router.post("/api/v1/scans", status_code=202)
async def create_manual_scan(request: Request, response: Response) -> ScanAggregateResponse:
    service: ScanService = request.app.state.scan_service
    snapshot = await service.start_scan(trigger=ScanTrigger.MANUAL)
    response.headers["Location"] = f"/api/v1/scans/{snapshot.scan_id}"
    return scan_aggregate_response(snapshot)


@router.get("/api/v1/scans/{scan_id}")
async def get_scan(scan_id: str, request: Request) -> ScanAggregateResponse:
    service: ScanService = request.app.state.scan_service
    snapshot = await service.get_scan(scan_id)
    return scan_aggregate_response(snapshot)


@router.get("/api/v1/scans/{scan_id}/cases/{case_id}")
async def get_case(scan_id: str, case_id: str, request: Request) -> CaseResponse:
    service: ScanService = request.app.state.scan_service
    snapshot = await service.get_case(scan_id, case_id)
    return case_response(snapshot)
```

Preserve every existing decorator/dependency (auth, CSRF) from the current
handlers — the research summary omitted decorators for brevity; read the
actual current lines 209-238 before this edit to carry them over exactly.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/api/test_scans.py -v
```

Expected: PASS — new tests plus every pre-existing test in the file,
updated for the new response shape where they asserted on the old one.

- [ ] **Step 6: Run focused quality checks**

```bash
cd /home/weam/StockAI && uv run ruff check src/procurement/api/routes/scans.py tests/unit/api/test_scans.py
uv run mypy src/procurement/api/routes/scans.py
```

Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add src/procurement/api/routes/scans.py tests/unit/api/test_scans.py
git commit -m "feat(api): add scan-aggregate and case-detail response shapes"
```

---

## Task 6: `make check` full backend verification

**Files:** none (verification-only task, no code changes)

- [ ] **Step 1: Run the complete backend quality gate**

```bash
cd /home/weam/StockAI && make check
```

Expected: PASS — lock/format checks, Ruff, strict mypy, architecture tests,
and the full unit suite, confirming Tasks 1-5's changes compose correctly
across the whole backend (e.g. any other call site referencing the old
`ScanResponse`/single-case `ScanState` shape that individual task-level
focused checks didn't happen to touch).

- [ ] **Step 2: Run integration tests**

```bash
cd /home/weam/StockAI && make test-integration
```

Expected: PASS — real MCP transport tests exercising the new per-candidate
orchestration against the fake Odoo scenarios.

- [ ] **Step 3: Fix any cross-cutting failures found**

If `make check` or `make test-integration` surfaces a failure outside
Tasks 1-5's directly-touched files (e.g. a persistence-layer consumer in
`mcp_server/` or `bootstrap/` still assuming one case per scan), fix it here
with its own failing-test-first cycle, matching that file's existing test
conventions, then re-run Steps 1-2 until both pass.

- [ ] **Step 4: Commit any fixes from Step 3**

```bash
git add -A
git commit -m "fix: reconcile remaining single-case assumptions after T27C orchestration change"
```

(Skip this step entirely if Step 3 found nothing to fix.)

---

## Task 7: Frontend types — `client.ts`

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/tests/api-client.test.ts`

**Interfaces:**
- `Scan` splits into `ScanAggregate` (scan-level: status, trigger,
  timestamps, `results: CaseSummary[]`, `outcomeCounts`) and `CaseDetail`
  (today's exact `Scan` shape, renamed).
- `ScanResult` gains `NoValidOfferResult` and `ConfirmedResult` (placeholder)
  variants, matching Task 5's new response shapes exactly.
- New functions: `getScanAggregate(scanId)`, `getCase(scanId, caseId)`,
  replacing `getScan(scanId)`. `createManualScan()` and `listScans()` keep
  their names but their return type becomes `ScanAggregate`.

- [ ] **Step 1: Write the failing tests**

In `frontend/tests/api-client.test.ts`, following its existing fixture/
`jsonResponse` helper conventions (matching the pattern already used in
`frontend/tests/recommendation.test.tsx`'s `BASE_SCAN`/`jsonResponse`), add:

```ts
it("parses a scan aggregate with mixed-outcome case summaries", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      jsonResponse({
        scan_id: "scan-1",
        status: "succeeded",
        trigger: "manual",
        created_at: "2026-08-18T10:00:00Z",
        started_at: "2026-08-18T10:00:01Z",
        completed_at: "2026-08-18T10:00:40Z",
        results: [
          {
            case_id: "scan-1:product-1",
            product_id: "product-1",
            product_name: "Fictional Widget",
            outcome: "approval_ready",
            amount: "120.500000",
            need_by_date: "2026-08-20",
          },
          {
            case_id: "scan-1:product-2",
            product_id: "product-2",
            product_name: "Fictional Gadget",
            outcome: "no_valid_offer",
            amount: null,
            need_by_date: "2026-08-19",
          },
        ],
        outcome_counts: { approval_ready: 1, no_valid_offer: 1 },
      }),
    ),
  );
  const aggregate = await getScanAggregate("scan-1");
  expect(aggregate.results).toHaveLength(2);
  expect(aggregate.results[1].outcome).toBe("no_valid_offer");
  expect(aggregate.outcomeCounts.no_valid_offer).toBe(1);
});

it("parses a case detail with a no_valid_offer result", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      jsonResponse({
        ...BASE_CASE, // adapt from BASE_SCAN in recommendation.test.tsx, minus scan-aggregate-only fields
        result: {
          outcome: "no_valid_offer",
          product_id: "product-2",
          product_name: "Fictional Gadget",
          rationale: "No approved vendor offer is eligible for this product.",
          evidence_limitations: [],
          read_only: true,
        },
      }),
    ),
  );
  const detail = await getCase("scan-1", "scan-1:product-2");
  expect(detail.result?.outcome).toBe("no_valid_offer");
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && npx vitest run tests/api-client.test.ts -t "scan aggregate or no_valid_offer"
```

Expected: FAIL — `getScanAggregate`/`getCase`/`NoValidOfferResult` parsing
don't exist yet.

- [ ] **Step 3: Add the new types and parsers**

In `frontend/src/api/client.ts`, add alongside the existing
`ApprovalReadyResult`/`LegacyApprovalReadyResult`/`ManualReviewResult`
interfaces (currently lines 93-144):

```ts
export interface NoValidOfferResult {
  outcome: "no_valid_offer";
  product_id: string;
  product_name: string;
  rationale: string;
  evidence_limitations: string[];
  read_only: true;
}

export interface ConfirmedResult {
  outcome: "confirmed";
  product_id: string;
  product_name: string;
  po_reference: string;
  po_amount: string;
  read_only: true;
}

export type ScanResult =
  | ApprovalReadyResult
  | LegacyApprovalReadyResult
  | ManualReviewResult
  | NoValidOfferResult
  | ConfirmedResult;

export interface CaseSummary {
  case_id: string;
  product_id: string;
  product_name: string;
  outcome: string;
  amount: string | null;
  need_by_date: string | null;
}

export interface ScanAggregate {
  scan_id: string;
  status: ScanStatus;
  trigger: ScanTrigger;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  results: CaseSummary[];
  outcomeCounts: Record<string, number>;
}

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
}
```

Rename the existing `Scan` interface/type usages to `CaseDetail` throughout
the file (the `parseScan` function becomes `parseCaseDetail`, its callers
`getScan`/`listScans`/`createManualScan` are restructured in Step 4).

Extend `parseResult` (currently lines 216-312) with two new branches
matching `NoValidOfferResult` (`outcome === "no_valid_offer"`) and
`ConfirmedResult` (`outcome === "confirmed"`) following the exact
validation-then-construct style already used for the `manual_review` branch
(lines 231-241) — strict field presence/type checks calling
`invalidResponse()` on any mismatch, matching every other branch's rigor.

Add `parseCaseSummary` and `parseScanAggregate` functions following
`parseEvidence`'s existing validation style (strict `isRecord`/field-type
checks, `invalidResponse()` on mismatch — do not relax validation rigor for
the new types relative to the existing ones).

- [ ] **Step 4: Update the fetch functions**

Replace `getScan` (currently lines 683-692) with:

```ts
export async function getScanAggregate(
  scanId: string,
  options: RequestOptions = {},
): Promise<ScanAggregate> {
  const response = await request(`${SCANS_PATH}/${encodeURIComponent(scanId)}`, {
    method: "GET",
    signal: options.signal,
  });
  return parseScanAggregate(response.body);
}

export async function getCase(
  scanId: string,
  caseId: string,
  options: RequestOptions = {},
): Promise<CaseDetail> {
  const response = await request(
    `${SCANS_PATH}/${encodeURIComponent(scanId)}/cases/${encodeURIComponent(caseId)}`,
    { method: "GET", signal: options.signal },
  );
  return parseCaseDetail(response.body);
}
```

Update `createManualScan` (lines 651-664) and `listScans` (666-681) to
return `ScanAggregate` / `ScanAggregate[]` respectively, calling
`parseScanAggregate` instead of `parseScan`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd frontend && npx vitest run tests/api-client.test.ts
```

Expected: PASS.

- [ ] **Step 6: Run full frontend verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: all fail at this point with real compile errors in every file
that still imports `Scan`/`getScan` (`RecommendationPage.tsx`,
`OverviewPage.tsx`, `App.tsx`) — this is expected and resolved by Tasks 8-9,
which consume these new types. Confirm the failures are exactly those three
files' now-stale imports, not a mistake in this task's own new code — read
each reported error to verify.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/tests/api-client.test.ts
git commit -m "feat(frontend): split client.ts types into scan aggregate and case detail"
```

---

## Task 8: New `ScanDetailPage`

**Files:**
- Create: `frontend/src/pages/ScanDetailPage.tsx`
- Create: `frontend/tests/scan-detail.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Produces: `ScanDetailPage({ scanId, onBack, onSelectCase }):
  JSX.Element` — `scanId: string`, `onBack: () => void`, `onSelectCase:
  (caseId: string) => void`. Fetches via `getScanAggregate`, renders the
  results table and outcome-breakdown donut matching `Scan_details.png`.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/scan-detail.test.tsx`, following
`recommendation.test.tsx`'s exact `vi.stubGlobal("fetch", ...)` +
`jsonResponse` pattern:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScanDetailPage } from "../src/pages/ScanDetailPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const AGGREGATE = {
  scan_id: "scan-4278",
  status: "succeeded",
  trigger: "manual",
  created_at: "2026-08-18T14:40:00Z",
  started_at: "2026-08-18T14:40:01Z",
  completed_at: "2026-08-18T14:41:00Z",
  results: [
    {
      case_id: "scan-4278:product-1",
      product_id: "product-1",
      product_name: "PROD Fictional Happy-Path Component",
      outcome: "approval_ready",
      amount: "1080.000000",
      need_by_date: "2026-08-18",
    },
    {
      case_id: "scan-4278:product-2",
      product_id: "product-2",
      product_name: "PROD Fictional No-Valid-Offer Component",
      outcome: "no_valid_offer",
      amount: null,
      need_by_date: "2026-08-18",
    },
  ],
  outcome_counts: { approval_ready: 1, no_valid_offer: 1 },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ScanDetailPage", () => {
  it("shows a results row and outcome count per case", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(AGGREGATE)));

    render(<ScanDetailPage scanId="scan-4278" onBack={vi.fn()} onSelectCase={vi.fn()} />);

    expect(
      await screen.findByText("PROD Fictional Happy-Path Component"),
    ).toBeInTheDocument();
    expect(screen.getByText("PROD Fictional No-Valid-Offer Component")).toBeInTheDocument();
    expect(screen.getByText("No valid offer")).toBeInTheDocument();
    const donut = screen.getByRole("img", { name: /outcome breakdown/i });
    expect(donut).toBeInTheDocument();
  });

  it("calls onSelectCase with the row's case id when clicked", async () => {
    const user = userEvent.setup();
    const onSelectCase = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(AGGREGATE)));

    render(<ScanDetailPage scanId="scan-4278" onBack={vi.fn()} onSelectCase={onSelectCase} />);

    await user.click(
      await screen.findByRole("button", {
        name: /view recommendation.*happy-path/i,
      }),
    );
    expect(onSelectCase).toHaveBeenCalledWith("scan-4278:product-1");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run tests/scan-detail.test.tsx
```

Expected: FAIL — `ScanDetailPage` does not exist.

- [ ] **Step 3: Create `ScanDetailPage.tsx`**

```tsx
import { useEffect, useState } from "react";

import { ApiError, getScanAggregate, isAbortError, type ScanAggregate } from "../api/client";
import { formatCurrency, formatDate, formatDateTime } from "../presentation";

const OUTCOME_LABEL: Record<string, string> = {
  approval_ready: "Approval ready",
  manual_review: "Manual review",
  no_valid_offer: "No valid offer",
  confirmed: "Confirmed",
};

const OUTCOME_COLOR: Record<string, string> = {
  approval_ready: "#2f9e58",
  manual_review: "#3157c8",
  no_valid_offer: "#c0392b",
  confirmed: "#2f9e58",
};

function OutcomeDonut({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, count]) => count > 0);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  if (total === 0) {
    return null;
  }
  let cumulative = 0;
  const radius = 60;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className="outcome-donut">
      <svg
        role="img"
        aria-label={`Outcome breakdown: ${entries
          .map(([outcome, count]) => `${count} ${OUTCOME_LABEL[outcome] ?? outcome}`)
          .join(", ")}`}
        viewBox="0 0 140 140"
      >
        {entries.map(([outcome, count]) => {
          const fraction = count / total;
          const dashArray = `${fraction * circumference} ${circumference}`;
          const dashOffset = -cumulative * circumference;
          cumulative += fraction;
          return (
            <circle
              key={outcome}
              cx="70"
              cy="70"
              r={radius}
              fill="none"
              stroke={OUTCOME_COLOR[outcome] ?? "#94a3b8"}
              strokeWidth="20"
              strokeDasharray={dashArray}
              strokeDashoffset={dashOffset}
              transform="rotate(-90 70 70)"
            />
          );
        })}
      </svg>
      <ul className="outcome-donut__legend">
        {entries.map(([outcome, count]) => (
          <li key={outcome}>
            <span
              className="outcome-donut__swatch"
              style={{ background: OUTCOME_COLOR[outcome] ?? "#94a3b8" }}
            />
            {OUTCOME_LABEL[outcome] ?? outcome}
            <strong>{count}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ScanDetailPage({
  scanId,
  onBack,
  onSelectCase,
}: {
  scanId: string;
  onBack: () => void;
  onSelectCase: (caseId: string) => void;
}) {
  const [aggregate, setAggregate] = useState<ScanAggregate | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void getScanAggregate(scanId, { signal: controller.signal })
      .then(setAggregate)
      .catch((requestError: unknown) => {
        if (!isAbortError(requestError)) {
          setError(
            requestError instanceof ApiError
              ? requestError.message
              : "The request could not be completed.",
          );
        }
      });
    return () => controller.abort();
  }, [scanId]);

  return (
    <section aria-labelledby="scan-detail-title" className="page-stack">
      <button className="back-button" type="button" onClick={onBack}>
        ← Back to scans
      </button>
      <p className="eyebrow">Scan detail</p>
      <h1 id="scan-detail-title">Manual scan results</h1>

      {error ? (
        <p className="notice notice--error" role="alert">
          {error}
        </p>
      ) : aggregate === null ? (
        <div className="panel loading-skeleton" role="status">
          <span className="visually-hidden">Loading scan…</span>
        </div>
      ) : (
        <>
          <section className="panel" aria-label="Run summary">
            <span className={`status status--${aggregate.status}`}>{aggregate.status}</span>
            <span className="identifier">{aggregate.scan_id}</span>
            {aggregate.completed_at ? (
              <span>Completed {formatDateTime(aggregate.completed_at)}</span>
            ) : null}
          </section>

          <div className="scan-detail-grid">
            <section className="panel" aria-label="Results from this scan">
              <h2>Results from this scan</h2>
              <ul className="scan-results-list">
                {aggregate.results.map((row) => (
                  <li key={row.case_id}>
                    <div>
                      <strong>{row.product_name}</strong>
                      <span>
                        Need by {formatDate(row.need_by_date)}
                      </span>
                    </div>
                    <span className={`status status--${row.outcome}`}>
                      {OUTCOME_LABEL[row.outcome] ?? row.outcome}
                    </span>
                    <span>{row.amount ? formatCurrency(row.amount, "USD") : "—"}</span>
                    <button
                      type="button"
                      onClick={() => onSelectCase(row.case_id)}
                    >
                      View recommendation for {row.product_name}
                    </button>
                  </li>
                ))}
              </ul>
              {aggregate.results.length === 0 ? (
                <p>No products needed replenishment in this scan.</p>
              ) : null}
            </section>

            <section className="panel" aria-label="Outcome breakdown">
              <h2>Outcome breakdown</h2>
              <OutcomeDonut counts={aggregate.outcomeCounts} />
            </section>
          </div>
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run tests/scan-detail.test.tsx
```

Expected: PASS. If the accessible-name query for the "View recommendation"
button doesn't match (button text is `View recommendation for {product
name}`, the test regex was `/view recommendation.*happy-path/i` — confirm
this matches; adjust either the button text or the test regex to agree,
whichever better matches `Scan_details.png`'s literal "View recommendation"
button copy — the mockup's button text is exactly "View recommendation",
without the product name suffix, so prefer changing the button to plain
`View recommendation` with an `aria-label` carrying the product-name context
for accessibility, and adjust the test to query by that `aria-label`
instead of visible text).

- [ ] **Step 5: Run full frontend verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: PASS for this task's own files; `App.tsx`/`OverviewPage.tsx`
still fail to compile until Task 9.

- [ ] **Step 6: Add outcome donut and results-table CSS**

Append to `frontend/src/styles.css`, reusing existing tokens
(`--accent`, `--border`, `--muted`):

```css
.scan-detail-grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: minmax(0, 2fr) minmax(16rem, 1fr);
  align-items: start;
}

.scan-results-list {
  display: grid;
  gap: 0.75rem;
  list-style: none;
  margin: 1rem 0 0;
  padding: 0;
}

.scan-results-list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: 1rem;
  padding: 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
}

.outcome-donut {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
}

.outcome-donut svg {
  width: 140px;
  height: 140px;
}

.outcome-donut__legend {
  display: grid;
  gap: 0.5rem;
  list-style: none;
  margin: 0;
  padding: 0;
  width: 100%;
}

.outcome-donut__legend li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.outcome-donut__legend strong {
  margin-left: auto;
}

.outcome-donut__swatch {
  display: inline-block;
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 999px;
}
```

- [ ] **Step 7: Run full frontend verification and commit**

```bash
cd frontend && npm run typecheck && npm run lint
git add frontend/src/pages/ScanDetailPage.tsx frontend/tests/scan-detail.test.tsx frontend/src/styles.css
git commit -m "feat(frontend): add ScanDetailPage results table and outcome donut"
```

---

## Task 9: Wire up navigation

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/OverviewPage.tsx`
- Modify: `frontend/src/pages/RecommendationPage.tsx`
- Modify: `frontend/tests/overview.test.tsx`
- Modify: `frontend/tests/recommendation.test.tsx`

**Interfaces:**
- `App.tsx` gains `selectedCaseId: string | null` alongside
  `selectedScanId`. Three-way render: `selectedScanId === null` →
  `OverviewPage`; `selectedScanId !== null && selectedCaseId === null` →
  `ScanDetailPage`; `selectedCaseId !== null` → `RecommendationPage`.
- `RecommendationPage` fetches via `getCase(scanId, caseId)` instead of
  `getScan(scanId)`; its props gain `caseId: string`.

- [ ] **Step 1: Write the failing test**

In `frontend/tests/overview.test.tsx`, following its existing render/click
patterns, update the scan-selection test (locate the existing test
asserting `onSelectScan` is called on a scan-list click — read the file
first) to confirm it still only navigates to a scan (not a case) — this
should already pass unmodified, since `OverviewPage`'s own contract
(`onSelectScan(scanId)`) doesn't change in this task, only what `App.tsx`
does with that callback.

Add a new integration-style test in a suitable existing file (or
`frontend/tests/app-navigation.test.tsx` if none of the existing files fit
— check `frontend/tests/` for an existing `App`-level test first) asserting
the three-way navigation: selecting a scan shows `ScanDetailPage` content
(not `RecommendationPage` content), and selecting a result row from there
shows `RecommendationPage` content for that case.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npm test 2>&1 | tail -60
```

Expected: FAIL/compile-error — `App.tsx` still jumps straight from
`OverviewPage` to `RecommendationPage`.

- [ ] **Step 3: Update `App.tsx`**

Add `selectedCaseId` state and restructure the render branch (currently
`App.tsx:78-94`):

```tsx
const [selectedScanId, setSelectedScanId] = useState<string | null>(null);
const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
```

```tsx
{session === undefined ? (
  <p role="status">Loading session…</p>
) : session === null ? (
  <SignInPage message={sessionError} />
) : selectedScanId === null ? (
  <OverviewPage
    view={workspacePage}
    onSelectScan={(scanId) => {
      setWorkspacePage("scans");
      setSelectedScanId(scanId);
    }}
  />
) : selectedCaseId === null ? (
  <ScanDetailPage
    scanId={selectedScanId}
    onBack={() => {
      setSelectedScanId(null);
      setWorkspacePage("scans");
    }}
    onSelectCase={setSelectedCaseId}
  />
) : (
  <RecommendationPage
    scanId={selectedScanId}
    caseId={selectedCaseId}
    onBack={() => setSelectedCaseId(null)}
  />
)}
```

Also update the brand-link click handler (currently `App.tsx:44-48`) to
reset `selectedCaseId` alongside `selectedScanId`.

Add `import { ScanDetailPage } from "./pages/ScanDetailPage";`.

- [ ] **Step 4: Update `RecommendationPage.tsx`**

Change `RecommendationPageProps` to add `caseId: string`, and its data
fetch from `getScan(scanId)` to `getCase(scanId, caseId)` — update the
`useEffect`/polling logic's call site accordingly (the polling loop
structure itself is unchanged, only which fetch function it calls and what
type it stores).

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd frontend && npm test 2>&1 | tail -60
```

Expected: PASS — update any remaining test call sites in
`recommendation.test.tsx` that construct `<RecommendationPage
scanId="scan-101" onBack={vi.fn()} />` to also pass a `caseId` prop (e.g.
`caseId="scan-101:product-101"`, and update those tests' mocked fetch URLs/
response shapes to match `CaseDetail` instead of the old `Scan` shape from
Task 7).

- [ ] **Step 6: Run full verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/pages/RecommendationPage.tsx \
  frontend/tests/
git commit -m "feat(frontend): wire ScanDetailPage into scan/case navigation"
```

---

## Task 10: Update `docs/plan.md`

**Files:**
- Modify: `docs/plan.md`

- [ ] **Step 1: Insert the T27C task section**

Insert between the T27 amendment note (currently ending around
`docs/plan.md:2941`) and the `#### T28` heading (currently line 2943), a new
section following the exact same format as T28/T29 (Files / Interfaces /
Work and tests / Dependencies / Requirements / Complete when):

```markdown
#### T27C — Evaluate every replenishment candidate independently

**Files**

- Modify `src/procurement/agent/state.py`, `src/procurement/agent/nodes/walking_skeleton.py`,
  `src/procurement/agent/graph.py`, `src/procurement/ports/repositories.py`,
  `src/procurement/adapters/aws/dynamodb.py`, `src/procurement/api/services/scans.py`,
  `src/procurement/api/routes/scans.py`.
- Create `frontend/src/pages/ScanDetailPage.tsx`; modify `frontend/src/api/client.ts`,
  `frontend/src/App.tsx`, `frontend/src/pages/OverviewPage.tsx`,
  `frontend/src/pages/RecommendationPage.tsx`.

**Interfaces**

- Consumes: T27's existing per-candidate evidence-gathering and LLM
  reasoning path, now invoked once per candidate instead of once per scan.
- Produces: one independent case (result: `approval_ready`, `manual_review`,
  or `no_valid_offer`) per replenishment candidate, aggregated under one
  `ScanRecord` per scan, surfaced as a results table with an outcome
  breakdown.

**Work and tests**

- [ ] **Step 1:** Add `NoValidOfferResult`; restructure the graph to accept
  one seeded candidate per invocation with `discover_candidates` called once
  per scan outside the graph.
- [ ] **Step 2:** Add `ScanRecord`/`CaseSummary` persistence, per-product
  `CaseId` scheme, and the DynamoDB adapter's scan-scoped case query.
- [ ] **Step 3:** Restructure `ScanService` to invoke the graph once per
  discovered candidate with an isolated `thread_id`, aggregating results
  and isolating per-case failures from siblings.
- [ ] **Step 4:** Add the scan-aggregate and case-detail API response
  shapes and routes, including the unreachable `ConfirmedResult` placeholder
  for future T29 use.
- [ ] **Step 5:** Add `ScanDetailPage` (results table + outcome donut) and
  wire three-way frontend navigation (scans list → scan detail → case
  detail).

**Dependencies:** T27.

**Requirements:** CR-02, CR-03, CR-04, CR-05, CR-06, CR-12, CR-13, CR-15;
spec section 5.1 ("One independent recommendation, approval, and PO per
product").

**Complete when:** A scan evaluates every replenishment candidate
independently, no candidate's outcome is silently dropped or fails a
sibling candidate's case, and the scan-detail page shows every result with
an outcome breakdown, each linking to its own recommendation detail.
```

- [ ] **Step 2: Update T28's dependency line**

Change T28's `**Dependencies:** T27.` (currently `docs/plan.md:2982`) to
`**Dependencies:** T27C.`

- [ ] **Step 3: Commit**

```bash
git add docs/plan.md
git commit -m "docs: insert T27C into the course plan between T27 and T28"
```

## Final Self-Review (perform before considering the plan complete)

- [ ] **Spec coverage check:** Re-read
  `docs/superpowers/specs/2026-08-18-t27c-scan-cardinality-design.md`
  section by section and confirm each decision maps to a task above: case
  boundary (Tasks 3-4), LLM call pattern (Task 2), `NoValidOfferResult`
  (Tasks 1-2), `ConfirmedResult` placeholder (Task 5), orchestration
  Approach A (Task 4), `ScanRecord` (Task 3), API shape (Task 5), frontend
  (Tasks 7-9). Non-goals — confirm no task adds draft/approval/confirmation
  logic or home page changes.
- [ ] **Full-stack verification:** `make check`, `make test-integration`
  (backend); `npm run typecheck && npm run lint && npm test && npm run
  build` (frontend, from `frontend/`).
- [ ] **Manual browser check:** Run the local Compose stack
  (`make compose-up`), trigger a manual scan, and visually compare the
  rendered scan-detail page against `Scan_details.png` — results table,
  outcome donut, and that each row correctly opens its own recommendation
  detail page unchanged from sub-project 2.

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-08-18-t27c-scan-cardinality.md`.

As with the previous plan, `subagent-driven-development` and
`executing-plans` aren't available in this repository's skill set. Given
this plan's size and the number of places later tasks depend on earlier
tasks' exact new interfaces (Task 4 depends on Task 2's and Task 3's real
signatures, Task 9 depends on Task 7's real types), executing it as a
sequence of fresh, isolated subagents per task carries real risk of drift
between what one task assumes and what the prior task actually produced.

**Recommended: execute inline, task by task, in this session**, verifying
each task's real diff against what the next task's steps assume before
moving on. Tasks 1-6 (backend) should complete before Tasks 7-9 (frontend),
since the frontend tasks consume the backend's real response shapes.

Which approach would you like?
