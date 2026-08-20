# No-Valid-Offer Improvements Implementation Plan

> **For agentic workers:** The `superpowers:subagent-driven-development` and
> `superpowers:executing-plans` sub-skills are not present in this
> repository's skill set. Execute tasks in order, one at a time, running
> each task's verification steps before moving to the next. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `no_valid_offer` case-detail page show real evidence and
a real, specific reason instead of a generic static message and no
evidence at all.

**Architecture:** Three independent, small changes: a deterministic
rationale-builder helper in the backend's evidence-gathering node (no LLM
involved), a one-line evidence-gating fix in `RecommendationPage.tsx`, and
an extended reason-code label map in `OfferComparison.tsx`. None depend on
each other's implementation, only on the same underlying data already
being gathered today.

**Tech Stack:** Python 3.12, LangGraph node (plain function, no graph
topology change), React/TypeScript/Vitest.

## Global Constraints

- No LLM call anywhere in this outcome — `no_valid_offer` is produced
  entirely inside `gather_evidence` before the LLM is ever invoked. An
  existing test asserts `llm.requests == []` for this path and that must
  keep passing unmodified.
- No new reason codes — `evaluate_offer` (`domain/policy/offers.py:66-76`)
  produces exactly five: `VENDOR_NOT_APPROVED`, `VENDOR_BLOCKED`,
  `OFFER_NOT_YET_VALID`, `OFFER_EXPIRED`, `DELIVERY_AFTER_NEED_BY`.
- Fixed priority order for all reason-code handling, backend and frontend
  alike: `VENDOR_NOT_APPROVED`, `VENDOR_BLOCKED`, `OFFER_NOT_YET_VALID`,
  `OFFER_EXPIRED`, `DELIVERY_AFTER_NEED_BY`.
- No changes to `manual_review` or `approval_ready` behavior.
- Spec reference: `docs/superpowers/specs/2026-08-18-no-valid-offer-improvements-design.md`
- Run `uv run ruff check <touched files> && uv run mypy <touched files>`
  after the backend task, and `npm run typecheck && npm run lint && npm test`
  (from `frontend/`) after each frontend task.

---

## Task 1: Deterministic rationale builder

**Files:**
- Modify: `src/procurement/agent/nodes/walking_skeleton.py` (`gather_evidence`,
  `NO_VALID_OFFER` branch, currently lines 186-196)
- Modify: `tests/unit/agent/test_walking_skeleton.py`

**Interfaces:**
- Produces: `_no_valid_offer_rationale(offers: tuple[OfferEvidence, ...]) -> str`,
  a module-level pure function in `walking_skeleton.py`.

- [x] **Step 1: Write the failing tests**

Add to `tests/unit/agent/test_walking_skeleton.py`. Add
`_no_valid_offer_rationale` to the existing
`from procurement.agent.nodes.walking_skeleton import WalkingSkeletonNodes`
line (currently line 19):

```python
from procurement.agent.nodes.walking_skeleton import (
    WalkingSkeletonNodes,
    _no_valid_offer_rationale,
)
```

Add `EvidenceStatus` to the existing
`from procurement.domain.policy.evidence import ProcurementEvidence` line
(currently line 28):

```python
from procurement.domain.policy.evidence import EvidenceStatus, ProcurementEvidence
```

Add a small local helper next to the existing `_evidence`/`_candidate`
helpers (currently lines 46-60), building rejected/eligible offers by
`replace()`-ing the single offer the existing `_evidence()` fixture
already returns — no need to reconstruct `evaluate_offer()`'s inputs from
scratch, since `_no_valid_offer_rationale` only reads `status` and
`reason_codes`:

```python
def _offer(
    *, offer_id: str = "offer-1", reason_codes: tuple[str, ...] = ()
) -> OfferEvidence:
    base = _evidence().offers[0]
    return replace(
        base,
        offer_id=offer_id,
        status=(
            EvidenceStatus.REJECTED if reason_codes else EvidenceStatus.ELIGIBLE
        ),
        reason_codes=reason_codes,
    )
```

`OfferEvidence` and `replace` are not yet imported in this file — add
`OfferEvidence` alongside `EvidenceStatus` on the same import line from
the previous step, and confirm `replace` is already imported (it is,
via the existing `from dataclasses import dataclass, field, replace` at
line 6).

```python
from procurement.domain.policy.evidence import EvidenceStatus, OfferEvidence, ProcurementEvidence
```

Now the actual tests:

```python
def test_no_valid_offer_rationale_for_zero_offers() -> None:
    assert (
        _no_valid_offer_rationale(())
        == "No vendor offers exist for this product."
    )


def test_no_valid_offer_rationale_for_single_reason() -> None:
    offers = (
        _offer(offer_id="offer-1", reason_codes=("VENDOR_NOT_APPROVED",)),
        _offer(offer_id="offer-2", reason_codes=("VENDOR_NOT_APPROVED",)),
    )
    assert _no_valid_offer_rationale(offers) == (
        "No eligible offer: 2 offers rejected (vendor not approved)."
    )


def test_no_valid_offer_rationale_singular_offer_count() -> None:
    offers = (_offer(offer_id="offer-1", reason_codes=("VENDOR_NOT_APPROVED",)),)
    assert _no_valid_offer_rationale(offers) == (
        "No eligible offer: 1 offer rejected (vendor not approved)."
    )


def test_no_valid_offer_rationale_for_multiple_reasons_in_priority_order() -> None:
    offers = (
        _offer(offer_id="offer-1", reason_codes=("DELIVERY_AFTER_NEED_BY",)),
        _offer(offer_id="offer-2", reason_codes=("VENDOR_NOT_APPROVED",)),
        _offer(offer_id="offer-3", reason_codes=("VENDOR_BLOCKED",)),
    )
    assert _no_valid_offer_rationale(offers) == (
        "No eligible offer: 1 offer rejected (vendor not approved), "
        "1 offer rejected (vendor blocked), "
        "1 offer rejected (delivery after need-by date)."
    )


def test_no_valid_offer_rationale_offer_with_multiple_reasons_counts_in_both() -> None:
    offers = (
        _offer(
            offer_id="offer-1",
            reason_codes=("VENDOR_NOT_APPROVED", "VENDOR_BLOCKED"),
        ),
    )
    assert _no_valid_offer_rationale(offers) == (
        "No eligible offer: 1 offer rejected (vendor not approved), "
        "1 offer rejected (vendor blocked)."
    )


def test_no_valid_offer_rationale_ignores_eligible_offers() -> None:
    offers = (
        _offer(offer_id="offer-1", reason_codes=()),
        _offer(offer_id="offer-2", reason_codes=("VENDOR_NOT_APPROVED",)),
    )
    assert _no_valid_offer_rationale(offers) == (
        "No eligible offer: 1 offer rejected (vendor not approved)."
    )
```

- [x] **Step 2: Run tests to verify they fail**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/agent/test_walking_skeleton.py -v -k "no_valid_offer_rationale"
```

Expected: FAIL — `ImportError: cannot import name '_no_valid_offer_rationale'`.

- [x] **Step 3: Add the helper and wire it in**

Read `src/procurement/agent/nodes/walking_skeleton.py:1-20` first to see
the exact current import block. Add `OfferEvidence` to the existing
`from procurement.domain.policy.evidence import ProcurementEvidence` line
(currently line 18) — change it to:

```python
from procurement.domain.policy.evidence import EvidenceStatus, OfferEvidence, ProcurementEvidence
```

Add the helper as a module-level function, placed above the
`WalkingSkeletonNodes` class (before line 37):

```python
_REJECTION_LABEL: dict[str, str] = {
    "VENDOR_NOT_APPROVED": "vendor not approved",
    "VENDOR_BLOCKED": "vendor blocked",
    "OFFER_NOT_YET_VALID": "not yet valid",
    "OFFER_EXPIRED": "offer expired",
    "DELIVERY_AFTER_NEED_BY": "delivery after need-by date",
}


def _no_valid_offer_rationale(offers: tuple[OfferEvidence, ...]) -> str:
    """Summarize why zero offers are eligible, from already-gathered evidence."""

    if not offers:
        return "No vendor offers exist for this product."
    counts: dict[str, int] = {}
    for offer in offers:
        if offer.status is not EvidenceStatus.REJECTED:
            continue
        for code in offer.reason_codes:
            counts[code] = counts.get(code, 0) + 1
    clauses = [
        f"{counts[code]} {'offer' if counts[code] == 1 else 'offers'} "
        f"rejected ({label})"
        for code, label in _REJECTION_LABEL.items()
        if counts.get(code)
    ]
    return "No eligible offer: " + ", ".join(clauses) + "."
```

Update the `NO_VALID_OFFER` branch (currently lines 186-196) to call it:

```python
        if item.skip_reason_code == "NO_VALID_OFFER":
            return {
                "evidence": (item,),
                "result": NoValidOfferResult(
                    product_id=candidate.product_id,
                    product_name=candidate.product_name,
                    rationale=_no_valid_offer_rationale(item.offers),
                ),
            }
```

- [x] **Step 4: Run tests to verify they pass**

```bash
cd /home/weam/StockAI && uv run pytest tests/unit/agent/test_walking_skeleton.py -v
```

Expected: PASS — the new tests plus every pre-existing test in the file,
including `test_graph_produces_no_valid_offer_result_for_zero_eligible_offers`
(currently lines 319-344), which only asserts `isinstance(result,
NoValidOfferResult)` and `product_id`, not the exact rationale string, so
it should already pass unmodified. If it fails, read its exact current
assertions first — it may need updating to not assert on the old static
string if a prior version of this file changed since this plan was
written.

- [x] **Step 5: Run focused quality checks**

```bash
cd /home/weam/StockAI && uv run ruff check src/procurement/agent/nodes/walking_skeleton.py tests/unit/agent/test_walking_skeleton.py
uv run mypy src/procurement/agent/nodes/walking_skeleton.py
```

Expected: both pass.

- [x] **Step 6: Commit**

```bash
git add src/procurement/agent/nodes/walking_skeleton.py tests/unit/agent/test_walking_skeleton.py
git commit -m "feat(agent): build no_valid_offer rationale from actual rejection reasons"
```

---

## Task 2: Show evidence for no_valid_offer cases

**Files:**
- Modify: `frontend/src/pages/RecommendationPage.tsx:99-105`
- Modify: `frontend/tests/recommendation.test.tsx`

**Interfaces:**
- Consumes: `NoValidOfferResult.product_id` (already exists on the
  frontend `NoValidOfferResult` interface in `client.ts`).

- [x] **Step 1: Write the failing test**

Read `frontend/tests/recommendation.test.tsx`'s `BASE_SCAN` fixture
(currently lines 7-90+) and the existing manual-review test at line 478
first, to match their exact structure. Add a new test, following the same
`{...BASE_SCAN, result: {...}}` pattern:

```ts
  it("shows evidence for a no_valid_offer result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          result: {
            outcome: "no_valid_offer",
            product_id: "product-101",
            product_name: "Fictional Safety Gloves",
            rationale: "No eligible offer: 1 offer rejected (vendor not approved).",
            evidence_limitations: [],
            read_only: true,
          },
        }),
      ),
    );

    render(
      <RecommendationPage
        scanId="scan-101"
        caseId="scan-101:product-101"
        onBack={vi.fn()}
      />,
    );

    expect(
      await screen.findByText(
        "No eligible offer: 1 offer rejected (vendor not approved).",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Deterministic procurement evidence" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Fictional Approved Supplies")).toBeInTheDocument();
  });
```

- [x] **Step 2: Run test to verify it fails**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/recommendation.test.tsx -t "shows evidence for a no_valid_offer"
```

Expected: FAIL — the "Deterministic procurement evidence" heading is not
found, since `findRecommendedEvidence` currently returns `null` for this
outcome.

- [x] **Step 3: Fix the gate**

In `frontend/src/pages/RecommendationPage.tsx`, replace
`findRecommendedEvidence` (currently lines 99-105):

```ts
function findRecommendedEvidence(scan: CaseDetail): ProcurementEvidenceRecord | null {
  const result = scan.result;
  if (
    result === null ||
    (result.outcome !== "approval_ready" && result.outcome !== "no_valid_offer")
  ) {
    return null;
  }
  return scan.evidence.find((item) => item.product_id === result.product_id) ?? null;
}
```

- [x] **Step 4: Run test to verify it passes**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/recommendation.test.tsx
```

Expected: PASS — the new test plus every pre-existing test in the file
(the gate change is additive — it only makes evidence show up for one
more outcome, it does not change behavior for `approval_ready` or
`manual_review`).

- [x] **Step 5: Run full frontend verification**

```bash
cd /home/weam/StockAI/frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build
```

Expected: all PASS.

- [x] **Step 6: Commit**

```bash
git add frontend/src/pages/RecommendationPage.tsx frontend/tests/recommendation.test.tsx
git commit -m "fix(frontend): show evidence for no_valid_offer case pages"
```

---

## Task 3: Label every offer-rejection reason

**Files:**
- Modify: `frontend/src/components/OfferComparison.tsx:23-28`
- Modify: `frontend/tests/offer-comparison.test.tsx`

**Interfaces:**
- Produces: `statusLabel(offer: OfferEvidence): string` now handles all
  five reason codes instead of one.

- [x] **Step 1: Write the failing tests**

Add to `frontend/tests/offer-comparison.test.tsx`, using the existing
`makeOffer` helper (currently lines 8-31) and following the exact style of
the existing `"shows a not-eligible badge with a vendor-not-approved
reason inline"` test (currently lines 48-61):

```ts
  it("labels every rejection reason code", () => {
    const cases: Array<[string, string]> = [
      ["VENDOR_BLOCKED", "Vendor blocked"],
      ["OFFER_NOT_YET_VALID", "Not yet valid"],
      ["OFFER_EXPIRED", "Offer expired"],
      ["DELIVERY_AFTER_NEED_BY", "Delivery too late"],
    ];
    for (const [code, label] of cases) {
      const offers = [
        makeOffer({
          offer_id: `offer-${code}`,
          status: "rejected",
          reason_codes: [code],
        }),
      ];
      render(<OfferComparison offers={offers} selectedOfferId={null} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      screen.getByText(label).closest("article")?.remove();
    }
  });

  it("labels the first matching reason in priority order for multi-reason offers", () => {
    const offers = [
      makeOffer({
        offer_id: "offer-multi",
        status: "rejected",
        reason_codes: ["OFFER_EXPIRED", "VENDOR_BLOCKED"],
      }),
    ];
    render(<OfferComparison offers={offers} selectedOfferId={null} />);

    expect(screen.getByText("Vendor blocked")).toBeInTheDocument();
    expect(screen.queryByText("Offer expired")).not.toBeInTheDocument();
  });
```

The first test renders four times in a loop within one `it` block — each
`render()` call mounts a fresh instance (Testing Library auto-cleans
between `it` blocks but not between manual `render()` calls within one, so
the `.remove()` line prevents duplicate-text collisions across loop
iterations within the same test; check this actually works when you run
it — if `screen.getByText` throws "multiple elements" on the second
iteration, split this into four separate `it` blocks instead, one per
reason code, matching the existing single-reason test's style exactly
rather than looping).

- [x] **Step 2: Run tests to verify they fail**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/offer-comparison.test.tsx -t "labels"
```

Expected: FAIL — all four non-`VENDOR_NOT_APPROVED` codes currently render
as "Not eligible", and the multi-reason offer shows "Not eligible" too
(no priority-order handling exists yet).

- [x] **Step 3: Extend `statusLabel`**

Replace `statusLabel` in `frontend/src/components/OfferComparison.tsx`
(currently lines 23-28):

```ts
const REJECTION_LABEL: Record<string, string> = {
  VENDOR_NOT_APPROVED: "Vendor not approved",
  VENDOR_BLOCKED: "Vendor blocked",
  OFFER_NOT_YET_VALID: "Not yet valid",
  OFFER_EXPIRED: "Offer expired",
  DELIVERY_AFTER_NEED_BY: "Delivery too late",
};
const REJECTION_PRIORITY = Object.keys(REJECTION_LABEL);

function statusLabel(offer: OfferEvidence): string {
  const matched = REJECTION_PRIORITY.find((code) =>
    offer.reason_codes.includes(code),
  );
  if (matched) {
    return REJECTION_LABEL[matched];
  }
  return offer.status === "eligible" ? "Eligible" : "Not eligible";
}
```

- [x] **Step 4: Run tests to verify they pass**

```bash
cd /home/weam/StockAI/frontend && npx vitest run tests/offer-comparison.test.tsx
```

Expected: PASS — new tests plus every pre-existing test in the file
(the existing `"vendor-not-approved"` test still passes since
`VENDOR_NOT_APPROVED` is still first in priority order, same behavior as
today for that single-reason case).

- [x] **Step 5: Run full frontend verification**

```bash
cd /home/weam/StockAI/frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build
```

Expected: all PASS.

- [x] **Step 6: Commit**

```bash
git add frontend/src/components/OfferComparison.tsx frontend/tests/offer-comparison.test.tsx
git commit -m "feat(frontend): label every offer-rejection reason code"
```

---

## Task 4: Final self-review and manual browser verification

**Files:** none (verification-only task, no code changes expected; fixes
if verification surfaces them)

- [x] **Step 1: Re-read the spec section by section**

Re-read `docs/superpowers/specs/2026-08-18-no-valid-offer-improvements-design.md`
end to end and confirm each Goal maps to a completed task above.

- [x] **Step 2: Full backend verification**

```bash
cd /home/weam/StockAI && uv run ruff format --check src tests scripts odoo
uv run ruff check src tests scripts odoo
uv run mypy
uv run pytest -q tests/unit
uv run pytest -q tests/integration
```

Expected: all PASS. If the full `tests/integration` directory hangs when
run as one invocation (a pre-existing sandbox flakiness observed earlier,
unrelated to any code in this repository — every test passes individually
or in two clean batches split roughly in half alphabetically), fall back
to running it in two batches instead of troubleshooting further.

- [x] **Step 3: Full frontend verification**

```bash
cd /home/weam/StockAI/frontend && npm run typecheck && npm run lint && npm test -- --run && npm run build
```

Expected: all PASS.

- [x] **Step 4: Manual browser verification**

```bash
cd /home/weam/StockAI && make compose-up
```

Sign in, run a manual scan against fictional data until it produces a
`no_valid_offer` case (the fictional dev Odoo seed includes a
"No-Valid-Offer" product scenario per prior sessions' work), open that
case's detail page, and confirm:
- The rationale states specific rejection reasons, not the old generic
  sentence.
- The evidence panel (inventory chart, offer cards, budget, applied
  preferences) renders below it.
- Every rejected offer card shows a specific reason label, not "Not
  eligible".

Then:

```bash
cd /home/weam/StockAI && make compose-down
```

- [x] **Step 5: Fix any issues found**

If manual verification surfaces a real bug, fix it with its own
failing-test-first cycle, then re-run Steps 2-4.

- [ ] **Step 6: Commit any fixes from Step 5**

```bash
git add -A
git commit -m "fix: address issues found during manual no-valid-offer verification"
```

(Skip this step entirely if Step 5 found nothing to fix.)
