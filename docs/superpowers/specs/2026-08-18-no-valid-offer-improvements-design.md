# No-valid-offer page improvements — design

## Status

Approved by user 2026-08-18. Ready for implementation planning.

## Context

While manually reviewing the deployed dev environment, the user found that
`RecommendationPage.tsx` shows no evidence at all for cases with the
`no_valid_offer` outcome — just a badge, title, and a generic one-line
rationale. This was raised alongside a separate, larger idea (letting an
officer refine a recommendation with situational context), which the user
chose to split into its own sub-project (see the decomposition decision
below) since it is a substantially bigger, independent capability with no
shared code path.

This spec covers only the smaller, contained fix: making the `no_valid_offer`
outcome show real evidence and a real reason, using data the deterministic
evidence-gathering step already collects but never surfaces.

## Decomposition decision

The user originally proposed one combined sub-project covering both this
work and a "bounded case refinement" feature (letting an officer send a
short preference message to re-evaluate a case, capped at a small number of
attempts per case). During brainstorming it was split into two fully
separate sub-projects — this one (no_valid_offer improvements, small,
self-contained) ships first; the refinement feature gets its own
brainstorm → spec → plan cycle afterward, since it requires new persistence
(a refinement counter per case), a new API route, and new UI, none of which
this work depends on or blocks.

## Goals

- Show the evidence panel (inventory chart, offer comparison, budget,
  applied preferences) on `no_valid_offer` case pages, the same panel
  `approval_ready` pages already render.
- Replace the static rationale ("No approved vendor offer is eligible for
  this product.") with one built from the actual reasons the evidence's
  offers were rejected.
- Fix the offer-card labels so every rejection reason has real text instead
  of a generic "Not eligible" — a gap that becomes immediately visible once
  offer cards start rendering for `no_valid_offer` pages for the first time.

## Non-goals

- No LLM involvement anywhere in this outcome. `no_valid_offer` is produced
  entirely inside `gather_evidence` (a deterministic node) before the LLM is
  ever invoked — an existing test asserts `llm.requests == []` for this
  path, and that must remain true. The new rationale is built with plain
  Python string logic over already-gathered evidence, not a new model call.
- No changes to `manual_review` or `approval_ready` page behavior.
- No bounded case refinement, preference messages, or any of that separate
  sub-project's scope — tracked independently.
- No new reason codes. `evaluate_offer` (`domain/policy/offers.py:66-76`)
  already produces exactly five: `VENDOR_NOT_APPROVED`, `VENDOR_BLOCKED`,
  `OFFER_NOT_YET_VALID`, `OFFER_EXPIRED`, `DELIVERY_AFTER_NEED_BY`. This
  work only adds readable text for the ones that already exist.

## Current state

**Evidence gating.** `frontend/src/pages/RecommendationPage.tsx:99-105`:

```ts
function findRecommendedEvidence(scan: CaseDetail): ProcurementEvidenceRecord | null {
  const result = scan.result;
  if (result === null || result.outcome !== "approval_ready") {
    return null;
  }
  return scan.evidence.find((item) => item.product_id === result.product_id) ?? null;
}
```

This excludes `no_valid_offer` even though `NoValidOfferResult` (both the
backend dataclass and the frontend `NoValidOfferResult` interface) already
carries `product_id`, and the matching evidence item is already present in
`scan.evidence` — it is fetched and returned by the API today, just never
looked up for this outcome.

**Static rationale.** `src/procurement/agent/nodes/walking_skeleton.py:186-196`,
inside `gather_evidence`'s `NO_VALID_OFFER` branch:

```python
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
```

`item` here is the full `ProcurementEvidence` for this candidate, including
`item.offers: tuple[OfferEvidence, ...]` — every offer considered, each
with `status: EvidenceStatus` (`"eligible"` / `"rejected"`) and, when
rejected, a non-empty `reason_codes: tuple[str, ...]`
(`domain/policy/evidence.py:181-216`). This branch only fires when zero
offers are eligible (`adapters/odoo/evidence.py:319`,
`elif not eligible: skip_reason = "NO_VALID_OFFER"`), which covers two
distinct real situations `item.offers` lets us tell apart: the tuple is
empty (no offers exist at all for this product), or it is non-empty but
every offer was rejected.

**Offer-card labels.** `frontend/src/components/OfferComparison.tsx:23-28`:

```ts
function statusLabel(offer: OfferEvidence): string {
  if (offer.reason_codes.includes("VENDOR_NOT_APPROVED")) {
    return "Vendor not approved";
  }
  return offer.status === "eligible" ? "Eligible" : "Not eligible";
}
```

Only one of the five possible reason codes has real text; the other four
rejected-offer cases all show the generic "Not eligible".

## Design

### Backend: deterministic rationale builder

Add a small pure helper, `_no_valid_offer_rationale(offers:
tuple[OfferEvidence, ...]) -> str`, next to `WalkingSkeletonNodes` in
`src/procurement/agent/nodes/walking_skeleton.py`:

- If `offers` is empty: `"No vendor offers exist for this product."`
- Otherwise, tally every `reason_codes` entry across all offers with
  `status is EvidenceStatus.REJECTED` (an offer with more than one reason
  contributes to more than one bucket — this is a count of *reasons*, not
  of offers, matching the approved "grouped count by reason" design). Walk
  a fixed priority order — `VENDOR_NOT_APPROVED`, `VENDOR_BLOCKED`,
  `OFFER_NOT_YET_VALID`, `OFFER_EXPIRED`, `DELIVERY_AFTER_NEED_BY` — same
  order `evaluate_offer` itself checks them in
  (`domain/policy/offers.py:66-76`), emitting only reasons with a nonzero
  count, and join them into one sentence:

  ```
  "No eligible offer: 2 offers rejected (vendor not approved), 1 offer rejected (delivery after need-by date)."
  ```

  Singular/plural ("1 offer" vs "2 offers") handled per clause. A single
  reason produces one clause: `"No eligible offer: 3 offers rejected
  (vendor not approved)."`

  Reason-code → phrase mapping (used verbatim in the sentence, lowercase
  since it's mid-sentence):

  | Code | Phrase |
  |---|---|
  | `VENDOR_NOT_APPROVED` | vendor not approved |
  | `VENDOR_BLOCKED` | vendor blocked |
  | `OFFER_NOT_YET_VALID` | not yet valid |
  | `OFFER_EXPIRED` | offer expired |
  | `DELIVERY_AFTER_NEED_BY` | delivery after need-by date |

`gather_evidence`'s `NO_VALID_OFFER` branch calls this helper for
`NoValidOfferResult.rationale` instead of the static string. Nothing else
in that branch changes — no LLM call is added, `evidence_limitations`
stays `()` (unchanged from today).

### Frontend: evidence gating

Change `findRecommendedEvidence`'s guard from `result.outcome !==
"approval_ready"` to a check that accepts either outcome:

```ts
function findRecommendedEvidence(scan: CaseDetail): ProcurementEvidenceRecord | null {
  const result = scan.result;
  if (result === null || (result.outcome !== "approval_ready" && result.outcome !== "no_valid_offer")) {
    return null;
  }
  return scan.evidence.find((item) => item.product_id === result.product_id) ?? null;
}
```

`manual_review` correctly stays excluded (`ManualReviewResult` has no
`product_id`). No other change is needed in `RecommendationPage.tsx` — the
existing render path already calls `<ProcurementEvidence>` whenever
`findRecommendedEvidence` returns non-null, and already computes
`selectedOfferId` as `null` for any outcome other than `approval_ready`
(`RecommendationPage.tsx:311-315`), which is exactly correct here too:
`no_valid_offer` has no selected offer, so the offer-comparison panel will
render every offer with its rejection label and no "AI selected" badge.

### Frontend: offer-card labels

Extend `statusLabel` in `OfferComparison.tsx` to check all five reason
codes in the same fixed priority order as the backend, falling back to the
existing generic labels only when `reason_codes` is empty (i.e., the offer
is actually eligible):

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

An offer with multiple reason codes shows the first match in priority
order (same convention as the backend's grouping order), matching the
existing single-reason behavior's precedent exactly (today's code already
special-cases `VENDOR_NOT_APPROVED` ahead of the generic fallback — this
generalizes that same pattern to all five instead of just one).

### Testing

- Backend: unit tests for `_no_valid_offer_rationale` — empty offers tuple,
  a single rejected offer with one reason, several rejected offers sharing
  one reason (pluralization), several rejected offers with different
  reasons (ordering and clause-joining), and one offer with multiple
  reason codes (contributes to multiple buckets). Extend the existing
  `gather_evidence` NO_VALID_OFFER test(s) in
  `tests/unit/agent/test_walking_skeleton.py` to assert the new rationale
  text instead of the old static string.
- Frontend: extend `RecommendationPage`'s existing no_valid_offer test
  coverage (`recommendation.test.tsx`) to assert the evidence panel now
  renders. Extend `OfferComparison`'s test coverage
  (`offer-comparison.test.tsx`) with one case per newly-handled reason
  code, plus a multi-reason offer asserting priority-order selection.

## Open questions

None — all decisions in this document were confirmed with the user during
brainstorming on 2026-08-18.
