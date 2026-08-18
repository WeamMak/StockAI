# Home page enhancements — design

## Status

Approved by user 2026-08-18. Ready for implementation planning.

## Context

Three UI mockups (`home_page.png`, `Scan_details.png`, `recommendation_details.png`)
in the repo root describe a target look for the StockAI procurement frontend.
They were decomposed into three sub-projects, each with its own spec → plan →
implementation cycle:

1. **Scan-cardinality architecture** — complete. See `docs/superpowers/specs/
   2026-08-18-t27c-scan-cardinality-design.md` (task **T27C** in `docs/plan.md`).
2. **Recommendation-detail page restyle** — complete. See
   `docs/superpowers/specs/2026-08-17-recommendation-detail-restyle-design.md`.
3. **Home page enhancements** — this document.

This spec covers only sub-project 3: bringing `frontend/src/pages/OverviewPage.tsx`'s
home view (`view="home"`) closer to `home_page.png`. The "Scans" view
(`view="scans"`, the full scan list) is unaffected.

## Goals

- Add a **"Recent recommendations"** panel: the 5 most recent individual
  case results across all scans (not scoped to one scan), matching
  `home_page.png`'s left-hand panel.
- Add outcome icons to the existing **"Recent scans"** panel's rows, matching
  the mockup's per-row icon treatment.
- Change the **"What needs attention"** panel's three cards from
  (Needs review / Approval ready / In progress) to (Needs review /
  Approval ready / **Over-budget exceptions**), matching the mockup's exact
  3-card composition. "In progress" is dropped from this panel because it
  already has its own stat tile at the top of the page.

## Non-goals

- No week-over-week trend percentages ("↑18% vs last 7 days") anywhere on
  the page, and no "Insights (Last 7 days)" panel. DynamoDB here has no
  time-range aggregation; building one (or approximating it by fetching and
  bucketing a wide window of history client-side) is real added complexity
  for a metric only the mockup asks for. Deliberately omitted rather than
  half-built.
- No "View all recommendations" link or dedicated all-recommendations page.
  The panel shows its fixed 5 most recent rows with no further affordance;
  users can already reach any case's full evidence via the existing Scans
  tab → scan detail → case detail path.
- No changes to the "Scans" workspace view, `ScanDetailPage`, or
  `RecommendationPage` — those already match their mockups from prior
  sub-projects.
- No new `docs/plan.md` task number. Unlike T27C, this sub-project fills no
  named course-requirement gap — it is read-only UI surfaced over data the
  backend already persists (`RecommendationRecord.budget_status`, existing
  `CaseRecord`s reachable through the repository's already-implemented,
  currently-unused `list_cases()`). It stays a standalone spec → plan →
  implementation cycle under `docs/superpowers/`, the same as sub-project 2.
- No changes to the top stat-tile row's four counts (Total / In progress /
  Approval ready / Needs review) — only the trend arrows shown in the
  mockup next to them are out of scope (see above); the counts themselves
  are unchanged and already implemented.
- No new `Icon` glyphs. Row icons reuse the existing coarse `check`/`alert`/
  `document` set; per-outcome distinction (approval_ready vs manual_review
  vs no_valid_offer vs confirmed) is carried by the colored `status--{outcome}`
  badge text already established in `ScanDetailPage`, not by a distinct icon
  per outcome.

## Current state

`frontend/src/pages/OverviewPage.tsx` (home view) currently renders, top to
bottom: a page heading with "Run manual scan" button, a 4-card stat-summary
row (`Total` / `In progress` / `Approval ready` / `Needs review`, computed
client-side by `scanCounts()` from the already-fetched `listScans()`
result), and a two-panel grid: "Recent scans" (list of `ScanAggregate` rows)
and "What needs attention" (three cards: Needs review / Approval ready /
In progress, same counts as the stat-summary row).

Backend: `ScanService.list_scans()` returns `tuple[ScanAggregateSnapshot, ...]`,
each with `results: tuple[CaseSummary, ...]`. `CaseSummary`
(`ports/repositories.py`) currently has `case_id`, `product_id`,
`product_name`, `outcome`, `amount`, `need_by_date` — no `scan_id`,
`budget_status`, or `completed_at`. The repository's `list_cases()`
(`limit`, `cursor`, optional `scan_id` filter) already exists and, called
with no `scan_id`, already returns a bounded newest-first page of
`CaseRecord`s spanning every scan — but nothing in `ScanService` or the API
routes calls it. `RecommendationRecord` (persisted on `CaseRecord.result`)
already carries `budget_status` (`"within_budget"` / `"exception_required"`
/ `"unavailable"` / `"not_evaluated"`) and, when present, `product_id`/
`product_name` — all populated today for `approval_ready` results, defaulted
for the others.

## Design

### Backend

**`CaseSummary` (`ports/repositories.py`)** gains three fields:

```python
@dataclass(frozen=True, slots=True)
class CaseSummary:
    case_id: str
    product_id: str
    product_name: str
    outcome: str
    amount: Decimal | None
    need_by_date: date | None
    scan_id: str            # new
    budget_status: str      # new
    completed_at: UtcTimestamp | None  # new
```

`ScanService._summarize()` (used by `_run_case` for the per-scan results
list) is updated to populate the three new fields from data already in
scope there: `scan_id` (the method's existing parameter), `terminal.result.
budget_status if terminal.result is not None else "not_evaluated"`, and
`terminal.completed_at`.

A new static method, `ScanService._summarize_record(record: CaseRecord) ->
CaseSummary | None`, builds a `CaseSummary` directly from a persisted
`CaseRecord` with no `ReplenishmentCandidate` in hand (unlike `_summarize`,
which is only ever called mid-orchestration where the candidate is still
available):

- Returns `None` for `record.status == "skipped"` (mirrors `_run_case`'s
  existing exclusion of skipped cases from any summary list).
- `product_id`/`product_name` come from `record.result.product_id`/
  `product_name` when `record.result` is not `None` and those fields are
  set; otherwise from `record.evidence[0].product_id`/`product_name` when
  evidence exists; otherwise the record is excluded (returns `None`) —
  there is nothing meaningful to show.
- `outcome`/`amount`/`budget_status` follow the same `terminal.result`
  branching `_summarize` already uses (`"error"`/`None`/`"not_evaluated"`
  when `record.result is None`).
- `need_by_date` from the first evidence item's `shortage.need_by_date`,
  same lookup `_summarize` already does.
- `scan_id` from `record.case_id.value.split(":", 1)[0]` (the existing
  `{scan_id}:{product_id}` case-ID scheme T27C established).

A new method, `ScanService.list_recent_cases(*, limit: int) -> tuple[CaseSummary, ...]`,
calls `self._repository.list_cases(limit=limit)` (no `scan_id` filter) and
maps each record through `_summarize_record`, dropping `None`s. Since
`list_cases` already returns newest-first and skipped/undecodable records
are the only ones dropped, no additional sorting or over-fetching is
needed for a fixed `limit` of 5 in normal operation (skips are rare enough
in practice that under-filling by one or two rows on an unlucky page is an
acceptable, non-blocking cosmetic edge case for this MVP panel).

**API route** — `src/procurement/api/routes/scans.py`:

- `CaseSummaryResponse` gains `scan_id: str`, `budget_status: str`,
  `completed_at: datetime | None`.
- New route added to the existing cases router in `routes/cases.py`,
  alongside its `/{case_id}` route (no path collision — `/api/v1/cases` and
  `/api/v1/cases/{case_id}` are different path shapes regardless of
  declaration order): `GET /api/v1/cases?limit=5` → `RecentCasesResponse
  { cases: tuple[CaseSummaryResponse, ...] }`. `limit` is bounded
  server-side (1–20, default 5) the same way other list routes already
  bound their `limit` query parameter.

### Frontend

**`frontend/src/api/client.ts`**:

- `CaseSummary` interface gains `scan_id: string`, `budget_status: string`,
  `completed_at: string | null`; `parseCaseSummary` validates and maps the
  new fields (same pattern as every other field on that type).
- New `listRecentCases(options?: { limit?: number } & RequestOptions):
  Promise<CaseSummary[]>` calling `GET /api/v1/cases?limit=...` and parsing
  the `cases` array with the existing `parseCaseSummary`.

**`frontend/src/pages/OverviewPage.tsx`**:

- On mount, alongside the existing `listScans()` call, fetch
  `listRecentCases({ limit: 5 })` into new state (`recentCases`), with its
  own independent loading/error handling (a failure to load recent
  recommendations does not block the rest of the page — same
  fail-independently spirit as the existing `loadError`/`startError` split).
- New **"Recent recommendations"** panel, positioned as the first panel in
  the home-view grid (left of "Recent scans", matching the mockup), row
  markup per item: outcome icon, product name, `Scan #{scan_id} · {need_by_date
  formatted}`, colored outcome badge (reusing the `status status--{outcome}`
  class and `OUTCOME_LABEL` mapping already defined in `ScanDetailPage.tsx` —
  hoisted to a shared location, see below), and the amount (`formatCurrency`,
  or `—` when `null`). Clicking a row calls a new `onSelectCase(scanId:
  string, caseId: string)` prop.
- `OUTCOME_LABEL`/`OUTCOME_COLOR` (currently defined only inside
  `ScanDetailPage.tsx`) move to `frontend/src/presentation.ts` so both pages
  import the same mapping instead of duplicating it.
- **"Recent scans"** panel rows: add the coarse outcome icon (`check` for
  `outcomeClass(scan) === "approval"`, `alert` otherwise, `document` for
  in-progress) — this already exists as the `outcomeClass`/icon logic used
  elsewhere in the file; applying it to this list's rows is a direct reuse,
  not new logic.
- **"What needs attention"** panel: the third card changes from "In
  progress" (`counts.inProgress`) to "Over-budget exceptions", computed by
  a new `overBudgetCount(scans: ScanAggregate[])` helper that counts cases
  across all fetched scans' `results` where `budget_status ===
  "exception_required"` (no extra fetch — this reuses the `listScans()`
  data already on the page).

**`frontend/src/App.tsx`**: `OverviewPage` gains an `onSelectCase` prop.
When called (from a "Recent recommendations" row), `App` sets both
`selectedScanId` and `selectedCaseId` in one update, landing the user
directly on that case's `RecommendationPage` (its own "Back to scans"
button already returns to `ScanDetailPage` for that scan, which is the
correct destination — this requires no change to existing back-navigation).

### Styling

New rules added to `frontend/src/styles.css` for the "Recent recommendations"
panel's row layout (mirrors the existing `.scan-list`/`.scan-link` rules
closely enough to share most of the visual language) and the row icon.
Reuses existing `summary-icon`, `status`, and `panel` classes.

### Error handling

`listRecentCases()` failures are shown as a small inline notice inside the
"Recent recommendations" panel only (same `ApiError`-safe-message pattern
used elsewhere), leaving the rest of the home page (stat tiles, recent
scans, attention panel) fully functional. This mirrors the page's existing
principle that `loadError` (scans) and `startError` (manual scan) are
already independent, non-blocking states.

### Testing

- Backend: unit tests for `_summarize_record` (skip exclusion, product
  fields sourced from result vs. evidence fallback vs. fully-undecodable
  record, budget_status/completed_at/scan_id population) and
  `list_recent_cases` (empty, bounded limit, newest-first ordering,
  skip-filtering across a mixed page). Unit tests for the new route
  (`GET /api/v1/cases`) covering the response shape and `limit` bounds. One
  integration test extending the existing real-MCP-transport coverage to
  confirm a case created through a real scan is visible through
  `GET /api/v1/cases`.
- Frontend: `client.ts` parser tests for the three new `CaseSummary` fields
  and `listRecentCases`. `OverviewPage` tests for: the new panel's rows and
  empty state, `onSelectCase` being called with `(scanId, caseId)` on row
  click, the icon added to "Recent scans" rows, and the attention panel's
  "Over-budget exceptions" card replacing "In progress" (count computed
  correctly from mixed `budget_status` values across fetched scans).

## Open questions

None — all decisions in this document were confirmed with the user during
brainstorming on 2026-08-18.
