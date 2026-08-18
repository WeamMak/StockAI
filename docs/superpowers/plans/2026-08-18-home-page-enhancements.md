# Home Page Enhancements Implementation Plan

> **For agentic workers:** The `superpowers:subagent-driven-development` and
> `superpowers:executing-plans` sub-skills are not present in this
> repository's skill set. Execute tasks in order, one at a time, running
> each task's verification steps before moving to the next. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `frontend/src/pages/OverviewPage.tsx`'s home view closer to
`home_page.png` by adding a cross-scan "Recent recommendations" panel and
swapping the attention panel's "In progress" card for "Over-budget
exceptions" — both backed by data the backend already persists but has
never surfaced through an API.

**Architecture:** `CaseSummary` (already used for one scan's results table)
gains three fields it doesn't have yet (`scan_id`, `budget_status`,
`completed_at`) that are already available on every persisted `CaseRecord`.
A new `ScanService.list_recent_cases()` wraps the repository's existing,
currently-unused cross-scan `list_cases()` to build `CaseSummary` rows
directly from persisted records (no `ReplenishmentCandidate` needed). A new
`GET /api/v1/cases` route exposes it. The frontend adds one new panel and
one small helper to `OverviewPage.tsx`, reusing every existing icon/badge/
panel convention already in the file.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, DynamoDB single-table
adapter (+ in-memory fake) — no schema change, since `CaseSummary` is a
derived read model, not a stored item. React/TypeScript/Vitest.

## Global Constraints

- No week-over-week trend percentages or "Insights (Last 7 days)" panel —
  explicitly out of scope (spec Non-goals).
- No "View all recommendations" link or dedicated all-recommendations page
  — the panel shows a fixed 5 most-recent rows with no further affordance.
- No changes to `ScanDetailPage.tsx`'s or `RecommendationPage.tsx`'s own
  behavior, and no change to "Recent scans" rows — they already render a
  per-row outcome icon (`OverviewPage.tsx:160-171`).
- No new `Icon` glyphs — reuse the existing `check`/`alert`/`document` set.
- No `docs/plan.md` task number for this sub-project (spec Non-goals) —
  standalone spec → plan → implementation cycle under `docs/superpowers/`.
- Spec reference: `docs/superpowers/specs/2026-08-18-home-page-enhancements-design.md`
- Run `uv run ruff check <touched files> && uv run mypy <touched files>`
  after each backend task, and `npm run typecheck && npm run lint && npm test`
  (from `frontend/`) after each frontend task.

---

## Task 1: Extend `CaseSummary` with `scan_id`, `budget_status`, `completed_at`

**Files:**
- Modify: `src/procurement/ports/repositories.py:103-113`
- Modify: `src/procurement/api/services/scans.py` (`_summarize`, currently
  lines 569-594)
- Modify: `src/procurement/api/routes/scans.py` (`CaseSummaryResponse`,
  currently lines 160-170; `scan_aggregate_response`, currently lines
  285-311)
- Modify: `tests/unit/api/test_scans.py` (extend
  `test_manual_scan_returns_202_and_can_be_polled_to_completion`, currently
  lines 180-233)

**Interfaces:**
- Produces: `CaseSummary` gains `scan_id: str`, `budget_status: str`,
  `completed_at: UtcTimestamp | None`. `CaseSummaryResponse` gains matching
  `scan_id: str`, `budget_status: str`, `completed_at: datetime | None`.

- [x] **Step 1: Write the failing test**

In `tests/unit/api/test_scans.py`, extend the existing assertions inside
`test_manual_scan_returns_202_and_can_be_polled_to_completion` (currently
ending at line 233). Add these lines directly after the existing
`assert results[0]["outcome"] == "approval_ready"` (line 210):

```python
    assert results[0]["scan_id"] == scan_id
    assert results[0]["budget_status"] == "within_budget"
    assert results[0]["completed_at"] is not None
```

- [x] **Step 2: Run test to verify it fails**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/api/test_scans.py::test_manual_scan_returns_202_and_can_be_polled_to_completion -v
```

Expected: FAIL with `KeyError: 'scan_id'` (or similar — the response body
does not yet have these keys on each result row).

- [x] **Step 3: Add the fields to `CaseSummary`**

In `src/procurement/ports/repositories.py`, replace the `CaseSummary` class
(currently lines 103-113):

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
    scan_id: str
    budget_status: str
    completed_at: UtcTimestamp | None
```

- [x] **Step 4: Update `ScanService._summarize` to populate the new fields**

Read `src/procurement/api/services/scans.py:569-594` first to confirm the
exact current body, then update the `CaseSummary(...)` construction at the
end of `_summarize` to add the three new fields:

```python
        return CaseSummary(
            case_id=terminal.case_id.value,
            product_id=candidate.product_id,
            product_name=candidate.product_name,
            outcome=outcome,
            amount=amount,
            need_by_date=need_by_date,
            scan_id=terminal.case_id.value.split(":", 1)[0],
            budget_status=(
                terminal.result.budget_status
                if terminal.result is not None
                else "not_evaluated"
            ),
            completed_at=terminal.completed_at,
        )
```

- [x] **Step 5: Add the fields to `CaseSummaryResponse` and its mapping**

In `src/procurement/api/routes/scans.py`, update `CaseSummaryResponse`
(currently lines 160-170):

```python
class CaseSummaryResponse(BaseModel):
    """One case's result, enough to render a scan's results table."""

    model_config = _RESPONSE_CONFIG

    case_id: str
    product_id: str
    product_name: str
    outcome: str
    amount: str | None
    need_by_date: date | None
    scan_id: str
    budget_status: str
    completed_at: datetime | None
```

Extract the inline `CaseSummaryResponse(...)` construction currently inside
`scan_aggregate_response`'s generator (lines 298-306) into a standalone
function placed just above `scan_aggregate_response` (currently starting at
line 285):

```python
def case_summary_response(row: CaseSummary) -> CaseSummaryResponse:
    """Map one internal case summary to its filtered public response model."""

    return CaseSummaryResponse(
        case_id=row.case_id,
        product_id=row.product_id,
        product_name=row.product_name,
        outcome=row.outcome,
        amount=format(row.amount, "f") if row.amount is not None else None,
        need_by_date=row.need_by_date,
        scan_id=row.scan_id,
        budget_status=row.budget_status,
        completed_at=row.completed_at,
    )
```

Update `scan_aggregate_response` to call it instead of constructing
`CaseSummaryResponse` inline:

```python
        results=tuple(case_summary_response(row) for row in snapshot.results),
```

`routes/scans.py` has no existing import from `procurement.ports.repositories`
— add a new import line near the top of the file, after the existing
`from procurement.api.services.scans import (...)` block (currently lines
18-24):

```python
from procurement.ports.repositories import CaseSummary
```

(`CaseSummary` is needed to type `case_summary_response`'s parameter.)

- [x] **Step 6: Run test to verify it passes**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/api/test_scans.py -v
```

Expected: PASS — the extended test plus every other pre-existing test in
the file (none of them assert an *exact* dict equality on a full result
row, only specific keys, so adding fields should not break anything else;
confirm this by reading any test that does assert full-row equality before
concluding, and update it if one exists).

- [x] **Step 7: Run focused quality checks**

```bash
cd /home/weam/StockAI && uv run ruff check src/procurement/ports/repositories.py src/procurement/api/services/scans.py src/procurement/api/routes/scans.py tests/unit/api/test_scans.py
uv run mypy src/procurement/ports/repositories.py src/procurement/api/services/scans.py src/procurement/api/routes/scans.py
```

Expected: both pass.

- [x] **Step 8: Commit**

```bash
git add src/procurement/ports/repositories.py src/procurement/api/services/scans.py \
  src/procurement/api/routes/scans.py tests/unit/api/test_scans.py
git commit -m "feat(persistence): add scan_id/budget_status/completed_at to CaseSummary"
```

---

## Task 2: `ScanService.list_recent_cases` and `GET /api/v1/cases`

**Files:**
- Modify: `src/procurement/api/services/scans.py`
- Modify: `src/procurement/api/routes/cases.py`
- Create: `tests/unit/api/test_cases.py`
- Modify: `tests/integration/test_walking_skeleton.py`

**Interfaces:**
- Consumes: `self._repository.list_cases(limit=...)` (already implemented
  on both `InMemoryApplicationRepository` and the DynamoDB adapter, unused
  until now), `CaseSummary` (Task 1).
- Produces: `ScanService.list_recent_cases(*, limit: int) -> tuple[CaseSummary, ...]`.
  `GET /api/v1/cases?limit=5` → `RecentCasesResponse { cases:
  tuple[CaseSummaryResponse, ...] }`.

- [x] **Step 1: Write the failing tests**

Create `tests/unit/api/test_cases.py`, following the exact
`create_app`/`ASGITransport`/`sign_in`/`MultiCandidateWorkflow` pattern
already used in `tests/unit/api/test_scans.py` (import
`MultiCandidateWorkflow`, `_poll_until_finished`, and `sign_in` from there
and from `tests.support.local_identity` respectively — read
`tests/unit/api/test_scans.py:1-30` first to copy its exact import block):

```python
"""Cross-scan recent-recommendations listing API behavior."""

from __future__ import annotations

from typing import cast

import pytest
from httpx2 import ASGITransport, AsyncClient
from tests.support.local_identity import LocalIdentityProvider, sign_in

from procurement.api.app import create_app
from tests.unit.api.test_scans import MultiCandidateWorkflow, _poll_until_finished


@pytest.mark.anyio
async def test_recent_cases_spans_multiple_scans_newest_first() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=2)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        csrf_headers = await sign_in(client)
        first = await client.post("/api/v1/scans", headers=csrf_headers)
        await _poll_until_finished(client, first.json()["scan_id"])
        second = await client.post("/api/v1/scans", headers=csrf_headers)
        second_finished = await _poll_until_finished(
            client, second.json()["scan_id"]
        )
        recent = await client.get("/api/v1/cases")

    assert recent.status_code == 200
    cases = cast(list[dict[str, object]], recent.json()["cases"])
    assert len(cases) == 4  # 2 candidates per scan, 2 scans
    second_scan_id = second_finished["scan_id"]
    assert cases[0]["scan_id"] == second_scan_id
    assert cases[1]["scan_id"] == second_scan_id
    assert {row["outcome"] for row in cases} == {"approval_ready"}
    assert {row["case_id"] for row in cases} == {
        f"{first.json()['scan_id']}:product-0",
        f"{first.json()['scan_id']}:product-1",
        f"{second_scan_id}:product-0",
        f"{second_scan_id}:product-1",
    }


@pytest.mark.anyio
async def test_recent_cases_bounds_limit_to_one_through_twenty() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=1)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        await sign_in(client)
        too_large = await client.get("/api/v1/cases?limit=21")
        too_small = await client.get("/api/v1/cases?limit=0")

    assert too_large.status_code == 422
    assert too_small.status_code == 422


@pytest.mark.anyio
async def test_recent_cases_defaults_to_no_history() -> None:
    workflow = MultiCandidateWorkflow(candidate_count=1)
    application = create_app(
        scan_workflow=workflow,
        identity_provider=LocalIdentityProvider(),
    )
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        await sign_in(client)
        recent = await client.get("/api/v1/cases")

    assert recent.status_code == 200
    assert recent.json()["cases"] == []
```

- [x] **Step 2: Run tests to verify they fail**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/api/test_cases.py -v
```

Expected: FAIL — `GET /api/v1/cases` returns 404 (route doesn't exist) or
422/import errors since `MultiCandidateWorkflow`/`_poll_until_finished`
import correctly (those already exist in `test_scans.py`) but the route
itself is missing.

- [x] **Step 3: Add `_summarize_record` and `list_recent_cases` to `ScanService`**

`CaseRecord` is already imported (`from procurement.ports.repositories
import (ApplicationRepository, CaseRecord, CaseSummary, ...)`, currently
lines 33-41) — no import changes are needed for this step.

Add these two methods to the `ScanService` class, placed directly after
`list_scans` (currently ending at line 226):

```python
    async def list_recent_cases(self, *, limit: int) -> tuple[CaseSummary, ...]:
        """Return a bounded newest-first list of cases spanning every scan."""

        page = await self._repository.list_cases(limit=limit)
        summaries = (self._summarize_record(record) for record in page.records)
        return tuple(summary for summary in summaries if summary is not None)

    @staticmethod
    def _summarize_record(record: CaseRecord) -> CaseSummary | None:
        if record.status == ScanStatus.SKIPPED.value:
            return None
        product_id = record.result.product_id if record.result is not None else None
        product_name = (
            record.result.product_name if record.result is not None else None
        )
        if product_id is None or product_name is None:
            if not record.evidence:
                return None
            product_id = record.evidence[0].product_id
            product_name = record.evidence[0].product_name
        if record.result is not None:
            outcome = record.result.outcome
            amount = record.result.normalized_cost
            budget_status = record.result.budget_status
        else:
            outcome = "error"
            amount = None
            budget_status = "not_evaluated"
        need_by_date = (
            record.evidence[0].shortage.need_by_date if record.evidence else None
        )
        return CaseSummary(
            case_id=record.case_id.value,
            product_id=product_id,
            product_name=product_name,
            outcome=outcome,
            amount=amount,
            need_by_date=need_by_date,
            scan_id=record.case_id.value.split(":", 1)[0],
            budget_status=budget_status,
            completed_at=record.completed_at,
        )
```

- [x] **Step 4: Add the route**

Read `src/procurement/api/routes/cases.py` in full first (it is short —
27 lines). Replace its entire contents:

```python
"""Read-only procurement-case evidence and cross-scan listing routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from procurement.api.auth.rbac import require_officer
from procurement.api.routes.scans import CaseSummaryResponse, case_summary_response, scan_service_from

router = APIRouter(
    prefix="/api/v1/cases",
    tags=["cases"],
    dependencies=[Depends(require_officer)],
)


class CaseEvidenceResponse(BaseModel):
    """Bounded authoritative evidence for one scan-created case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: str
    evidence: tuple[dict[str, object], ...]


class RecentCasesResponse(BaseModel):
    """Bounded newest-first list of cases spanning every scan."""

    model_config = ConfigDict(extra="forbid")

    cases: tuple[CaseSummaryResponse, ...]


@router.get("")
async def list_recent_cases(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> RecentCasesResponse:
    """Return the most recent cases across every scan, newest first."""

    service = scan_service_from(request)
    summaries = await service.list_recent_cases(limit=limit)
    return RecentCasesResponse(
        cases=tuple(case_summary_response(row) for row in summaries)
    )


@router.get("/{case_id}")
async def get_case(case_id: str, request: Request) -> CaseEvidenceResponse:
    """Return immutable deterministic evidence, including skipped reasons."""

    snapshot = await scan_service_from(request).get_case(case_id)
    return CaseEvidenceResponse(
        case_id=snapshot.case_id,
        status=snapshot.status.value,
        evidence=tuple(item.to_dict() for item in snapshot.evidence),
    )
```

The bare `GET /api/v1/cases` route is declared before `/{case_id}` in this
file for readability, but path shape (no segment vs. one segment) means
there is no ordering-dependent collision risk either way.

- [x] **Step 5: Run tests to verify they pass**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/api/test_cases.py tests/unit/api/test_scans.py -v
```

Expected: PASS.

- [x] **Step 6: Extend the real-MCP-transport integration test**

Read `tests/integration/test_walking_skeleton.py` in full first (currently
113 lines). In `test_local_processes_run_langgraph_over_real_mcp_transport`
(currently lines 29-80), add a call to the new endpoint right after the
existing `case = client.get(...)` call (currently lines 43-46):

```python
            recent = client.get("/api/v1/cases", headers=auth_headers)
```

Add these assertions after the existing `assert case.status_code == 200`
line (currently line 58):

```python
    assert recent.status_code == 200
    recent_cases = recent.json()["cases"]
    assert any(row["case_id"] == case_id for row in recent_cases)
    matching = next(row for row in recent_cases if row["case_id"] == case_id)
    assert matching["scan_id"] == scan_id
    assert matching["budget_status"] == "within_budget"
```

- [x] **Step 7: Run the integration test to verify it passes**

```bash
cd /home/weam/StockAI && uv run pytest tests/integration/test_walking_skeleton.py -v
```

Expected: PASS — both tests in this file (the extended happy-path test and
the pre-existing multi-candidate isolation test, unaffected by this
change).

- [x] **Step 8: Run focused quality checks**

```bash
cd /home/weam/StockAI && uv run ruff check src/procurement/api/services/scans.py src/procurement/api/routes/cases.py tests/unit/api/test_cases.py tests/integration/test_walking_skeleton.py
uv run mypy src/procurement/api/services/scans.py src/procurement/api/routes/cases.py
```

Expected: both pass. If `ruff` flags the long import line in
`routes/cases.py` (`from procurement.api.routes.scans import
CaseSummaryResponse, case_summary_response, scan_service_from`), split it
across multiple lines with parentheses following this project's existing
import-wrapping style (check any other multi-name import in the same file
for the exact formatting `ruff format` prefers, then run `uv run ruff
format src/procurement/api/routes/cases.py` to let it resolve the wrapping
automatically rather than hand-formatting).

- [x] **Step 9: Commit**

```bash
git add src/procurement/api/services/scans.py src/procurement/api/routes/cases.py \
  tests/unit/api/test_cases.py tests/integration/test_walking_skeleton.py
git commit -m "feat(api): add cross-scan recent-cases listing endpoint"
```

---

## Task 3: Backend full verification

**Files:** none (verification-only task, no code changes)

- [x] **Step 1: Run the complete backend quality gate**

```bash
cd /home/weam/StockAI && uv run ruff format --check src tests scripts odoo
uv run ruff check src tests scripts odoo
uv run mypy
uv run pytest -q tests/unit
```

Expected: all PASS. (Skip the Makefile's `actionlint` step if it is not
installed in this environment — that is a pre-existing environment gap
unrelated to this sub-project's changes, not a regression to fix here.)

- [x] **Step 2: Run integration tests**

```bash
cd /home/weam/StockAI && uv run pytest tests/integration -q
```

Expected: PASS — no integration test currently exercises `GET
/api/v1/cases`, so this step primarily confirms Task 1/2's changes to
shared files (`services/scans.py`, `routes/scans.py`) didn't regress the
existing real-MCP-transport/DynamoDB-backed scan flows.

- [x] **Step 3: Fix any failures found**

If either step surfaces a failure, fix it with its own failing-test-first
cycle matching the failing file's existing test conventions, then re-run
Steps 1-2 until both pass.

- [x] **Step 4: Commit any fixes from Step 3**

```bash
git add -A
git commit -m "fix: reconcile backend regressions found by full verification"
```

(Skip this step entirely if Step 3 found nothing to fix.)

---

## Task 4: Frontend types — `client.ts`

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/tests/api-client.test.ts`
- Modify: `frontend/tests/overview.test.tsx` (fixture updates only)
- Modify: `frontend/tests/scan-detail.test.tsx` (fixture updates only)

**Interfaces:**
- `CaseSummary` gains `scan_id: string`, `budget_status: string`,
  `completed_at: string | null`.
- New: `listRecentCases(options?: { limit?: number } & RequestOptions):
  Promise<CaseSummary[]>`.

- [x] **Step 1: Write the failing test**

In `frontend/tests/api-client.test.ts`, add a new fixture near the top,
next to the existing `CASE_DETAIL_PAYLOAD` (currently lines 36-47):

```ts
const CASE_SUMMARY_PAYLOAD = {
  case_id: "scan-recent:product-1",
  product_id: "product-1",
  product_name: "Fictional Widget",
  outcome: "approval_ready",
  amount: "120.500000",
  need_by_date: "2026-08-20",
  scan_id: "scan-recent",
  budget_status: "within_budget",
  completed_at: "2026-08-18T10:00:40Z",
};
```

Add a new test inside the existing `describe("scan API client", ...)`
block (following the exact style of the `"parses bounded scan-list..."`
test at line 100):

```ts
  it("parses recent cases spanning multiple scans", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ cases: [CASE_SUMMARY_PAYLOAD] }),
      ),
    );

    await expect(listRecentCases()).resolves.toEqual([
      {
        case_id: "scan-recent:product-1",
        product_id: "product-1",
        product_name: "Fictional Widget",
        outcome: "approval_ready",
        amount: "120.500000",
        need_by_date: "2026-08-20",
        scan_id: "scan-recent",
        budget_status: "within_budget",
        completed_at: "2026-08-18T10:00:40Z",
      },
    ]);
  });
```

Add `listRecentCases` to the existing `import { ... } from "../src/api/client"`
block at the top of the file.

- [x] **Step 2: Run test to verify it fails**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/api-client.test.ts -t "recent cases"
```

Expected: FAIL — `listRecentCases` does not exist yet, so the import
itself fails to compile.

- [x] **Step 3: Extend `CaseSummary` and its parser**

In `frontend/src/api/client.ts`, update the `CaseSummary` interface
(currently lines 196-203):

```ts
export interface CaseSummary {
  case_id: string;
  product_id: string;
  product_name: string;
  outcome: string;
  amount: string | null;
  need_by_date: string | null;
  scan_id: string;
  budget_status: string;
  completed_at: string | null;
}
```

Update `parseCaseSummary` (currently lines 656-676) to validate and map the
three new fields, following its exact existing strict-validation style:

```ts
function parseCaseSummary(value: unknown): CaseSummary {
  if (
    !isRecord(value) ||
    typeof value.case_id !== "string" ||
    typeof value.product_id !== "string" ||
    typeof value.product_name !== "string" ||
    typeof value.outcome !== "string" ||
    !isNullableString(value.amount) ||
    !isNullableString(value.need_by_date) ||
    typeof value.scan_id !== "string" ||
    typeof value.budget_status !== "string" ||
    !isNullableString(value.completed_at)
  ) {
    return invalidResponse();
  }
  return {
    case_id: value.case_id,
    product_id: value.product_id,
    product_name: value.product_name,
    outcome: value.outcome,
    amount: value.amount,
    need_by_date: value.need_by_date,
    scan_id: value.scan_id,
    budget_status: value.budget_status,
    completed_at: value.completed_at,
  };
}
```

- [x] **Step 4: Add `listRecentCases`**

Add `const CASES_PATH = "/api/v1/cases";` at the top of the file, next to
the existing `const SCANS_PATH = "/api/v1/scans";` and `const SESSION_PATH
= "/api/v1/session";` lines (currently lines 1-2).

Add the following near the end of the file, after `getCase` (currently
ending at line 853):

```ts
const MAX_RECENT_CASES_LENGTH = 20;

export async function listRecentCases(
  options: { limit?: number } & RequestOptions = {},
): Promise<CaseSummary[]> {
  const { limit, signal } = options;
  const path =
    limit === undefined ? CASES_PATH : `${CASES_PATH}?limit=${encodeURIComponent(String(limit))}`;
  const response = await request(path, { method: "GET", signal });
  if (
    !isRecord(response.body) ||
    !Array.isArray(response.body.cases) ||
    response.body.cases.length > MAX_RECENT_CASES_LENGTH
  ) {
    return invalidResponse();
  }
  return response.body.cases.map(parseCaseSummary);
}
```

- [x] **Step 5: Run test to verify it passes**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/api-client.test.ts
```

Expected: PASS.

- [x] **Step 6: Update existing fixtures that now fail strict validation**

`CaseSummary` is no longer satisfied by a case-summary object missing
`scan_id`/`budget_status`/`completed_at`. Two other test files construct
`results: [...]` arrays of case-summary-shaped fixtures and will now fail:

In `frontend/tests/scan-detail.test.tsx`, update the `AGGREGATE` fixture's
`results` array (currently lines 21-38) — add the three fields to each of
the two existing entries:

```ts
  results: [
    {
      case_id: "scan-4278:product-1",
      product_id: "product-1",
      product_name: "PROD Fictional Happy-Path Component",
      outcome: "approval_ready",
      amount: "1080.000000",
      need_by_date: "2026-08-18",
      scan_id: "scan-4278",
      budget_status: "within_budget",
      completed_at: "2026-08-18T14:41:00Z",
    },
    {
      case_id: "scan-4278:product-2",
      product_id: "product-2",
      product_name: "PROD Fictional No-Valid-Offer Component",
      outcome: "no_valid_offer",
      amount: null,
      need_by_date: "2026-08-18",
      scan_id: "scan-4278",
      budget_status: "not_evaluated",
      completed_at: "2026-08-18T14:41:00Z",
    },
  ],
```

In `frontend/tests/overview.test.tsx`, update the `SUCCEEDED_SCAN` fixture's
single `results` entry (currently lines 24-35) and the `MANUAL_REVIEW_SCAN`
fixture's single `results` entry (currently lines 42-53), adding the same
three fields to each (`scan_id` matching that fixture's own `scan_id`,
`budget_status: "within_budget"` for the approval-ready one and
`"not_evaluated"` for the manual-review one, `completed_at` matching that
fixture's own `completed_at`).

- [x] **Step 7: Run full frontend test suite**

```bash
cd /home/weam/StockAI/frontend && npm test -- --run
```

Expected: PASS — every test file, including the two fixture updates from
Step 6.

- [x] **Step 8: Run typecheck, lint, and build**

```bash
cd /home/weam/StockAI/frontend && npm run typecheck && npm run lint && npm run build
```

Expected: all PASS.

- [x] **Step 9: Commit**

```bash
git add frontend/src/api/client.ts frontend/tests/api-client.test.ts \
  frontend/tests/scan-detail.test.tsx frontend/tests/overview.test.tsx
git commit -m "feat(frontend): add listRecentCases and extend CaseSummary parsing"
```

---

## Task 5: Move outcome label/color mapping into `presentation.ts`

**Files:**
- Modify: `frontend/src/presentation.ts`
- Modify: `frontend/src/pages/ScanDetailPage.tsx:11-25`

**Interfaces:**
- Produces: `OUTCOME_LABEL: Record<string, string>` and `OUTCOME_COLOR:
  Record<string, string>`, exported from `frontend/src/presentation.ts`.

This is a pure refactor with no behavior change — verified by the existing
`scan-detail.test.tsx` suite continuing to pass unmodified, not a new test.

- [x] **Step 1: Move the constants**

In `frontend/src/presentation.ts`, add at the end of the file (after
`formatRatioPercent`, currently ending at line 83):

```ts
export const OUTCOME_LABEL: Record<string, string> = {
  approval_ready: "Approval ready",
  manual_review: "Manual review",
  no_valid_offer: "No valid offer",
  confirmed: "Confirmed",
  error: "Error",
};

export const OUTCOME_COLOR: Record<string, string> = {
  approval_ready: "#2f9e58",
  manual_review: "#3157c8",
  no_valid_offer: "#c0392b",
  confirmed: "#2f9e58",
  error: "#c0392b",
};
```

In `frontend/src/pages/ScanDetailPage.tsx`, remove the local `OUTCOME_LABEL`
and `OUTCOME_COLOR` const declarations (currently lines 11-25) and instead
import them:

```ts
import {
  formatCurrency,
  formatDate,
  formatDateTime,
  OUTCOME_COLOR,
  OUTCOME_LABEL,
} from "../presentation";
```

(Replacing the existing `import { formatCurrency, formatDate,
formatDateTime } from "../presentation";` line at the top of the file.)

- [x] **Step 2: Run the existing test suite to confirm no behavior changed**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/scan-detail.test.tsx
```

Expected: PASS — same 4 tests as before, unmodified, confirming the move
didn't change runtime behavior.

- [x] **Step 3: Run typecheck and lint**

```bash
cd /home/weam/StockAI/frontend && npm run typecheck && npm run lint
```

Expected: both PASS.

- [x] **Step 4: Commit**

```bash
git add frontend/src/presentation.ts frontend/src/pages/ScanDetailPage.tsx
git commit -m "refactor(frontend): share OUTCOME_LABEL/OUTCOME_COLOR via presentation.ts"
```

---

## Task 6: "Recent recommendations" panel on `OverviewPage`

**Files:**
- Modify: `frontend/src/pages/OverviewPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/overview.test.tsx`

**Interfaces:**
- Produces: `OverviewPageProps` gains `onSelectCase: (scanId: string,
  caseId: string) => void`. `App.tsx` passes a handler that sets both
  `selectedScanId` and `selectedCaseId` in one update.

- [x] **Step 1: Write the failing tests**

In `frontend/tests/overview.test.tsx`, add a new fixture near the top,
next to the existing `SUCCEEDED_SCAN`/`MANUAL_REVIEW_SCAN` fixtures
(currently ending at line 53):

```ts
const RECENT_CASES = [
  {
    case_id: "scan-succeeded:product-101",
    product_id: "product-101",
    product_name: "Fictional Safety Gloves",
    outcome: "approval_ready",
    amount: "437.500000",
    need_by_date: "2026-08-12",
    scan_id: "scan-succeeded",
    budget_status: "within_budget",
    completed_at: "2026-08-05T10:00:05Z",
  },
  {
    case_id: "scan-manual-review:product-102",
    product_id: "product-102",
    product_name: "Fictional Cable Ties",
    outcome: "manual_review",
    amount: null,
    need_by_date: null,
    scan_id: "scan-manual-review",
    budget_status: "not_evaluated",
    completed_at: "2026-08-05T10:00:07Z",
  },
];
```

Add two new tests inside the existing `describe("OverviewPage", ...)`
block, following the exact `vi.stubGlobal("fetch", ...)` mocking style
already used throughout the file (each existing test stubs one combined
`fetch` mock — the new tests need to distinguish the two calls `OverviewPage`
now makes, `listScans` and `listRecentCases`, by matching on the request
URL, matching the pattern already established for multi-call tests
elsewhere in this codebase, e.g. `tests/unit/api/test_scans.py`'s sequential
`.mockResolvedValueOnce` chains — here use a URL-dispatching mock instead
since call order between two independent `useEffect`-triggered fetches is
not guaranteed):

```ts
  it("lists recent recommendations with a link to their case", async () => {
    const onSelectCase = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.startsWith("/api/v1/cases")) {
          return Promise.resolve(jsonResponse({ cases: RECENT_CASES }));
        }
        return Promise.resolve(jsonResponse({ scans: [] }));
      }),
    );

    render(<OverviewPage onSelectScan={vi.fn()} onSelectCase={onSelectCase} />);

    const panel = await screen.findByRole("region", {
      name: "Recent recommendations",
    });
    expect(within(panel).getByText("Fictional Safety Gloves")).toBeInTheDocument();
    expect(within(panel).getByText("Fictional Cable Ties")).toBeInTheDocument();
    expect(within(panel).getByText("Manual review")).toBeInTheDocument();

    await userEvent.click(within(panel).getByText("Fictional Safety Gloves"));
    expect(onSelectCase).toHaveBeenCalledWith(
      "scan-succeeded",
      "scan-succeeded:product-101",
    );
  });

  it("shows an empty state when there are no recent recommendations", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.startsWith("/api/v1/cases")) {
          return Promise.resolve(jsonResponse({ cases: [] }));
        }
        return Promise.resolve(jsonResponse({ scans: [] }));
      }),
    );

    render(<OverviewPage onSelectScan={vi.fn()} onSelectCase={vi.fn()} />);

    const panel = await screen.findByRole("region", {
      name: "Recent recommendations",
    });
    expect(within(panel).getByText(/no recommendations yet/i)).toBeInTheDocument();
  });
```

Add `within` to the existing `import { render, screen, waitFor } from
"@testing-library/react";` line at the top of the file (it currently does
not import `within` — confirm before adding a duplicate).

Every *existing* test in this file stubs `fetch` with a single
`mockResolvedValue(...)` (not URL-dispatching), which after this task will
resolve to the same payload for both `listScans` and `listRecentCases`
calls. Since `listScans`'s expected payload shape is `{ scans: [...] }` and
`listRecentCases`'s is `{ cases: [...] }`, a mock returning only `{ scans:
[...] }` will make `listRecentCases` fail to parse (`cases` is `undefined`,
not an array) — read `parseCaseSummary`'s "empty state" and error test's
expectations after Step 4 below and adjust: `OverviewPage`'s new
`recentCases`-loading failure must not surface as a page-blocking error
(per the spec's "Error handling" section, it renders inline inside the
panel only). Confirm each pre-existing test's fetch mock either already
returns a shape both calls can parse, or update it to return `{ scans:
[...], cases: [] }`-compatible responses via the same URL-dispatch pattern
introduced above — do this for every pre-existing `vi.stubGlobal("fetch",
...)` call in the file that does not already use URL-dispatch, changing
each to route `/api/v1/cases*` requests to `jsonResponse({ cases: [] })`
and everything else to its existing payload. For example, the simplest
existing case, `"shows the loading state and then the empty state"`
(currently lines 90-103), goes from:

```ts
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(request));
```

to:

```ts
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        url.startsWith("/api/v1/cases")
          ? Promise.resolve(jsonResponse({ cases: [] }))
          : request,
      ),
    );
```

Apply the same `url.startsWith("/api/v1/cases") ? ... : ...` dispatch
rewrite to each of the file's other pre-existing `vi.stubGlobal("fetch",
...)` calls, keeping each test's original non-cases payload on the `:`
branch unchanged.

- [x] **Step 2: Run tests to verify they fail**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/overview.test.tsx
```

Expected: FAIL — no "Recent recommendations" region exists yet, and
`onSelectCase` is not yet a valid prop.

- [x] **Step 3: Add the panel to `OverviewPage.tsx`**

In `frontend/src/pages/OverviewPage.tsx`, update the imports (currently
lines 1-11):

```tsx
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  createManualScan,
  isAbortError,
  listRecentCases,
  listScans,
  type CaseSummary,
  type ScanAggregate,
} from "../api/client";
import { Icon } from "../components/Icon";
import { formatCurrency, formatDate, formatDateTime, OUTCOME_LABEL } from "../presentation";
```

Update `OverviewPageProps` (currently lines 13-16):

```tsx
interface OverviewPageProps {
  onSelectScan: (scanId: string) => void;
  onSelectCase: (scanId: string, caseId: string) => void;
  view?: "home" | "scans";
}
```

Add a small icon-mapping helper near the other module-level helpers
(after `outcomeClass`, currently ending at line 86):

```tsx
function recommendationIcon(outcome: string): "check" | "alert" {
  return outcome === "approval_ready" || outcome === "confirmed"
    ? "check"
    : "alert";
}
```

Update the component signature (currently line 88):

```tsx
export function OverviewPage({
  onSelectScan,
  onSelectCase,
  view = "home",
}: OverviewPageProps) {
```

Add new state and a second fetch effect, placed directly after the
existing `scans`/`loadError` state and `useEffect` (currently ending at
line 111):

```tsx
  const [recentCases, setRecentCases] = useState<CaseSummary[] | null>(null);
  const [recentCasesError, setRecentCasesError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void listRecentCases({ limit: 5, signal: controller.signal })
      .then((cases) => {
        setRecentCases(cases);
        setRecentCasesError(null);
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setRecentCasesError(safeMessage(error));
        }
      });
    return () => controller.abort();
  }, []);
```

Add the panel's content computation next to the existing `scanContent`
computation (currently lines 133-189), placed right after it:

```tsx
  const recentCasesContent = recentCasesError ? (
    <p className="notice notice--error" role="alert">
      {recentCasesError}
    </p>
  ) : recentCases === null ? (
    <div className="loading-skeleton" role="status">
      <span className="visually-hidden">Loading recent recommendations…</span>
      <span />
      <span />
      <span />
    </div>
  ) : recentCases.length === 0 ? (
    <div className="empty-state">
      <h3>No recommendations yet</h3>
      <p>Run a manual scan to create the first recommendation.</p>
    </div>
  ) : (
    <ul className="scan-list" aria-label="Recent procurement recommendations">
      {recentCases.map((row) => (
        <li key={row.case_id}>
          <button
            className="scan-link"
            type="button"
            onClick={() => onSelectCase(row.scan_id, row.case_id)}
            aria-label={`Open ${row.product_name}, ${
              OUTCOME_LABEL[row.outcome] ?? row.outcome
            }`}
          >
            <span
              className={`scan-list-icon scan-list-icon--${
                recommendationIcon(row.outcome) === "check" ? "approval" : "review"
              }`}
            >
              <Icon name={recommendationIcon(row.outcome)} />
            </span>
            <span className="scan-list-copy">
              <strong>{row.product_name}</strong>
              <small>
                Scan #{row.scan_id} · Need by {formatDate(row.need_by_date)}
              </small>
            </span>
            <span className={`status status--${row.outcome}`}>
              {OUTCOME_LABEL[row.outcome] ?? row.outcome}
            </span>
            <span>{row.amount ? formatCurrency(row.amount, "USD") : "—"}</span>
          </button>
        </li>
      ))}
    </ul>
  );
```

Add the new panel to the home-view grid (currently lines 247-284), as the
first child inside `.home-dashboard-grid`, directly before the existing
"Recent scan activity" `<section>`:

```tsx
          <section
            aria-label="Recent recommendations"
            className="panel dashboard-panel"
          >
            <div className="panel-heading">
              <span className="summary-icon summary-icon--blue">
                <Icon name="recommendation" />
              </span>
              <h2>Recent recommendations</h2>
            </div>
            {recentCasesContent}
          </section>
```

`formatDateTime` remains used elsewhere in the file (the existing "Recent
scans" rows) — do not remove it from the import even though the new panel
uses `formatDate` instead (need-by dates are calendar dates, not
timestamps, matching `ScanDetailPage.tsx`'s existing convention for the
same field).

- [x] **Step 4: Update `App.tsx`**

In `frontend/src/App.tsx`, update the `OverviewPage` usage (currently
lines 84-90):

```tsx
            <OverviewPage
              view={workspacePage}
              onSelectScan={(scanId) => {
                setWorkspacePage("scans");
                setSelectedScanId(scanId);
              }}
              onSelectCase={(scanId, caseId) => {
                setWorkspacePage("scans");
                setSelectedScanId(scanId);
                setSelectedCaseId(caseId);
              }}
            />
```

- [x] **Step 5: Add CSS for the 3-column grid**

In `frontend/src/styles.css`, update `.home-dashboard-grid` (currently
lines 526-531):

```css
.home-dashboard-grid {
  display: grid;
  align-items: start;
  gap: 1rem;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(16rem, 0.8fr);
}
```

Check the responsive breakpoint rules referencing `.home-dashboard-grid`
(currently lines 1516 and 1562, inside `@media` blocks) — read their
surrounding context first; if they collapse the grid to a single column
below some width, no change is needed there since a 3-column-to-1-column
collapse works the same regardless of column count. If they instead assume
exactly 2 children in some intermediate breakpoint, adjust accordingly
after reading their exact current rules.

Also add the case-outcome status-badge colors, which do not exist yet —
`.status--approval_ready`, `.status--manual_review`, `.status--no_valid_offer`,
and `.status--confirmed`/`.status--error` are referenced by class name in
both this new panel and the pre-existing `ScanDetailPage.tsx` results list
(`className={`status status--${row.outcome}`}`), but no CSS currently
defines them, so every one of those badges has silently rendered as the
generic gray `.status` pill with no color since T27C. Add, next to the
existing `.status--succeeded`/`.status--failed` rules (currently lines
615-629), using the same colors as `OUTCOME_COLOR` (Task 5) for visual
consistency with the outcome donut:

```css
.status--approval_ready,
.status--confirmed {
  color: #14532d;
  background: #dcfce7;
}

.status--manual_review {
  color: #1e3a8a;
  background: #dbeafe;
}

.status--no_valid_offer,
.status--error {
  color: #7f1d1d;
  background: #fee2e2;
}
```

This is a CSS-only addition — it changes no JSX/logic in `ScanDetailPage.tsx`
(honoring the spec's "no changes to `ScanDetailPage`" constraint) while
fixing the same pre-existing gap there as a side effect, since both pages
reference these class names already.

- [x] **Step 6: Run tests to verify they pass**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/overview.test.tsx
```

Expected: PASS — new tests plus every pre-existing test, with their fetch
mocks updated per Step 1's note.

- [x] **Step 7: Run full frontend verification**

```bash
cd /home/weam/StockAI/frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build
```

Expected: all PASS.

- [x] **Step 8: Commit**

```bash
git add frontend/src/pages/OverviewPage.tsx frontend/src/App.tsx frontend/src/styles.css \
  frontend/tests/overview.test.tsx
git commit -m "feat(frontend): add Recent recommendations panel to the home page"
```

---

## Task 7: "Over-budget exceptions" attention card

**Files:**
- Modify: `frontend/src/pages/OverviewPage.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/overview.test.tsx`

**Interfaces:**
- Produces: a new `overBudgetCount(scans: ScanAggregate[]): number` helper.
  The attention panel's third card renders "Over-budget exceptions"
  instead of "In progress".

- [x] **Step 1: Write the failing test**

In `frontend/tests/overview.test.tsx`, extend the existing
`"summarizes loaded scan outcomes without inventing data"` test (find its
exact current body first — it asserts on the `"What needs attention"`
region's content). Add a new scan fixture with an over-budget case, next
to the existing fixtures:

```ts
const OVER_BUDGET_SCAN = {
  ...QUEUED_SCAN,
  scan_id: "scan-over-budget",
  status: "succeeded",
  completed_at: "2026-08-05T10:00:08Z",
  results: [
    {
      case_id: "scan-over-budget:product-103",
      product_id: "product-103",
      product_name: "Fictional Industrial Fasteners",
      outcome: "approval_ready",
      amount: "980.000000",
      need_by_date: "2026-08-14",
      scan_id: "scan-over-budget",
      budget_status: "exception_required",
      completed_at: "2026-08-05T10:00:08Z",
    },
  ],
  outcome_counts: { approval_ready: 1 },
};
```

Add `OVER_BUDGET_SCAN` to that test's `scans` array in its `fetch` mock,
and add these assertions after the test's existing ones:

```ts
    const attention = screen.getByRole("region", { name: "What needs attention" });
    expect(attention).toHaveTextContent("1Over-budget exceptions");
    expect(attention).not.toHaveTextContent("In progress");
```

- [x] **Step 2: Run test to verify it fails**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/overview.test.tsx -t "summarizes loaded scan outcomes"
```

Expected: FAIL — the attention panel still shows "In progress", not
"Over-budget exceptions", and the over-budget count isn't computed at all.

- [x] **Step 3: Add `overBudgetCount` and update the attention panel**

In `frontend/src/pages/OverviewPage.tsx`, add a new helper next to
`scanCounts` (currently ending at line 73):

```tsx
function overBudgetCount(scans: ScanAggregate[]): number {
  let count = 0;
  for (const scan of scans) {
    for (const row of scan.results) {
      if (row.budget_status === "exception_required") {
        count += 1;
      }
    }
  }
  return count;
}
```

Update the `counts` computation (currently line 133) to include it:

```tsx
  const counts = scans === null ? null : scanCounts(scans);
  const overBudget = scans === null ? 0 : overBudgetCount(scans);
```

Replace the attention panel's third card (currently lines 275-280):

```tsx
                <article className="attention-card attention-card--exception">
                  <span className="summary-icon summary-icon--amber">
                    <Icon name="alert" />
                  </span>
                  <strong>{overBudget}</strong>
                  <span>Over-budget exceptions</span>
                  <small>Exceed budget thresholds</small>
                </article>
```

- [x] **Step 4: Add the CSS variant**

In `frontend/src/styles.css`, add next to the existing
`.attention-card--progress` rule (currently lines 599-601), which is no
longer used by the attention panel but is left in place since it is a
generic reusable class name, not specific to this one card:

```css
.attention-card--exception {
  background: #fffaf0;
}
```

- [x] **Step 5: Run test to verify it passes**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/overview.test.tsx
```

Expected: PASS.

- [x] **Step 6: Run full frontend verification**

```bash
cd /home/weam/StockAI/frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build
```

Expected: all PASS.

- [x] **Step 7: Commit**

```bash
git add frontend/src/pages/OverviewPage.tsx frontend/src/styles.css frontend/tests/overview.test.tsx
git commit -m "feat(frontend): swap in-progress attention card for over-budget exceptions"
```

---

## Task 8: Final self-review and manual browser verification

**Files:** none (verification-only task, no code changes expected; fixes
if verification surfaces them)

- [x] **Step 1: Re-read the spec section by section**

Re-read `docs/superpowers/specs/2026-08-18-home-page-enhancements-design.md`
end to end and confirm each Goal and each item in the "Design" section maps
to a completed task above. List any gap found.

- [x] **Step 2: Full backend verification**

```bash
cd /home/weam/StockAI && uv run ruff format --check src tests scripts odoo
uv run ruff check src tests scripts odoo
uv run mypy
uv run pytest -q tests/unit
uv run pytest -q tests/integration
```

Expected: all PASS.

- [x] **Step 3: Full frontend verification**

```bash
cd /home/weam/StockAI/frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build
```

Expected: all PASS.

- [x] **Step 4: Manual browser verification**

```bash
cd /home/weam/StockAI && make compose-up
```

Sign in, trigger a manual scan (or run one more than once to build up
history across multiple scans), navigate to the home view, and visually
compare the rendered "Recent recommendations" panel and "Over-budget
exceptions" attention card against `home_page.png`. Confirm:
- The "Recent recommendations" panel lists cases (not scans), each row
  showing product name, `Scan #{scan_id} · Need by {date}`, an outcome
  badge, and an amount.
- Clicking a row navigates directly to that case's `RecommendationPage`
  (not through an intermediate `ScanDetailPage` view).
- The attention panel shows "Over-budget exceptions" as its third card.

Then:

```bash
cd /home/weam/StockAI && make compose-down
```

- [x] **Step 5: Fix any issues found**

If manual verification surfaces a real bug (matching the precedent set
during T27C's own final review, which caught a missing-polling bug this
way), fix it with its own failing-test-first cycle, then re-run Steps 2-4.

- [x] **Step 6: Commit any fixes from Step 5**

```bash
git add -A
git commit -m "fix: address issues found during manual home-page verification"
```

(Skip this step entirely if Step 5 found nothing to fix.)
