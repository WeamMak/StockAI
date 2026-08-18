# Recommendation-Detail Page Restyle Implementation Plan

> **For agentic workers:** The `superpowers:subagent-driven-development` and
> `superpowers:executing-plans` sub-skills referenced by the standard
> writing-plans template are not present in this repository's skill set.
> Execute tasks in this session sequentially, one task at a time, running
> each task's verification steps before moving to the next. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the existing scan/recommendation page to match
`recommendation_details.png`: a 4-card stat header, side-by-side offer
comparison cards, and always-visible budget/preferences panels, applied
consistently across all three outcome variants the backend can return.

**Architecture:** Rename `ScanPage.tsx` to `RecommendationPage.tsx` (it
already only ever renders one recommendation). Extract three new focused
components (`RecommendationHeader`, `OfferComparison`, `BudgetPanel`) out of
the current `RecommendationSummary` function and `ProcurementEvidence.tsx`.
Filter `scan.evidence` down to the one recommended product before rendering
evidence, instead of iterating the full array.

**Tech Stack:** React 18 + TypeScript, Vite, Vitest + React Testing Library
+ `@testing-library/user-event`.

## Global Constraints

- No changes to `src/procurement` (backend) — this is a frontend-only plan.
- No changes to LLM prompt or the wording the backend returns
  (`rationale`, `trade_offs`, `risk_flags`, `uncertainty`,
  `evidence_limitations` render exactly as returned).
- No new outcome types, no PO/confirmed fields, no approve/reject/create-draft
  UI — out of scope per the approved spec.
- Reuse existing CSS custom properties (`--accent`, `--accent-soft`,
  `--border`, `--ink`, `--muted`) and existing classes (`panel`,
  `summary-icon`, `status`, `decision-grid` naming pattern) rather than
  introducing a parallel styling system.
- Run `npm run typecheck`, `npm run lint`, and `npm test` (from
  `frontend/`) after every task; all three must pass before committing.
- Spec reference: `docs/superpowers/specs/2026-08-17-recommendation-detail-restyle-design.md`

---

## Task 1: Rename ScanPage to RecommendationPage

**Files:**
- Create: `frontend/src/pages/RecommendationPage.tsx` (moved from `ScanPage.tsx`)
- Delete: `frontend/src/pages/ScanPage.tsx`
- Modify: `frontend/src/App.tsx:6` (import), `frontend/src/App.tsx:87` (usage)
- Create: `frontend/tests/recommendation.test.tsx` (moved from `scan.test.tsx`)
- Delete: `frontend/tests/scan.test.tsx`

**Interfaces:**
- Produces: `RecommendationPage` component with the exact same props as
  today's `ScanPage` (`scanId: string`, `onBack: () => void`,
  `pollIntervalMs?: number`, `maxPollAttempts?: number`).

This task is a pure rename — no behavior changes. It establishes the new
file identity before Tasks 2–7 modify its internals.

- [ ] **Step 1: Copy `ScanPage.tsx` to `RecommendationPage.tsx` with renamed identifiers**

Copy the full current contents of `frontend/src/pages/ScanPage.tsx` into a
new file `frontend/src/pages/RecommendationPage.tsx`, then apply these
renames throughout the new file:
- `interface ScanPageProps` → `interface RecommendationPageProps`
- `export function ScanPage(` → `export function RecommendationPage(`
- Function body and JSX otherwise unchanged in this step.

- [ ] **Step 2: Delete the old file**

```bash
rm frontend/src/pages/ScanPage.tsx
```

- [ ] **Step 3: Update `App.tsx` import and usage**

In `frontend/src/App.tsx`, change line 6:

```tsx
import { ScanPage } from "./pages/ScanPage";
```

to:

```tsx
import { RecommendationPage } from "./pages/RecommendationPage";
```

And change line 87 (inside the ternary that renders the selected scan):

```tsx
<ScanPage
  scanId={selectedScanId}
  onBack={() => {
    setSelectedScanId(null);
    setWorkspacePage("scans");
  }}
/>
```

to:

```tsx
<RecommendationPage
  scanId={selectedScanId}
  onBack={() => {
    setSelectedScanId(null);
    setWorkspacePage("scans");
  }}
/>
```

- [ ] **Step 4: Copy the test file with renamed import**

Copy `frontend/tests/scan.test.tsx` to `frontend/tests/recommendation.test.tsx`,
changing only line 5:

```tsx
import { ScanPage } from "../src/pages/ScanPage";
```

to:

```tsx
import { RecommendationPage } from "../src/pages/RecommendationPage";
```

and every `<ScanPage ...>` usage in the file to `<RecommendationPage ...>`
(there are 8 occurrences — one per `it(...)` block that renders the
component). Keep every other line, including all `describe`/`it` names and
assertions, byte-for-byte identical in this step.

```bash
rm frontend/tests/scan.test.tsx
```

- [ ] **Step 5: Run the full suite to verify the rename introduced no regressions**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: all pass, identical results to before the rename (same test
count, same assertions, just running under the new file/component names).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/RecommendationPage.tsx frontend/src/App.tsx \
  frontend/tests/recommendation.test.tsx
git rm frontend/src/pages/ScanPage.tsx frontend/tests/scan.test.tsx
git commit -m "refactor(frontend): rename ScanPage to RecommendationPage"
```

---

## Task 2: Filter evidence to the recommended product

**Files:**
- Modify: `frontend/src/pages/RecommendationPage.tsx` (the `RecommendationPage`
  function's render branch for `scan.result`)
- Modify: `frontend/src/components/ProcurementEvidence.tsx` (prop type:
  `evidence: Evidence[]` → `evidence: Evidence`)
- Modify: `frontend/tests/recommendation.test.tsx`

**Interfaces:**
- Consumes: `Scan.evidence: ProcurementEvidence[]`, `Scan.result: ScanResult | null`
  from `frontend/src/api/client.ts` (unchanged).
- Produces: `ProcurementEvidence` component now takes
  `evidence: Evidence` (singular, required) instead of `evidence: Evidence[]`.
  Callers must filter before passing.

- [ ] **Step 1: Write the failing test**

Add this test to `frontend/tests/recommendation.test.tsx`, inside the
`describe("RecommendationPage", ...)` block (rename the `describe` title
from `"ScanPage"` to `"RecommendationPage"` while adding this):

```tsx
it("shows evidence only for the recommended product, not every evaluated candidate", async () => {
  const otherCandidateEvidence = {
    ...BASE_SCAN.evidence[0],
    evidence_id: "dev:evidence-product-999",
    product_id: "product-999",
    product_name: "Fictional Other Candidate",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      jsonResponse({
        ...BASE_SCAN,
        evidence: [otherCandidateEvidence, BASE_SCAN.evidence[0]],
      }),
    ),
  );

  render(<RecommendationPage scanId="scan-101" onBack={vi.fn()} />);

  expect(
    await screen.findByRole("heading", { name: "Deterministic procurement evidence" }),
  ).toBeInTheDocument();
  expect(screen.getByText("dev:evidence-product-101")).toBeInTheDocument();
  expect(screen.queryByText("Fictional Other Candidate")).not.toBeInTheDocument();
  expect(screen.queryByText("dev:evidence-product-999")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run tests/recommendation.test.tsx -t "shows evidence only for the recommended product"
```

Expected: FAIL — today's code renders both evidence records (one
`evidence-record` per array item), so `"Fictional Other Candidate"` is
present in the document.

- [ ] **Step 3: Replace `ProcurementEvidence.tsx` with a single-item version**

Every reference in the component body renames from the array-item variable
`item` to the prop name `evidence` directly (no aliasing), so this and all
later tasks (5, 6, 7) consistently use `evidence.<field>`. Replace the
entire contents of `frontend/src/components/ProcurementEvidence.tsx` with:

```tsx
import type {
  OfferEvidence,
  ProcurementEvidence as Evidence,
} from "../api/client";
import {
  formatCurrency,
  formatDate,
  formatDateTime,
  formatNumber,
  formatQuantity,
  formatRatioPercent,
} from "../presentation";
import { AppliedPreferences } from "./AppliedPreferences";
import { InventoryChart } from "./InventoryChart";

function label(code: string) {
  return code.replaceAll("_", " ").toLowerCase();
}

function OfferList({
  accessibleName,
  offers,
  onlyEligible = false,
}: {
  accessibleName: string;
  offers: OfferEvidence[];
  onlyEligible?: boolean;
}) {
  return (
    <ul className="offer-list" aria-label={accessibleName}>
      {offers.map((offer) => (
        <li key={offer.offer_id}>
          <div className="offer-heading">
            <strong>{offer.vendor_name}</strong>
            <span
              className={`status status--${offer.status === "eligible" ? "succeeded" : "failed"}`}
            >
              {onlyEligible ? "Only eligible offer" : offer.status}
            </span>
          </div>
          <dl className="offer-metrics">
            <div><dt>Price</dt><dd title={offer.normalized_cost}>{formatCurrency(offer.normalized_cost, offer.company_currency)}</dd></div>
            <div><dt>Quantity</dt><dd>{formatQuantity(offer.quantity)}</dd></div>
            <div><dt>Delivery</dt><dd>{formatDate(offer.delivery_date)}</dd></div>
          </dl>
          <p className="offer-history">
            On-time: {formatRatioPercent(offer.performance.on_time_rate)} ·{" "}
            {offer.performance.completed_order_count} completed orders ·{" "}
            {offer.performance.history_status} history
          </p>
          {offer.reason_codes.length > 0 ? (
            <p className="rejection-reason">
              {offer.reason_codes.map(label).join(", ")}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function ProcurementEvidence({ evidence }: { evidence: Evidence }) {
  return (
    <section aria-labelledby="evidence-title" className="panel evidence-panel">
      <h2 id="evidence-title">Deterministic procurement evidence</h2>
      <p className="section-intro">
        Grounded facts, calculations, and policy configuration used to arrive
        at this recommendation.
      </p>
      <article className="evidence-record">
        <div className="result-heading">
          <div>
            <h3>{evidence.product_name}</h3>
            <p className="muted identifier">{evidence.evidence_id}</p>
          </div>
          {evidence.skip_reason_code ? (
            <span className="status status--failed">
              Skipped: {label(evidence.skip_reason_code)}
            </span>
          ) : (
            <span className="status status--succeeded">Eligible</span>
          )}
        </div>

        <div className="evidence-detail-grid" role="region" aria-label="Evidence details">
        <div className="evidence-main">
        <dl className="evidence-grid evidence-overview">
          <div>
            <dt>Reorder trigger</dt>
            <dd title={evidence.shortage.reorder_trigger_date ?? undefined}>
              {formatDate(evidence.shortage.reorder_trigger_date)}
            </dd>
          </div>
          <div>
            <dt>Need by</dt>
            <dd title={evidence.shortage.need_by_date}>
              {formatDate(evidence.shortage.need_by_date)}
            </dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>
              {label(evidence.coverage.status)} ·{" "}
              {formatQuantity(evidence.coverage.covered_quantity)} covered ·{" "}
              {formatQuantity(evidence.coverage.residual_quantity)} residual
            </dd>
          </div>
        </dl>

        <div className="evidence-primary-grid">
          <section className="evidence-card" aria-labelledby={`inventory-${evidence.evidence_id}`}>
            <div className="evidence-card__heading">
              <div>
                <h4 id={`inventory-${evidence.evidence_id}`}>Inventory projection</h4>
                <p>{formatDate(evidence.shortage.horizon_start)} – {formatDate(evidence.shortage.horizon_end)}</p>
              </div>
              <span className="evidence-count">{evidence.shortage.timeline.length} days</span>
            </div>
            <InventoryChart
              reorderMinimum={evidence.shortage.reorder_minimum}
              timeline={evidence.shortage.timeline}
            />
            <details className="compact-disclosure">
              <summary>View daily values</summary>
              <div className="table-scroll">
              <table className="evidence-timeline">
                <caption>14-day inventory projection</caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Projected</th>
                  </tr>
                </thead>
                <tbody>
                  {evidence.shortage.timeline.map((day) => (
                    <tr key={day.projection_date}>
                      <td title={day.projection_date}>
                        {formatDate(day.projection_date)}
                      </td>
                      <td title={day.quantity}>{formatNumber(day.quantity)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </details>
          </section>

          <section className="evidence-card" aria-labelledby={`offers-${evidence.evidence_id}`}>
            <div className="evidence-card__heading">
              <div>
                <h4 id={`offers-${evidence.evidence_id}`}>Vendor offers</h4>
                <p>Eligible offers are shown first.</p>
              </div>
              <span className="evidence-count">{evidence.offers.length} considered</span>
            </div>
            {evidence.offers.filter((offer) => offer.status === "eligible").length === 0 ? (
                <p>No valid vendor offer evidence was available.</p>
              ) : (
                <OfferList
                  accessibleName={`${evidence.product_name} eligible offers`}
                  offers={evidence.offers.filter((offer) => offer.status === "eligible")}
                  onlyEligible={evidence.offers.filter((offer) => offer.status === "eligible").length === 1}
                />
              )}
            {evidence.offers.some((offer) => offer.status === "rejected") ? (
              <details className="compact-disclosure rejected-offers">
                <summary>
                  View rejected offers ({evidence.offers.filter((offer) => offer.status === "rejected").length})
                </summary>
                <OfferList
                  accessibleName={`${evidence.product_name} rejected offers`}
                  offers={evidence.offers.filter((offer) => offer.status === "rejected")}
                />
              </details>
            ) : null}
          </section>
        </div>
        </div>

        <aside className="evidence-disclosures" aria-label="Evidence policy details">

          {evidence.budget ? (
            <details className="disclosure" open>
              <summary>
                <span>Budget calculation</span>
                <small>
                  {evidence.budget.exception_required
                    ? "Exception required"
                    : "Within budget"}
                </small>
              </summary>
              <div className="disclosure__content">
                <dl className="evidence-grid">
                  <div>
                    <dt>Budget</dt>
                    <dd>{formatCurrency(evidence.budget.budget_amount, evidence.budget.currency)}</dd>
                  </div>
                  <div>
                    <dt>Committed</dt>
                    <dd>{formatCurrency(evidence.budget.confirmed_commitment, evidence.budget.currency)}</dd>
                  </div>
                  <div>
                    <dt>Proposed</dt>
                    <dd>{formatCurrency(evidence.budget.proposed_amount, evidence.budget.currency)}</dd>
                  </div>
                  <div>
                    <dt>Remaining after</dt>
                    <dd>{formatCurrency(evidence.budget.remaining_after, evidence.budget.currency)}</dd>
                  </div>
                </dl>
                {evidence.budget.exception_required ? (
                  <p className="budget-warning">
                    Manager exception required for{" "}
                    {formatCurrency(evidence.budget.overage, evidence.budget.currency)}{" "}
                    overage.
                  </p>
                ) : null}
              </div>
            </details>
          ) : null}

          {evidence.preferences ? (
            <details className="disclosure" open>
              <summary>
                <span>Applied preferences</span>
                <small>Revision {evidence.preferences.revision}</small>
              </summary>
              <div className="disclosure__content">
                <AppliedPreferences preferences={evidence.preferences} />
              </div>
            </details>
          ) : null}
        </aside>
        </div>
        <footer className="evidence-footer">
          <span className="identifier">Evidence ID: {evidence.evidence_id}</span>
          <span>Captured {formatDateTime(evidence.captured_at)}</span>
        </footer>
      </article>
    </section>
  );
}
```

This keeps the eligible/rejected `OfferList` split and the disclosure-wrapped
budget/preferences sections unchanged for now — Tasks 5, 6, and 7 replace
those sections individually in later steps, each still using `evidence.`
consistently.

- [ ] **Step 4: Compute and pass the filtered evidence in `RecommendationPage.tsx`**

In `frontend/src/pages/RecommendationPage.tsx`, inside the `RecommendationPage`
function's return statement, find:

```tsx
      ) : scan.result ? (
        <>
          <RecommendationSummary scan={scan} />
          <ProcurementEvidence evidence={scan.evidence} />
        </>
      ) : (
```

Replace with:

```tsx
      ) : scan.result ? (
        <>
          <RecommendationSummary
            scan={scan}
            evidence={findRecommendedEvidence(scan)}
          />
          {findRecommendedEvidence(scan) ? (
            <ProcurementEvidence evidence={findRecommendedEvidence(scan)!} />
          ) : null}
        </>
      ) : (
```

Add this helper function above the `RecommendationSummary` function
definition:

```tsx
function findRecommendedEvidence(scan: Scan): ProcurementEvidenceRecord | null {
  const result = scan.result;
  if (result === null || result.outcome !== "approval_ready") {
    return null;
  }
  return scan.evidence.find((item) => item.product_id === result.product_id) ?? null;
}
```

Add `ProcurementEvidence as ProcurementEvidenceRecord` to the existing
type-only import from `../api/client` at the top of the file (renamed to
avoid colliding with the `ProcurementEvidence` component import):

```tsx
import {
  ApiError,
  getScan,
  isAbortError,
  type ProcurementEvidence as ProcurementEvidenceRecord,
  type Scan,
  type ScanFailure,
} from "../api/client";
```

- [ ] **Step 5: Update `RecommendationSummary` to accept and pass through `evidence`**

In `frontend/src/pages/RecommendationPage.tsx`, change the
`RecommendationSummary` function signature from
`function RecommendationSummary({ scan }: { scan: Scan }) {` to:

```tsx
function RecommendationSummary({
  scan,
  evidence,
}: {
  scan: Scan;
  evidence: ProcurementEvidenceRecord | null;
}) {
```

Inside the function body, remove the existing local computation:

```tsx
  const evidence = scan.evidence.find(
    (item) => item.product_id === result.product_id,
  );
```

(now received as a prop instead) and update every reference from `evidence`
staying the same name (no further renames needed at call sites within this
function, since the prop name matches).

- [ ] **Step 6: Run test to verify it passes**

```bash
cd frontend && npx vitest run tests/recommendation.test.tsx
```

Expected: PASS — all tests including the new one.

- [ ] **Step 7: Run full verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/RecommendationPage.tsx \
  frontend/src/components/ProcurementEvidence.tsx \
  frontend/tests/recommendation.test.tsx
git commit -m "feat(frontend): filter recommendation evidence to the recommended product"
```

---

## Task 3: Extract RecommendationHeader with the mockup's 4-card stat row

**Files:**
- Create: `frontend/src/components/RecommendationHeader.tsx`
- Create: `frontend/tests/recommendation-header.test.tsx`
- Modify: `frontend/src/pages/RecommendationPage.tsx`
- Modify: `frontend/tests/recommendation.test.tsx` (update assertions that
  reference the old decision-grid card labels)
- Modify: `frontend/src/styles.css` (add stat-card row styling)

**Interfaces:**
- Produces: `RecommendationHeader({ result, evidence }): JSX.Element` —
  `result: ScanResult`, `evidence: ProcurementEvidenceRecord | null`.
  Renders the outcome badge, title, and (when `evidence` is non-null) the
  4-card stat row: Offers considered / Uncovered target gap / Recommended
  vendor / Budget status.
- Consumes: `formatCurrency`, `formatDate`, `formatQuantity` from
  `../presentation`; `Icon` from `./Icon`.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/recommendation-header.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecommendationHeader } from "../src/components/RecommendationHeader";

const EVIDENCE = {
  environment: "dev" as const,
  evidence_id: "dev:evidence-product-101",
  product_id: "product-101",
  product_name: "Fictional Safety Gloves",
  category_id: "category-safety",
  captured_at: "2026-08-05T10:00:01Z",
  shortage: {
    horizon_start: "2026-08-05",
    horizon_end: "2026-08-19",
    reorder_trigger_date: "2026-08-08",
    need_by_date: "2026-08-12",
    reorder_minimum: "10.000000",
    reorder_maximum: "40.000000",
    minimum_projected_quantity: "0.000000",
    timeline: [],
  },
  coverage: {
    status: "partial" as const,
    covered_quantity: "5.000000",
    residual_quantity: "35.000000",
    source_count: 1,
  },
  offers: [
    {
      offer_id: "offer-101",
      vendor_id: "vendor-101",
      vendor_name: "Fictional Approved Supplies",
      status: "eligible" as const,
      reason_codes: [],
      currency: "USD",
      unit_price: "12.500000",
      company_currency: "USD",
      normalized_unit_price: "12.500000",
      delivery_date: "2026-08-10",
      quantity: "35.000000",
      normalized_cost: "437.500000",
      projected_inventory_after_receipt: "40.000000",
      excess_inventory: "0.000000",
      performance: {
        completed_order_count: 2,
        on_time_rate: "0.500000",
        history_status: "limited" as const,
      },
    },
  ],
  budget: {
    period_start: "2026-08-01",
    currency: "USD",
    budget_amount: "5000.000000",
    confirmed_commitment: "160.000000",
    proposed_amount: "437.500000",
    remaining_before: "4840.000000",
    remaining_after: "4402.500000",
    overage: "0.000000",
    exception_required: false,
  },
  skip_reason_code: null,
  preferences: null,
};

const RESULT = {
  outcome: "approval_ready" as const,
  validation_level: "t27" as const,
  product_id: "product-101",
  product_name: "Fictional Safety Gloves",
  offer_id: "offer-101",
  rationale: "Projected stock is below the reorder minimum.",
  trade_offs: [],
  risk_flags: [],
  uncertainty: "",
  evidence_limitations: [],
  evidence_digest: `sha256:${"a".repeat(64)}`,
  quantity: "35.000000",
  unit_price: "12.500000",
  normalized_cost: "437.500000",
  budget_status: "within_budget" as const,
  preference_profile_id: "preference-3",
  preference_scope: "product" as const,
  preference_revision: 6,
  priority_order: ["price", "reliability", "delivery"] as const,
  premium_outcome: "within_cap" as const,
  read_only: true as const,
};

describe("RecommendationHeader", () => {
  it("shows the mockup's 4-card stat row for an approval-ready result", () => {
    render(<RecommendationHeader result={RESULT} evidence={EVIDENCE} />);

    const highlights = screen.getByRole("region", { name: "Decision highlights" });
    expect(highlights).toHaveTextContent("Offers considered1 eligible1 total reviewed");
    expect(highlights).toHaveTextContent("Uncovered target gap35 unitsAt Aug 12, 2026 stockout");
    expect(highlights).toHaveTextContent("Recommended vendorFictional Approved Supplies$437.50");
    expect(highlights).toHaveTextContent("Budget statusWithin budget$4,402.50 remaining");
  });

  it("omits the stat row when no evidence is available", () => {
    render(<RecommendationHeader result={RESULT} evidence={null} />);

    expect(
      screen.queryByRole("region", { name: "Decision highlights" }),
    ).not.toBeInTheDocument();
  });

  it("shows the manual-review badge and fallback title", () => {
    render(
      <RecommendationHeader
        result={{
          outcome: "manual_review",
          rationale: "x",
          trade_offs: [],
          risk_flags: [],
          uncertainty: "x",
          evidence_limitations: [],
          read_only: true,
        }}
        evidence={null}
      />,
    );

    expect(screen.getByText("Manual review")).toBeInTheDocument();
    expect(screen.getByText("No draft created")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Compare eligible offers" }),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run tests/recommendation-header.test.tsx
```

Expected: FAIL — `frontend/src/components/RecommendationHeader.tsx` does not
exist yet.

- [ ] **Step 3: Create `RecommendationHeader.tsx`**

```tsx
import type {
  ProcurementEvidence as Evidence,
  ScanResult,
} from "../api/client";
import { formatCurrency, formatDate, formatQuantity } from "../presentation";
import { Icon } from "./Icon";

function badgeCopy(result: ScanResult): { label: string; readOnly: string } {
  if (result.outcome === "manual_review") {
    return { label: "Manual review", readOnly: "No draft created" };
  }
  if (result.validation_level === "legacy") {
    return {
      label: "Historical recommendation",
      readOnly: "Read-only recommendation",
    };
  }
  return { label: "Approval ready", readOnly: "Read-only recommendation" };
}

function titleCopy(result: ScanResult): {
  title: string;
  subtitle: string | null;
} {
  if (result.outcome === "manual_review") {
    return { title: "Compare eligible offers", subtitle: null };
  }
  return { title: result.product_name, subtitle: result.product_id };
}

export function RecommendationHeader({
  result,
  evidence,
}: {
  result: ScanResult;
  evidence: Evidence | null;
}) {
  const { label, readOnly } = badgeCopy(result);
  const { title, subtitle } = titleCopy(result);
  const selectedOffer =
    evidence && result.outcome === "approval_ready" && result.offer_id !== null
      ? evidence.offers.find((offer) => offer.offer_id === result.offer_id) ??
        null
      : null;
  const eligibleCount =
    evidence?.offers.filter((offer) => offer.status === "eligible").length ?? 0;
  const totalCount = evidence?.offers.length ?? 0;

  return (
    <div className="recommendation-overview">
      <div className="result-heading">
        <div>
          <p className="approval-label">
            <span className="summary-icon summary-icon--green">
              <Icon name="coverage" />
            </span>
            {label}
          </p>
          <h2>{title}</h2>
          {subtitle ? <p className="muted identifier">{subtitle}</p> : null}
        </div>
        <span className="read-only-badge">{readOnly}</span>
      </div>

      {evidence ? (
        <section aria-label="Decision highlights">
          <dl className="decision-grid">
            <div className="decision-card decision-card--offers">
              <dt>
                <span className="summary-icon summary-icon--blue">
                  <Icon name="offer" />
                </span>
                Offers considered
              </dt>
              <dd>
                {eligibleCount} eligible
                <small>{totalCount} total reviewed</small>
              </dd>
            </div>
            <div className="decision-card decision-card--shortage">
              <dt>
                <span className="summary-icon summary-icon--amber">
                  <Icon name="shortage" />
                </span>
                Uncovered target gap
              </dt>
              <dd title={evidence.coverage.residual_quantity}>
                {formatQuantity(evidence.coverage.residual_quantity)}
                <small>
                  At {formatDate(evidence.shortage.need_by_date)} stockout
                </small>
              </dd>
            </div>
            <div className="decision-card decision-card--vendor">
              <dt>
                <span className="summary-icon summary-icon--green">
                  <Icon name="recommendation" />
                </span>
                Recommended vendor
              </dt>
              <dd>
                {selectedOffer ? selectedOffer.vendor_name : "Not available"}
                <small>
                  {selectedOffer
                    ? formatCurrency(
                        selectedOffer.normalized_cost,
                        selectedOffer.company_currency,
                      )
                    : "No offer selected"}
                </small>
              </dd>
            </div>
            <div className="decision-card decision-card--budget">
              <dt>
                <span className="summary-icon summary-icon--blue">
                  <Icon name="document" />
                </span>
                Budget status
              </dt>
              <dd>
                {evidence.budget
                  ? evidence.budget.exception_required
                    ? "Exception required"
                    : "Within budget"
                  : "Not available"}
                <small>
                  {evidence.budget
                    ? `${formatCurrency(evidence.budget.remaining_after, evidence.budget.currency)} remaining`
                    : "Budget not available"}
                </small>
              </dd>
            </div>
          </dl>
        </section>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run tests/recommendation-header.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Wire `RecommendationHeader` into `RecommendationPage.tsx`**

In `frontend/src/pages/RecommendationPage.tsx`, inside `RecommendationSummary`,
replace the entire block from `<div className="recommendation-overview">`
through its matching closing `</div>` (the badge/title/decision-grid block
described in the file's current lines ~157–222) with:

```tsx
        <RecommendationHeader result={result} evidence={evidence} />
```

Add the import at the top of the file:

```tsx
import { RecommendationHeader } from "../components/RecommendationHeader";
```

Remove the now-unused `isLegacy` computation duplication if any remains
outside `RecommendationHeader`'s own copy of that logic — keep the
`isLegacy` variable used later for the "AI reasoning" vs "Historical
reasoning" heading, since that is a separate concern from the badge.

- [ ] **Step 6: Update `recommendation.test.tsx`'s decision-grid assertions**

In `frontend/tests/recommendation.test.tsx`, replace the test
`"shows truthful icon-card highlights and risk status"` assertions for the
highlights region (the four `expect(highlights).toHaveTextContent(...)`
lines) with:

```tsx
    expect(highlights).toHaveTextContent("Offers considered1 eligible1 total reviewed");
    expect(highlights).toHaveTextContent(
      "Uncovered target gap35 unitsAt Aug 12, 2026 stockout",
    );
    expect(highlights).toHaveTextContent(
      "Recommended vendorFictional Approved Supplies$437.50",
    );
    expect(highlights).toHaveTextContent("Budget statusWithin budget$4,402.50 remaining");
```

- [ ] **Step 7: Run full verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/RecommendationHeader.tsx \
  frontend/src/pages/RecommendationPage.tsx \
  frontend/tests/recommendation-header.test.tsx \
  frontend/tests/recommendation.test.tsx
git commit -m "feat(frontend): add RecommendationHeader with mockup stat cards"
```

- [ ] **Step 9: Add stat-card row CSS**

The `RecommendationHeader` reuses the existing `.decision-grid` class
(already styled at `frontend/src/styles.css:813`), so no new base grid CSS
is required. Add modifier styling for the new card identities by appending
to `frontend/src/styles.css` immediately after the existing
`.decision-card--offer dd small` rule (around line 868):

```css
.decision-card--offers dd small,
.decision-card--vendor dd small {
  color: #3450a3;
}

.decision-card--budget dd small {
  color: #36724d;
}
```

- [ ] **Step 10: Run full verification and commit the CSS**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
git add frontend/src/styles.css
git commit -m "style(frontend): color the recommendation header stat cards"
```

---

## Task 4: Unify manual_review rendering into the shared header + reasoning structure

**Files:**
- Modify: `frontend/src/pages/RecommendationPage.tsx`
- Modify: `frontend/tests/recommendation.test.tsx`

**Interfaces:**
- Consumes: `RecommendationHeader` (Task 3), unchanged `ScanResult` union
  from `../api/client`.
- Produces: `RecommendationSummary` renders one unified structure for all
  three outcomes (`approval_ready`/t27, `approval_ready`/legacy,
  `manual_review`) instead of branching into two separate JSX trees.

Today, `RecommendationSummary` returns an entirely different element tree
for `manual_review` (a `<section aria-label="Manual review summary">` with
bespoke rationale/trade-offs/uncertainty markup) versus `approval_ready`
(the decision-grid + reasoning-panel tree). Per the approved spec, all three
outcomes should share the same visual language. Every field the manual-review
branch uses (`rationale`, `trade_offs`, `risk_flags`, `uncertainty`,
`evidence_limitations`) already exists on all three `ScanResult` variants
(confirmed in `frontend/src/api/client.ts`), so the reasoning panel can
render generically without branching.

- [ ] **Step 1: Write the failing test**

Update the existing test `"shows deterministic evidence with a safe
manual-review fallback"` in `frontend/tests/recommendation.test.tsx` — change
its assertions from:

```tsx
    expect(
      await screen.findByRole("region", { name: "Manual review summary" }),
    ).toHaveTextContent("No draft created");
    expect(screen.getByText("Compare the eligible offers manually.")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Deterministic procurement evidence" }),
    ).toBeInTheDocument();
```

to:

```tsx
    const summary = await screen.findByRole("region", {
      name: "Recommendation summary",
    });
    expect(summary).toHaveTextContent("Manual review");
    expect(summary).toHaveTextContent("No draft created");
    expect(
      screen.getByRole("heading", { name: "Compare eligible offers" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Contextual model judgment could not be safely accepted.")).toBeInTheDocument();
    expect(screen.getByText("Compare the eligible offers manually.")).toBeInTheDocument();
```

Note: this test's mock response already sets `evidence` to the same single
dev-product record as `BASE_SCAN.evidence[0]` while `result.outcome` is
`"manual_review"` — since `manual_review` never has a `product_id`,
`findRecommendedEvidence` (Task 2) correctly returns `null` for it, so no
"Deterministic procurement evidence" heading should appear. Add:

```tsx
    expect(
      screen.queryByRole("heading", { name: "Deterministic procurement evidence" }),
    ).not.toBeInTheDocument();
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run tests/recommendation.test.tsx -t "manual-review fallback"
```

Expected: FAIL — today's manual_review branch uses a differently-labeled
region (`"Manual review summary"`, not `"Recommendation summary"`) and
never renders `RecommendationHeader`.

- [ ] **Step 3: Rewrite `RecommendationSummary` to a single unified structure**

In `frontend/src/pages/RecommendationPage.tsx`, replace the entire
`RecommendationSummary` function body (from the `if (scan.result === null)`
check through its final closing brace) with:

```tsx
function RecommendationSummary({
  scan,
  evidence,
}: {
  scan: Scan;
  evidence: ProcurementEvidenceRecord | null;
}) {
  if (scan.result === null) {
    return null;
  }
  const result = scan.result;
  const isLegacy =
    result.outcome === "approval_ready" && result.validation_level === "legacy";

  return (
    <section
      aria-label="Recommendation summary"
      className="panel recommendation-summary"
    >
      <div className="recommendation-hero-grid">
        <RecommendationHeader result={result} evidence={evidence} />

        <section className="reasoning-panel" aria-labelledby="rationale-title">
          <div className="reasoning-heading">
            <h3 id="rationale-title">
              <span className="summary-icon summary-icon--blue">
                <Icon name="recommendation" />
              </span>
              {isLegacy ? "Historical reasoning" : "AI reasoning"}
            </h3>
            <span
              className={`validation-badge ${isLegacy ? "validation-badge--legacy" : ""}`}
            >
              <Icon name={isLegacy ? "document" : "check"} />
              {isLegacy ? "Predates T27 validation" : "Validated against evidence"}
            </span>
          </div>
          <p className="reasoning-rationale">{result.rationale}</p>
          <div className="reasoning-details">
            <section aria-labelledby="tradeoffs-title">
              <h4 id="tradeoffs-title">Key trade-offs</h4>
              {result.trade_offs.length > 0 ? (
                <ul>
                  {result.trade_offs.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : (
                <p>No additional trade-offs recorded.</p>
              )}
            </section>
            <section aria-labelledby="risks-title">
              <h4 id="risks-title">Risks and limitations</h4>
              {result.risk_flags.length === 0 ? (
                <p className="risk-state risk-state--clear">
                  <span className="summary-icon summary-icon--green">
                    <Icon name="check" />
                  </span>
                  No risk flags identified
                </p>
              ) : (
                <div className="risk-state risk-state--warning">
                  <span className="summary-icon summary-icon--amber">
                    <Icon name="alert" />
                  </span>
                  <ul className="tag-list">
                    {result.risk_flags.map((flag) => (
                      <li key={flag}>{flag.replaceAll("_", " ")}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          </div>
          <div className="uncertainty-block">
            <h4>Uncertainty</h4>
            <p>{result.uncertainty}</p>
            {result.evidence_limitations.length > 0 ? (
              <ul>
                {result.evidence_limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : null}
          </div>
        </section>
      </div>
    </section>
  );
}
```

This removes the separate `manual_review`-only branch entirely — the same
function now handles all three outcomes uniformly. Evidence rendering
(`ProcurementEvidence`) stays external to this function, driven by the
`RecommendationPage`-level conditional from Task 2 (which already only
renders it when `findRecommendedEvidence` returns non-null — correctly
`null` for `manual_review`).

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run tests/recommendation.test.tsx
```

Expected: PASS — all tests, including the updated manual-review test and
the still-passing `"keeps historical successful recommendations
approval-ready"` test (its assertions already only check text content
present in the unified structure).

- [ ] **Step 5: Run full verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/RecommendationPage.tsx frontend/tests/recommendation.test.tsx
git commit -m "refactor(frontend): unify manual_review into the shared recommendation layout"
```

---

## Task 5: Extract OfferComparison (side-by-side cards, capped, AI-selected first)

**Files:**
- Create: `frontend/src/components/OfferComparison.tsx`
- Create: `frontend/tests/offer-comparison.test.tsx`
- Modify: `frontend/src/components/ProcurementEvidence.tsx`
- Modify: `frontend/src/pages/RecommendationPage.tsx` (pass `selectedOfferId`
  down to `ProcurementEvidence`)
- Modify: `frontend/tests/recommendation.test.tsx` (update the offer-list
  test to match the new card layout)
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Produces: `OfferComparison({ offers, selectedOfferId }): JSX.Element` —
  `offers: OfferEvidence[]`, `selectedOfferId: string | null`. Sorts the
  selected offer first, renders up to 3 as cards, remainder behind a "View N
  more offers" disclosure.
- Consumes: `OfferEvidence` type from `../api/client`; `formatCurrency`,
  `formatDate`, `formatQuantity`, `formatRatioPercent` from `../presentation`.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/offer-comparison.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { OfferComparison } from "../src/components/OfferComparison";
import type { OfferEvidence } from "../src/api/client";

function makeOffer(overrides: Partial<OfferEvidence>): OfferEvidence {
  return {
    offer_id: "offer-x",
    vendor_id: "vendor-x",
    vendor_name: "Vendor X",
    status: "eligible",
    reason_codes: [],
    currency: "USD",
    unit_price: "10.000000",
    company_currency: "USD",
    normalized_unit_price: "10.000000",
    delivery_date: "2026-08-18",
    quantity: "12.000000",
    normalized_cost: "120.000000",
    projected_inventory_after_receipt: "12.000000",
    excess_inventory: "0.000000",
    performance: {
      completed_order_count: 2,
      on_time_rate: "1.000000",
      history_status: "limited",
    },
    ...overrides,
  };
}

describe("OfferComparison", () => {
  it("sorts the selected offer first and marks it as AI selected", () => {
    const offers = [
      makeOffer({ offer_id: "offer-a", vendor_name: "Vendor A" }),
      makeOffer({ offer_id: "offer-b", vendor_name: "Vendor B" }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId="offer-b" />);

    const cards = screen.getAllByRole("listitem");
    expect(cards[0]).toHaveTextContent("Vendor B");
    expect(cards[0]).toHaveTextContent("AI selected");
    expect(cards[1]).toHaveTextContent("Vendor A");
    expect(cards[1]).not.toHaveTextContent("AI selected");
  });

  it("shows a not-eligible badge with a vendor-not-approved reason inline", () => {
    const offers = [
      makeOffer({ offer_id: "offer-a", vendor_name: "Vendor A" }),
      makeOffer({
        offer_id: "offer-c",
        vendor_name: "Vendor C",
        status: "rejected",
        reason_codes: ["VENDOR_NOT_APPROVED"],
      }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId="offer-a" />);

    expect(screen.getByText("Vendor not approved")).toBeInTheDocument();
  });

  it("caps visible cards at 3 and collapses the rest", async () => {
    const user = userEvent.setup();
    const offers = [
      makeOffer({ offer_id: "offer-a", vendor_name: "Vendor A" }),
      makeOffer({ offer_id: "offer-b", vendor_name: "Vendor B" }),
      makeOffer({ offer_id: "offer-c", vendor_name: "Vendor C" }),
      makeOffer({ offer_id: "offer-d", vendor_name: "Vendor D" }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId={null} />);

    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByText("Vendor D")).not.toBeInTheDocument();

    await user.click(screen.getByText("View 1 more offer"));
    expect(screen.getByText("Vendor D")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run tests/offer-comparison.test.tsx
```

Expected: FAIL — `frontend/src/components/OfferComparison.tsx` does not
exist.

- [ ] **Step 3: Create `OfferComparison.tsx`**

```tsx
import type { OfferEvidence } from "../api/client";
import {
  formatCurrency,
  formatDate,
  formatQuantity,
  formatRatioPercent,
} from "../presentation";

const VISIBLE_OFFER_COUNT = 3;

function sortOffers(
  offers: OfferEvidence[],
  selectedOfferId: string | null,
): OfferEvidence[] {
  if (selectedOfferId === null) {
    return offers;
  }
  const selected = offers.filter((offer) => offer.offer_id === selectedOfferId);
  const rest = offers.filter((offer) => offer.offer_id !== selectedOfferId);
  return [...selected, ...rest];
}

function statusLabel(offer: OfferEvidence): string {
  if (offer.reason_codes.includes("VENDOR_NOT_APPROVED")) {
    return "Vendor not approved";
  }
  return offer.status === "eligible" ? "Eligible" : "Not eligible";
}

function OfferCard({
  offer,
  isSelected,
}: {
  offer: OfferEvidence;
  isSelected: boolean;
}) {
  return (
    <article
      className={`offer-card${isSelected ? " offer-card--selected" : ""}`}
    >
      {isSelected ? (
        <span className="offer-card__selected-badge">AI selected</span>
      ) : null}
      <div className="offer-card__heading">
        <strong>{offer.vendor_name}</strong>
        <span
          className={`status status--${offer.status === "eligible" ? "succeeded" : "failed"}`}
        >
          {statusLabel(offer)}
        </span>
      </div>
      <p className="offer-card__price" title={offer.normalized_cost}>
        {formatCurrency(offer.normalized_cost, offer.company_currency)}
      </p>
      <dl className="offer-metrics">
        <div>
          <dt>Quantity</dt>
          <dd>{formatQuantity(offer.quantity)}</dd>
        </div>
        <div>
          <dt>Delivery</dt>
          <dd>{formatDate(offer.delivery_date)}</dd>
        </div>
      </dl>
      <p className="offer-history">
        On-time rate: {formatRatioPercent(offer.performance.on_time_rate)}
        <br />
        Completed orders: {offer.performance.completed_order_count}
      </p>
    </article>
  );
}

export function OfferComparison({
  offers,
  selectedOfferId,
}: {
  offers: OfferEvidence[];
  selectedOfferId: string | null;
}) {
  if (offers.length === 0) {
    return <p>No vendor offer evidence was available.</p>;
  }
  const sorted = sortOffers(offers, selectedOfferId);
  const visible = sorted.slice(0, VISIBLE_OFFER_COUNT);
  const overflow = sorted.slice(VISIBLE_OFFER_COUNT);

  return (
    <div className="offer-comparison">
      <div
        className="offer-comparison__row"
        role="list"
        aria-label="Vendor offers"
      >
        {visible.map((offer) => (
          <div role="listitem" key={offer.offer_id}>
            <OfferCard
              offer={offer}
              isSelected={offer.offer_id === selectedOfferId}
            />
          </div>
        ))}
      </div>
      {overflow.length > 0 ? (
        <details className="compact-disclosure offer-comparison__overflow">
          <summary>
            View {overflow.length} more{" "}
            {overflow.length === 1 ? "offer" : "offers"}
          </summary>
          <div
            className="offer-comparison__row"
            role="list"
            aria-label="Additional vendor offers"
          >
            {overflow.map((offer) => (
              <div role="listitem" key={offer.offer_id}>
                <OfferCard offer={offer} isSelected={false} />
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run tests/offer-comparison.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Wire `OfferComparison` into `ProcurementEvidence.tsx`**

In `frontend/src/components/ProcurementEvidence.tsx`, remove the `OfferList`
function entirely (no longer used) and its two call sites (eligible offers
list and the rejected-offers `<details>` disclosure). Replace the entire
`<section className="evidence-card" aria-labelledby={`offers-${evidence.evidence_id}`}>`
block with:

```tsx
        <section
          className="evidence-card"
          aria-labelledby={`offers-${evidence.evidence_id}`}
        >
          <div className="evidence-card__heading">
            <div>
              <h4 id={`offers-${evidence.evidence_id}`}>Vendor offers</h4>
              <p>The AI-selected offer is shown first.</p>
            </div>
            <span className="evidence-count">
              {evidence.offers.length} considered
            </span>
          </div>
          <OfferComparison
            offers={evidence.offers}
            selectedOfferId={selectedOfferId}
          />
        </section>
```

Add the import:

```tsx
import { OfferComparison } from "./OfferComparison";
```

Update the `ProcurementEvidence` component's props to accept
`selectedOfferId`:

```tsx
export function ProcurementEvidence({
  evidence,
  selectedOfferId,
}: {
  evidence: Evidence;
  selectedOfferId: string | null;
}) {
```

- [ ] **Step 6: Pass `selectedOfferId` from `RecommendationPage.tsx`**

Update the `<ProcurementEvidence>` call site added in Task 2 to:

```tsx
          {findRecommendedEvidence(scan) ? (
            <ProcurementEvidence
              evidence={findRecommendedEvidence(scan)!}
              selectedOfferId={
                scan.result &&
                scan.result.outcome === "approval_ready" &&
                scan.result.offer_id !== null
                  ? scan.result.offer_id
                  : null
              }
            />
          ) : null}
```

- [ ] **Step 7: Update the existing offer-list test to match the new card layout**

In `frontend/tests/recommendation.test.tsx`, update the test
`"shows a projection graph and separates rejected offers"`. Replace its
final assertions block (from `expect(screen.getByText("Only eligible
offer"))` through the end of the test) with:

```tsx
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Fictional Approved Supplies")).toBeInTheDocument();
    expect(screen.getByText("Fictional Late Supplies")).toBeInTheDocument();
    expect(screen.getByText("Not eligible")).toBeInTheDocument();
```

(Both offers now render inline as cards since there are only 2, under the
cap of 3 — no collapsed disclosure needed for this test's fixture.)

- [ ] **Step 8: Run full verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: all pass.

- [ ] **Step 9: Add offer comparison CSS**

Append to `frontend/src/styles.css`, after the existing `.offer-heading
.status` rule (end of file, around line 1400+):

```css
.offer-comparison__row {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
  margin-top: 0.75rem;
}

.offer-card {
  position: relative;
  min-width: 0;
  padding: 0.9rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: #ffffff;
}

.offer-card--selected {
  border: 2px solid var(--accent);
  background: var(--accent-soft);
}

.offer-card__selected-badge {
  position: absolute;
  top: -0.6rem;
  right: 0.75rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  background: var(--accent);
  color: #ffffff;
  font-size: 0.65rem;
  font-weight: 750;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.offer-card__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.offer-card__price {
  margin: 0 0 0.5rem;
  color: var(--ink);
  font-size: 1.1rem;
  font-weight: 780;
}

.offer-comparison__overflow {
  margin-top: 0.75rem;
}
```

- [ ] **Step 10: Run full verification and commit**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
git add frontend/src/components/OfferComparison.tsx \
  frontend/src/components/ProcurementEvidence.tsx \
  frontend/src/pages/RecommendationPage.tsx \
  frontend/tests/offer-comparison.test.tsx \
  frontend/tests/recommendation.test.tsx \
  frontend/src/styles.css
git commit -m "feat(frontend): add side-by-side OfferComparison cards"
```

---

## Task 6: Extract BudgetPanel as an always-visible panel

**Files:**
- Create: `frontend/src/components/BudgetPanel.tsx`
- Create: `frontend/tests/budget-panel.test.tsx`
- Modify: `frontend/src/components/ProcurementEvidence.tsx`
- Modify: `frontend/tests/recommendation.test.tsx` (no assertions target the
  budget disclosure's collapse behavior today, so no test needs removal —
  verify this in Step 6)
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Produces: `BudgetPanel({ budget }): JSX.Element | null` — `budget:
  ProcurementEvidence["budget"]` (nullable). Renders `null` when `budget` is
  `null`, matching today's conditional.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/budget-panel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BudgetPanel } from "../src/components/BudgetPanel";

describe("BudgetPanel", () => {
  it("renders budget figures without a collapse control", () => {
    render(
      <BudgetPanel
        budget={{
          period_start: "2026-08-01",
          currency: "USD",
          budget_amount: "5000.000000",
          confirmed_commitment: "160.000000",
          proposed_amount: "1080.000000",
          remaining_before: "4840.000000",
          remaining_after: "3760.000000",
          overage: "0.000000",
          exception_required: false,
        }}
      />,
    );

    expect(screen.getByText("Budget calculation")).toBeInTheDocument();
    expect(screen.getByText("Within budget")).toBeInTheDocument();
    expect(screen.getByText("$3,760.00")).toBeInTheDocument();
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(document.querySelector("details.budget-panel")).toBeNull();
  });

  it("shows the exception warning when required", () => {
    render(
      <BudgetPanel
        budget={{
          period_start: "2026-08-01",
          currency: "USD",
          budget_amount: "500.000000",
          confirmed_commitment: "0.000000",
          proposed_amount: "600.000000",
          remaining_before: "500.000000",
          remaining_after: "-100.000000",
          overage: "100.000000",
          exception_required: true,
        }}
      />,
    );

    expect(screen.getByText("Exception required")).toBeInTheDocument();
    expect(
      screen.getByText(/Manager exception required for \$100\.00 overage\./),
    ).toBeInTheDocument();
  });

  it("renders nothing when budget is null", () => {
    const { container } = render(<BudgetPanel budget={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run tests/budget-panel.test.tsx
```

Expected: FAIL — `frontend/src/components/BudgetPanel.tsx` does not exist.

- [ ] **Step 3: Create `BudgetPanel.tsx`**

```tsx
import type { ProcurementEvidence as Evidence } from "../api/client";
import { formatCurrency } from "../presentation";

export function BudgetPanel({
  budget,
}: {
  budget: Evidence["budget"];
}) {
  if (budget === null) {
    return null;
  }
  return (
    <section className="panel budget-panel" aria-labelledby="budget-panel-title">
      <div className="budget-panel__heading">
        <h4 id="budget-panel-title">Budget calculation</h4>
        <span
          className={
            budget.exception_required
              ? "budget-panel__status budget-panel__status--exception"
              : "budget-panel__status"
          }
        >
          {budget.exception_required ? "Exception required" : "Within budget"}
        </span>
      </div>
      <dl className="evidence-grid">
        <div>
          <dt>Budget</dt>
          <dd>{formatCurrency(budget.budget_amount, budget.currency)}</dd>
        </div>
        <div>
          <dt>Committed</dt>
          <dd>{formatCurrency(budget.confirmed_commitment, budget.currency)}</dd>
        </div>
        <div>
          <dt>Proposed</dt>
          <dd>{formatCurrency(budget.proposed_amount, budget.currency)}</dd>
        </div>
        <div>
          <dt>Remaining after</dt>
          <dd>{formatCurrency(budget.remaining_after, budget.currency)}</dd>
        </div>
      </dl>
      {budget.exception_required ? (
        <p className="budget-warning">
          Manager exception required for{" "}
          {formatCurrency(budget.overage, budget.currency)} overage.
        </p>
      ) : null}
    </section>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run tests/budget-panel.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Wire `BudgetPanel` into `ProcurementEvidence.tsx`**

In `frontend/src/components/ProcurementEvidence.tsx`, find the `<aside
className="evidence-disclosures" ...>` block. Replace the
`{evidence.budget ? (<details ...>...</details>) : null}` sub-block (the
budget `<details>` disclosure) with:

```tsx
            <BudgetPanel budget={evidence.budget} />
```

Add the import:

```tsx
import { BudgetPanel } from "./BudgetPanel";
```

- [ ] **Step 6: Confirm no existing test asserts the removed disclosure toggle**

```bash
grep -n "Budget calculation" frontend/tests/recommendation.test.tsx
```

Expected: no output (no existing test targets the budget `<details>`
toggle behavior specifically — the removed behavior is safe to drop). If
this grep does find a match, update that assertion to match the new
always-visible rendering (no `<details>`/`click to expand` interaction)
before proceeding.

- [ ] **Step 7: Run full verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: all pass.

- [ ] **Step 8: Add budget panel CSS**

Append to `frontend/src/styles.css`:

```css
.budget-panel {
  padding: 1rem;
}

.budget-panel__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.budget-panel__heading h4 {
  margin: 0;
}

.budget-panel__status {
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 0.72rem;
  font-weight: 750;
}

.budget-panel__status--exception {
  background: #fdecea;
  color: #b3261e;
}
```

- [ ] **Step 9: Run full verification and commit**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
git add frontend/src/components/BudgetPanel.tsx \
  frontend/src/components/ProcurementEvidence.tsx \
  frontend/tests/budget-panel.test.tsx \
  frontend/src/styles.css
git commit -m "feat(frontend): make budget calculation an always-visible panel"
```

---

## Task 7: Make Applied Preferences an always-visible panel

**Files:**
- Modify: `frontend/src/components/ProcurementEvidence.tsx`
- Modify: `frontend/tests/recommendation.test.tsx` (update the applied
  preferences test to remove the disclosure-open interaction)
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: unchanged `AppliedPreferences` component from
  `./AppliedPreferences.tsx` (no prop changes).

- [ ] **Step 1: Write the failing test**

In `frontend/tests/recommendation.test.tsx`, update the test `"presents
applied preferences as ordered policy information"`. Replace:

```tsx
    render(<RecommendationPage scanId="scan-101" onBack={vi.fn()} />);

    await screen.findByText("Applied preferences", {
      selector: "summary > span",
    });
```

with:

```tsx
    render(<RecommendationPage scanId="scan-101" onBack={vi.fn()} />);

    await screen.findByRole("heading", { name: "Applied preferences" });
    expect(
      document.querySelector("details.evidence-disclosures .disclosure"),
    ).toBeNull();
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run tests/recommendation.test.tsx -t "applied preferences"
```

Expected: FAIL — today's markup still wraps `AppliedPreferences` in a
`<details className="disclosure">`, so there is no plain `summary > span`
match under the new selector expectations, and the `disclosure` element
still exists.

- [ ] **Step 3: Remove the disclosure wrapper in `ProcurementEvidence.tsx`**

Replace the block:

```tsx
            {evidence.preferences ? (
              <details className="disclosure" open>
                <summary>
                  <span>Applied preferences</span>
                  <small>Revision {evidence.preferences.revision}</small>
                </summary>
                <div className="disclosure__content">
                  <AppliedPreferences preferences={evidence.preferences} />
                </div>
              </details>
            ) : null}
```

with:

```tsx
            {evidence.preferences ? (
              <div className="panel">
                <AppliedPreferences preferences={evidence.preferences} />
              </div>
            ) : null}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run tests/recommendation.test.tsx
```

Expected: PASS — all tests.

- [ ] **Step 5: Run full verification**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
```

Expected: all pass.

- [ ] **Step 6: Adjust CSS for the un-wrapped preferences panel**

The existing `.evidence-disclosures .preferences-heading > div:first-child
.summary-icon` and related rules (`frontend/src/styles.css:1108-1120`) were
scoped to `.evidence-disclosures .disclosure`. Since preferences no longer
render inside a `.disclosure`, update the aside's own class from
`evidence-disclosures` to a name reflecting always-visible panels. In
`frontend/src/components/ProcurementEvidence.tsx`, change:

```tsx
<aside className="evidence-disclosures" aria-label="Evidence policy details">
```

to:

```tsx
<aside className="evidence-panels" aria-label="Evidence policy details">
```

In `frontend/src/styles.css`, rename every `.evidence-disclosures` selector
(lines 941, 1094, 1099, 1103, 1108, 1109, 1113, 1117) to `.evidence-panels`,
and remove the now-dead `.disclosure`-scoped rules within that block (lines
1094–1120's `.disclosure`-specific selectors), keeping only the layout rule:

```css
.evidence-panels {
  display: grid;
  align-content: start;
  gap: 0.65rem;
}

.evidence-panels .preferences-panel {
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: #ffffff;
}
```

- [ ] **Step 7: Run full verification and commit**

```bash
cd frontend && npm run typecheck && npm run lint && npm test
git add frontend/src/components/ProcurementEvidence.tsx \
  frontend/tests/recommendation.test.tsx \
  frontend/src/styles.css
git commit -m "feat(frontend): make applied preferences an always-visible panel"
```

---

## Final Self-Review (perform before considering the plan complete)

- [ ] **Spec coverage check:** Re-read
  `docs/superpowers/specs/2026-08-17-recommendation-detail-restyle-design.md`
  section by section and confirm each design decision maps to a task above:
  rename (Task 1), evidence filtering (Task 2), header stat cards (Task 3),
  unified outcome treatment (Task 4), offer comparison layout (Task 5),
  budget panel (Task 6), preferences panel (Task 7). Non-goals (no LLM
  content changes, no cardinality work, no T28/T29 additions) — confirm no
  task above touches `src/procurement` or adds outcome types beyond what
  `frontend/src/api/client.ts` already defines.
- [ ] **Full suite run:** From `frontend/`, run
  `npm run typecheck && npm run lint && npm test && npm run build` and
  confirm all pass with no warnings introduced.
- [ ] **Manual browser check:** Run `npm run dev` in `frontend/`, sign in
  (or use the local test session path per `docs/plan.md`), open a scan with
  an approval-ready result, and visually compare the rendered page against
  `recommendation_details.png` at the repo root — header cards, offer
  cards, always-visible budget/preferences panels.

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-08-17-recommendation-detail-restyle.md`.

The standard `subagent-driven-development` and `executing-plans` sub-skills
are not available in this repository's `.claude/skills/` — normally this is
where you'd choose between dispatching a fresh subagent per task or
executing inline with checkpoints. Since neither sub-skill is loaded here,
the practical choice is:

1. **Execute inline, task by task** — work through Tasks 1–7 in this
   session in order, running each task's verification steps before
   committing and moving to the next. This plan's tasks are already sized
   for that (each ends in a runnable, tested, committed state).
2. **Dispatch a general-purpose subagent per task** — since the specific
   named sub-skills aren't present, a plain `Agent` call per task (passing
   the task's full text as the prompt) approximates the same isolation
   benefit without the specialized review protocol those skills would add.

Which approach would you like?
