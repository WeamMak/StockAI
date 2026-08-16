# T27 Live Repair and Demo Design

**Status:** Approved in conversation on 2026-08-16; written review pending.

## Purpose

This amendment closes the live T27 validation gap and makes the read-only user
experience match the three supplied visual references without introducing
T28 draft creation or T29 approval/confirmation behavior.

## Selected approach

`[Project decision]` Reuse the existing FastAPI, LangGraph, Bedrock, MCP, Odoo,
and T26A React seams. Repair only the T27 structured-recommendation boundary,
the fictional idempotent seed, and the existing Home/scan-detail presentation.
Do not add a batch-result API, new workflow states, ERP writes, approval actions,
or fabricated analytics.

The alternatives were rejected because a pixel-for-pixel implementation of all
three mockups requires unsupported batch, draft, approval, confirmation, and
historical-trend behavior, while a seed-only change would leave the live Bedrock
failure and misleading legacy presentation unresolved.

## User interface

`[Project decision]` The local `home_page.png`, `recommendation_details.png`,
and `Scan_details.png` files are visual references for layout, hierarchy,
spacing, cards, colors, and information grouping. They are not authorization to
invent data or future actions.

- Home uses only values derivable from the current scan API. Unsupported trend,
  approval-queue, confirmed-order, over-budget-exception, help, and insight
  actions are omitted or shown only when an existing route and truthful value
  support them.
- A successful T27 scan keeps the T26A four-card summary and adds the approved
  offer-level AI reasoning, trade-offs, risks, uncertainty, evidence
  limitations, budget acknowledgement, and applied preference snapshot.
- A genuine invalid or unavailable T27 model result uses the manual-review
  presentation, preserves deterministic evidence, and says that no draft was
  created.
- A historical approval-ready result remains visually approval ready. It is
  labeled as predating T27 offer-level validation and must not claim the new
  validation occurred. Missing new fields do not silently convert historical
  success into manual review.
- The scan-detail reference informs the single-result page's hierarchy only.
  The current workflow may gather several candidates but returns one selected
  recommendation; this amendment does not add multi-result scan aggregation.

## Fictional Odoo seed

`[Project decision]` The existing environment-prefixed, idempotent seed is
extended to exactly four demonstration products. Each product has exactly three
vendor offers and deterministic inventory, movement, offer, preference,
performance, and budget inputs:

1. No replenishment trigger: deterministically skipped and never sent to the
   LLM as an actionable case.
2. Replenishment required with three eligible offers: Bedrock chooses one from
   the safe set.
3. Replenishment required with two eligible offers and one deterministically
   rejected offer: Bedrock chooses only between the two eligible offers.
4. Replenishment required with zero eligible offers: deterministic
   `no_valid_offer`; Bedrock cannot override eligibility.

Stable references and environment prefixes keep dev and prod isolated. Reruns
reconcile dates, quantities, offers, tags, and configuration without creating
duplicates. The verifier checks exact product/offer counts and the inputs that
produce each expected outcome. The same image-contained seed is used by the
existing Argo-managed seed Job in both environments; no manual console-created
production data is introduced.

## Bedrock live repair

`[Project decision]` The live `manual_review` result is treated as a contract
bug, not as a reason to relax deterministic safety. Capture a sanitized
validation category at the adapter boundary, reproduce it with a focused test,
and align only the prompt, bounded context, JSON schema, or deterministic field
mapping proven responsible.

The repaired model may select only an eligible offer and provide bounded
judgment text. Application code continues to own and exactly validate product
and offer identity, quantity, unit price, normalized cost, budget status,
evidence digest, preference revision, hard enforcement, and required warnings.
The existing limit of two transient retries and one schema repair remains.

## Data flow and safety

Odoo supplies known-movement inventory evidence, coverage, offers, performance,
budget, and preferences through authenticated MCP. Deterministic code calculates
the forecast and safe offer set. Bedrock reasons only within that set. The
validator either accepts the exact evidence-bound result or returns read-only
manual review. No path in this amendment calls an Odoo write tool.

## Verification

- Focused seed and verifier tests prove exact idempotent dev/prod scenarios.
- Prompt, schema, Bedrock, graph, API, persistence, and legacy-compatibility
  tests cover the reproduced live failure and unchanged safety rejection cases.
- React tests cover Home, successful T27, genuine manual review, and historical
  approval-ready presentation using only supported data.
- Frontend type checking, linting, tests, coverage, and production build pass.
- Unit, real MCP transport integration, clean Odoo contract, Kubernetes, and
  release checks pass as required by T27.
- After publishing the complete image set, Argo dev is `Synced` and `Healthy`;
  `make smoke-dev` produces `approval_ready` through real Bedrock and records
  retry/repair/fallback and sanitized log evidence.
- Production seed reconciliation and smoke occur only through the normal exact-
  artifact protected promotion after dev acceptance.

## Exclusions

No draft PO, manager approval, confirmation, rejection, multi-result scan API,
predictive demand model, new AWS service, new MCP tool, or direct production
mutation is included.
