# Recommendation-detail page restyle — design

## Status

Approved by user 2026-08-17. Ready for implementation planning.

## Context

Three UI mockups (`home_page.png`, `Scan_details.png`, `recommendation_details.png`)
in the repo root describe a target look for the StockAI procurement frontend. They
bundle several independent pieces of work, so they were decomposed into separate
sub-projects, each with its own spec → plan → implementation cycle:

1. **Scan-cardinality architecture** — redesigning the LLM contract, `ScanState`,
   and the scan API response so one scan can produce multiple independent
   recommendations (matching `Scan_details.png`'s results table and outcome
   donut), instead of today's single-winner arbitration. Deferred; not started.
   T28/T29 placeholders (a `confirmed` outcome badge and PO reference/amount
   fields) belong to this sub-project's scope when it is specced, because that
   is where the mockups actually show them.
2. **Recommendation-detail page restyle** — this document.
3. **Home page enhancements** — trend deltas, an insights panel, an
   over-budget-exceptions card. Deferred; not started.

This spec covers only sub-project 2: restyling the existing recommendation
page to match `recommendation_details.png`.

## Goals

- Match `recommendation_details.png`'s layout: a 4-card stat header, an AI
  reasoning panel, side-by-side offer comparison cards, and always-visible
  budget/preferences panels.
- Apply the same visual treatment to all three outcome variants the backend
  can already return (`approval_ready` with `validation_level: "t27"`,
  `approval_ready` with `validation_level: "legacy"`, and `manual_review`),
  even though the mockup only depicts the first.

## Non-goals

- No change to LLM prompt, `agent/` reasoning logic, or the words the backend
  produces (`rationale`, `trade_offs`, `risk_flags`, `uncertainty`,
  `evidence_limitations` render exactly as returned today).
- No scan-cardinality or multi-recommendation backend work.
- No home page changes.
- No T28/T29 placeholders (confirmed outcome, PO fields, approve/create-draft
  actions) — none of these appear in `recommendation_details.png`, so none are
  added here. They belong to the scan-cardinality sub-project.
- No new routing framework — navigation stays the existing manual
  `selectedScanId` state pattern in `App.tsx`.
- No "View full evidence pack" button. The mockup shows one, but with no
  separate evidence-detail destination to link to (this page already shows
  the full evidence inline), it would be an inert placeholder. Deliberately
  omitted rather than shipping a button that does nothing.
- No "All data is secure and encrypted" footer note. Present in the mockup
  but not added — out of scope for this restyle; revisit if/when it's
  added consistently across pages rather than one-off here.

## Current state

`frontend/src/pages/ScanPage.tsx` polls `getScan`, then renders:

- `ScanHeading` — back button, eyebrow, title, scan ID with copy button.
- `RecommendationSummary` — branches on `scan.result.outcome`
  (`manual_review` vs `approval_ready`) and renders a 4-card "decision grid"
  (existing coverage / uncovered target gap / offer / recommendation) plus an
  inline reasoning panel.
- `ProcurementEvidence` (`frontend/src/components/ProcurementEvidence.tsx`) —
  iterates over **the entire `scan.evidence` array** (one entry per candidate
  the scan evaluated, not just the recommended one) and for each renders an
  inventory chart, an offer list (eligible offers shown, rejected offers
  hidden behind a `<details>` disclosure), and a `<details>` sidebar with
  budget calculation and `AppliedPreferences`.

## Design

### File structure

- Rename `frontend/src/pages/ScanPage.tsx` → `frontend/src/pages/RecommendationPage.tsx`;
  rename the exported component `ScanPage` → `RecommendationPage`. Update the
  import and JSX usage in `frontend/src/App.tsx` (no behavior change to the
  `selectedScanId` state machine, only the import/component name).
- New components under `frontend/src/components/`:
  - `RecommendationHeader.tsx`
  - `OfferComparison.tsx`
  - `BudgetPanel.tsx`
- Unchanged, reused as-is: `AppliedPreferences.tsx`, `InventoryChart.tsx`,
  `Icon.tsx`.
- `ProcurementEvidence.tsx` keeps its name and role (rendering one evidence
  record's detail: inventory chart + offers + budget + preferences) but
  shrinks to composing the new sub-components, and always receives exactly
  one evidence item instead of an array.

### Data flow

`RecommendationPage` computes the recommended evidence record once, after the
scan loads:

```ts
const recommendedEvidence =
  scan.result && "product_id" in scan.result
    ? scan.evidence.find((e) => e.product_id === scan.result.product_id) ?? null
    : null;
```

This covers `approval_ready` (both `t27` and `legacy` validation levels,
which both carry `product_id`). `manual_review` has no `product_id` on the
result type, so `recommendedEvidence` is `null` for that outcome — in that
case `ProcurementEvidence` is not rendered at all (matches today's actual
behavior: `RecommendationSummary` already returns early with just the
manual-review rationale/uncertainty text and no evidence section for that
outcome).

### Component design

**`RecommendationHeader`**

Props: `outcome: ScanResult["outcome"]`, `validationLevel` (`"t27" | "legacy" | undefined`,
undefined for manual_review), `productName: string`, `productId: string`,
`evidence: ProcurementEvidence | null`, `result: ScanResult`.

Renders the existing top badge (Approval ready / Historical recommendation /
Manual review — reusing today's `isLegacy` logic) and title, then a 4-card
row:

| Card | Source |
|---|---|
| Offers considered | `evidence.offers` — eligible count / total count |
| Uncovered target gap | `evidence.coverage.residual_quantity` + `evidence.shortage.need_by_date` |
| Recommended vendor | selected offer's `vendor_name` + `normalized_cost` (via `result.offer_id` lookup in `evidence.offers`) |
| Budget status | `evidence.budget.exception_required` → "Within budget" / "Exception required"; `evidence.budget.remaining_after` as the subtext |

If `evidence` is `null` (manual_review), the 4-card row is omitted; only the
badge/title renders, same as today.

**`OfferComparison`**

Props: `offers: OfferEvidence[]`, `selectedOfferId: string | null`.

- Sorts `offers` so the offer matching `selectedOfferId` is first (if any).
- Renders the first 3 as cards in a row. The selected offer's card gets a
  highlighted border and an "AI selected" badge.
- Each card shows vendor name, an eligibility badge (`eligible` /
  `rejected` — reuse `reason_codes` for a rejected-reason subtext, e.g.
  "Vendor not approved" when `reason_codes` includes that code, mirroring the
  mockup's "NOT ELIGIBLE" / "Vendor not approved" treatment), price, quantity,
  delivery date, on-time rate, and completed-order count.
- If `offers.length > 3`, the remaining offers render inside a
  `<details>` "View N more offers" disclosure below the row, using the same
  card markup.

**`BudgetPanel`**

Props: `budget: ProcurementEvidence["budget"]` (nullable).

Always-visible panel (no `<details>` wrapper) showing budget/committed/proposed/
remaining-after amounts and the exception-required warning line, ported
directly from today's disclosure content. Renders nothing if `budget` is
`null` (matches today's conditional).

**`AppliedPreferences`** — unchanged component, now rendered inside an
always-visible panel wrapper instead of inside a `<details>` element.

### Outcome variants

- **`approval_ready`, `validation_level: "t27"`**: full treatment as designed
  above — header with 4 cards, reasoning panel, offer comparison, budget
  panel, preferences panel.
- **`approval_ready`, `validation_level: "legacy"`**: same layout, but the
  result type lacks `offer_id`. The "Recommended vendor" header card renders
  a neutral "Not available" state since there is no offer to look up
  (legacy records predate T27's structured offer-level fields). "Budget
  status" is not similarly forced to "Not available" — it reflects whatever
  `evidence.budget` actually contains when evidence exists for that product,
  since budget data comes from the evidence record, not from the result
  type, and hiding real data there would be less accurate, not more. Badge
  reads "Historical recommendation" / reasoning panel reads "Historical
  reasoning" / "Predates T27 validation", reusing today's exact copy.
- **`manual_review`**: header renders badge + title only (no 4-card row, no
  evidence section, as `recommendedEvidence` is `null`). Reasoning panel
  still renders with `rationale`, `trade_offs`, `risk_flags`, `uncertainty`,
  `evidence_limitations` — same fields, restyled to match the visual language
  (cards/spacing/badges) of the other two variants.

### Styling

New rules added to `frontend/src/styles.css`: stat-card row layout, offer
comparison card grid, selected-offer highlight border/badge, collapsed-offers
disclosure, and panel styling for the budget/preferences sections (replacing
disclosure-chevron styling with plain panel styling). Reuses existing
`summary-icon`, `status`, and `panel` classes rather than introducing a
parallel styling system, so the rest of the app (home page, scan list, which
are not changing in this sub-project) stays visually consistent.

### Error handling

Unchanged. `RecommendationPage` keeps today's loading skeleton, polling
logic, queued/running status view, and `ErrorState` handling verbatim — only
the succeeded-with-`result` render path changes.

### Testing

Existing tests in `frontend/tests` that cover `ScanPage` are updated for the
rename and new component structure, following the existing React Testing
Library patterns in that directory. New focused tests are added for
`RecommendationHeader`, `OfferComparison` (offer sorting/capping/disclosure
behavior), and `BudgetPanel`. No Python/backend tests are affected.

## Open questions

None — all decisions in this document were confirmed with the user during
brainstorming on 2026-08-17.
