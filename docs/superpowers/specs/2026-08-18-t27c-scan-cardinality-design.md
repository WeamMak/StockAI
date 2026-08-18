# T27C — Scan cardinality: one recommendation per product

## Status

Approved by user 2026-08-18. Ready for implementation planning.

## Context

Three UI mockups (`home_page.png`, `Scan_details.png`, `recommendation_details.png`)
in the repo root describe a target look for the StockAI procurement frontend.
They were decomposed into three sub-projects, each with its own spec → plan →
implementation cycle:

1. **Scan-cardinality architecture** — this document. Task name **T27C**,
   inserted between T27 and T28 in `docs/plan.md`.
2. **Recommendation-detail page restyle** — complete. See
   `docs/superpowers/specs/2026-08-17-recommendation-detail-restyle-design.md`.
3. **Home page enhancements** — deferred, not started.

This is not a new feature invented for the mockups. `docs/spec.md:189` already
states, as an authoritative `[Project decision]`: *"One independent
recommendation, approval, and PO per product."* The T27 amendment design doc
(`docs/superpowers/specs/2026-08-16-t27-live-repair-and-demo-design.md:45-47`)
explicitly named the gap: *"The current workflow may gather several
candidates but returns one selected recommendation; this amendment does not
add multi-result scan aggregation,"* and listed "multi-result scan API" as an
explicit T27 exclusion. Neither T28 nor T29 as currently written in
`docs/plan.md` claim this work — both state `Consumes: one validated T27
recommendation`, assuming a single recommendation is already the input. T27C
fills that gap.

## Goals

- One scan evaluates every candidate product needing replenishment (reusing
  today's `discover_candidates`), and every candidate gets an independent,
  fully-evaluated result — deterministic evidence gathering, then LLM
  reasoning, then validation — regardless of whether the others succeed,
  find no valid offer, or need manual review.
- No candidate is silently dropped. Every one of them ends up in exactly one
  of: `approval_ready`, `manual_review`, or the new `no_valid_offer` outcome.
- A new scan-detail page shows all of a scan's results as a table with an
  outcome-breakdown donut, matching `Scan_details.png`. Each row links to the
  existing `RecommendationPage` (built in sub-project 2) for that one case.
- Lay the groundwork for T28/T29 by choosing a case boundary that keeps their
  existing "one validated recommendation → at most one draft" design correct
  without rework.

## Non-goals

- No draft PO creation, manager approval, confirmation, or rejection logic —
  that is T28 and T29's own work.
- No home page changes — sub-project 3, separate spec.
- No LLM prompt content changes beyond running today's exact same
  single-candidate reasoning call once per candidate instead of once across
  all candidates arbitrated together. The words the LLM produces for a given
  candidate are not changing.
- No `confirmed`-outcome backend logic. The type is added as an unreachable
  placeholder (see below); nothing in T27C's code path ever produces it.
- No change to the deterministic evidence-gathering logic itself (coverage,
  offers, budget, preferences) — only to how many times it runs per scan and
  how each run's outcome is persisted and surfaced.

## Decisions

### Case boundary: one case per product per scan

A scan run spawns N independent cases — one per candidate needing
replenishment — each with its own LangGraph `thread_id`, checkpoint, and
persisted case record. This keeps T28's "one validated recommendation
produces at most one idempotent draft" design correct unmodified: T28 will
simply be invoked once per case instead of once per scan.

### LLM call pattern: one call per candidate

Each candidate's evidence goes through its own independent `recommend()`
call — the same call shape, prompt, and validator that exist today for the
single-candidate path, just invoked N times instead of once with all
candidates bundled into one arbitration call. This keeps the existing
prompt, JSON schema, and validator unchanged; only the orchestration layer
calling it changes.

### New outcome: `NoValidOfferResult`

Today, "no eligible vendor offer" is represented as a whole-scan failure
(`status: "failed"`, `error.error_code: "NO_VALID_OFFER"`). Once evaluation
is per-case, a candidate having no valid offer must not fail its case (and
must not affect any sibling case) — it is a legitimate, correctly-functioning
deterministic outcome, not a system error. A new result variant is added
alongside `ApprovalReadyResult` and `ManualReviewResult`:

```python
@dataclass(frozen=True)
class NoValidOfferResult:
    outcome: Literal["no_valid_offer"]
    product_id: str
    product_name: str
    rationale: str
    evidence_limitations: list[str]
    read_only: Literal[True]
```

A case with this result has `status: "succeeded"` — the deterministic
process correctly ran and correctly found no eligible vendor. `rationale`
explains why (e.g. no approved vendor could satisfy the need-by date),
mirroring the existing `no_valid_offer` fictional seed scenario (T27's
4-product seed already includes exactly this scenario: "Replenishment
required with zero eligible offers: deterministic `no_valid_offer`").

### Placeholder: `ConfirmedResult` (type-only, unreachable)

A `confirmed` outcome variant and `po_reference`/`po_amount` fields are added
to the type system and the scan-detail table's rendering logic now, matching
`Scan_details.png`'s "Confirmed" status badge and "PO confirmed in Odoo" / "PO
amount" row treatment. Nothing in T27C's orchestration or graph code ever
produces this outcome — it exists so T29's future confirmation work only
needs to start returning it, with no frontend rework required. No
approve/confirm/reject UI actions are added anywhere in T27C.

### Orchestration: Approach A — thin aggregator over independent cases

`discover_candidates` runs once per scan (unchanged function, reused as-is).
For each candidate it returns, the existing LangGraph app is invoked with a
fresh `thread_id` — the evidence-gathering → LLM-reasoning → validation path
is the same path that runs today for a single candidate, called once per
candidate instead of once total. This was chosen over LangGraph `Send`-based
fan-out within one graph invocation because `Send` branches normally share
the parent run's checkpoint rather than being fully independent, which would
fight the "one case, one `thread_id`" decision above for no benefit here —
scans in this system are not latency-sensitive enough (daily cron plus
on-demand, per `docs/spec.md`) to justify the added complexity of concurrent
in-process branching.

### Persistence: new `ScanRecord`

Today there is no domain-layer `Scan` class — "the Scan concept lives
entirely in `agent/state.py` and `api/services/scans.py`" (confirmed by
reading the current source). A new `ScanRecord` is introduced:

- `scan_id`, `status` (`queued` / `running` / `succeeded` / `failed` — now
  describing the scan's own orchestration health, not any one candidate's
  outcome), `trigger`, `created_at`, `started_at`, `completed_at`.
- A list of case summaries: `case_id`, `product_id`, `product_name`,
  `outcome`, and the minimal fields the results table needs (best-offer
  amount or recommendation amount, need-by date) — enough to render
  `Scan_details.png`'s table without an N+1 query per row. Full per-case
  detail (evidence, reasoning, offers) stays on the case record, fetched
  only when a row is opened.
- A scan's own `status` is `failed` only for infrastructure-level failures
  that prevented evaluating candidates at all (e.g. MCP unreachable before
  any candidate could be evaluated) — never because one candidate's case
  came back `no_valid_offer` or `manual_review`. A scan can be `succeeded`
  with a results table where every row is `no_valid_offer`, `manual_review`,
  or any mix — the scan succeeded at running the process; what it found is
  the point of the results.

### API shape

- `GET /api/v1/scans/{scan_id}` returns the scan aggregate: status, trigger,
  timestamps, and the list of case summaries for the results table plus the
  outcome-breakdown counts for the donut chart.
- Case-level detail (evidence, full result, offers) is served through a
  route scoped to one case — reusing the existing single-result response
  shape `RecommendationPage` already consumes, so that page's prop contract
  and nearly all of its rendered content in sub-project 2 stay unchanged.
  The exact route path (nested under the scan, e.g.
  `/api/v1/scans/{scan_id}/cases/{case_id}`, versus a flatter
  `/api/v1/cases/{case_id}`) is an implementation-plan-level decision, not a
  design-level one — either satisfies this spec.
- `POST /api/v1/scans` (trigger a manual scan) keeps its existing shape;
  its immediate response becomes the new scan-aggregate shape instead of a
  single case result.

## Frontend

- **New `ScanDetailPage`** (`frontend/src/pages/`): results table (product
  name, need-by date, outcome badge, best offer / recommendation amount or
  PO amount for `confirmed` rows, link to that case's `RecommendationPage`)
  plus an outcome-breakdown donut chart, matching `Scan_details.png`. This
  page is new; nothing existing is renamed for it (unlike sub-project 2,
  where the single-result page *was* renamed because it doubled as both
  concepts).
- **`App.tsx` navigation**: selecting a scan from the scans list now goes to
  `ScanDetailPage` instead of directly to `RecommendationPage`; selecting a
  row within `ScanDetailPage` goes to `RecommendationPage` for that case.
- **`frontend/src/api/client.ts`**: the `Scan` type splits into a scan
  aggregate type (list of case summaries + counts) and a case-detail type
  (today's existing `Scan` shape, minus the fields that move to the
  aggregate). `ScanResult` gains `NoValidOfferResult` and the placeholder
  `ConfirmedResult` variants.
- Every component built in sub-project 2 (`RecommendationHeader`,
  `OfferComparison`, `BudgetPanel`, `ProcurementEvidence`) is reused
  unmodified for rendering one case's detail — this is exactly the payoff of
  building sub-project 2 first, per the sequencing decision made earlier.

## Error handling

- A scan-level failure (e.g. MCP unreachable before candidate discovery
  completes) surfaces exactly like today's whole-scan failure path — nothing
  changes there, since no candidates were ever evaluated to report on.
- A failure evaluating one specific candidate after discovery succeeded
  (e.g. a transient MCP error fetching that one candidate's evidence, after
  retries are exhausted) does not fail the scan or its sibling cases. That
  one case gets a case-level failure state (reusing today's existing
  `ScanFailure`-shaped error, now scoped to a case instead of a scan) while
  the rest of the scan's cases proceed normally.

## Testing

- Python: unit tests for the new orchestration loop (discovery once, N
  independent graph invocations, aggregation into a `ScanRecord`), the new
  `NoValidOfferResult` type and its validator path, and case-level failure
  isolation (one candidate's failure doesn't affect siblings). Extends the
  existing T27 fictional 4-product seed scenarios (which already include a
  `no_valid_offer` case) rather than inventing new ones.
- Frontend: new tests for `ScanDetailPage` (results table rendering, donut
  chart, per-row links) and updated `client.ts` parsing tests for the new
  scan-aggregate/case-detail split and the two new outcome variants.
- Integration: real-MCP-transport test confirming N independent case
  invocations against the existing fake-Odoo scenarios produce N correctly
  isolated results in one scan.

## Open questions / follow-on work

- The exact case-detail API route path (nested vs. flat) is left to the
  implementation plan, as noted above.
- `docs/plan.md` needs a new T27C section (Files/Interfaces/Work-and-tests/
  Dependencies/Requirements/Complete-when, matching the T28/T29 format) and
  T28's `Dependencies: T27` line needs to become `Dependencies: T27C`. This
  is deferred to the implementation-plan step rather than done as part of
  this design document, consistent with how the plan document mirrors
  implementation-level task structure rather than design rationale.
