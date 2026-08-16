# T27 Live Repair and Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a real evidence-validated T27 Bedrock recommendation, reconcile four deterministic fictional seed scenarios in dev/prod, and apply the supplied visual language without adding T28/T29 behavior.

**Architecture:** Keep the existing single-result FastAPI → LangGraph → MCP → Odoo → Bedrock path. Add explicit application-generated output guidance at the Bedrock boundary while retaining the strict semantic validator, model legacy success as a read-only compatibility result, and derive every Home value from the existing scan API. Extend only the existing idempotent Odoo seed and verifier.

**Tech Stack:** Python 3.12, LangGraph, boto3 Bedrock Converse, FastAPI/Pydantic, Odoo 19 shell seed, React 19, TypeScript, Vitest, pytest, Docker, Kubernetes/Argo CD.

## Global Constraints

- T27 remains read-only: no Odoo write tool, draft, approval, confirmation, rejection, or manager action.
- Bedrock may select only an eligible offer; application code owns every calculation and validates every copied value.
- Keep at most two transient retries and one schema-repair attempt.
- Do not accept wrapper objects, malformed JSON, missing warnings, copied-value mismatches, or broader JSON repair.
- The three root images guide visual hierarchy only; never fabricate unsupported analytics or multi-result scan data.
- Seed changes are environment-prefixed and idempotent; prod changes only through exact-artifact protected promotion.
- Do not commit the three user-owned root reference images unless the user separately requests it.

---

### Task 1: Repair the real Bedrock T27 output contract

**Files:**
- Modify: `src/procurement/ports/llm.py`
- Modify: `src/procurement/adapters/aws/bedrock.py`
- Modify: `src/procurement/agent/prompts/procurement_system.md`
- Modify: `src/procurement/agent/recommendation_schema.py`
- Modify: `src/procurement/agent/nodes/walking_skeleton.py`
- Test: `tests/unit/adapters/aws/test_bedrock.py`
- Test: `tests/unit/agent/test_prompt_boundary.py`
- Test: `tests/unit/agent/test_recommendation_schema.py`
- Test: `tests/unit/agent/test_walking_skeleton.py`

**Interfaces:**
- Consumes: `RecommendationRequest.evidence` containing only non-skipped evidence and eligible offers.
- Produces: the unchanged `StructuredRecommendation` contract or the unchanged safe `ManualReviewResult` fallback.
- Adds: `required_risk_flags(evidence, offer) -> tuple[str, ...]` as the single deterministic warning source used by context generation and semantic validation.

- [ ] **Step 1: Add red tests for the observed live request defect**

Assert that `_provider_request()` supplies a flat top-level contract, adds exact `required_risk_flags` to each eligible offer, uses `maxTokens: 2048`, and tells the repair attempt not to create a `recommend` wrapper. Retain a negative test proving this observed response remains rejected rather than unwrapped:

```python
wrapped = {"recommend": t27_payload()}
with pytest.raises(LlmOutputInvalidError):
    validate_recommendation_payload(wrapped, request, 10, 10)
```

- [ ] **Step 2: Run the focused tests and confirm the new assertions fail**

Run:

```bash
uv run pytest -q \
  tests/unit/adapters/aws/test_bedrock.py \
  tests/unit/agent/test_prompt_boundary.py \
  tests/unit/agent/test_recommendation_schema.py
```

Expected: failures for missing flat-contract guidance, missing per-offer warnings, and the old 1,024-token bound; existing malformed-output negatives remain green.

- [ ] **Step 3: Centralize deterministic required-warning calculation**

Move the existing warning calculation to `src/procurement/ports/llm.py` without changing its rules:

```python
def required_risk_flags(
    evidence: ProcurementEvidence,
    offer: OfferEvidence,
) -> tuple[str, ...]:
    flags: set[str] = set()
    if evidence.budget is None:
        flags.add("BUDGET_UNAVAILABLE")
    elif evidence.budget.exception_required:
        flags.add("BUDGET_EXCEPTION_REQUIRED")
    if offer.performance.history_status == "limited":
        flags.add("LIMITED_VENDOR_HISTORY")
    # Add ADVISORY_PREMIUM_EXCEEDED from the exact applied offer result.
    return tuple(sorted(flags))
```

Use this function in `validate_recommendation_payload()` so safety behavior is unchanged.

- [ ] **Step 4: Add explicit bounded model guidance**

In `_message()`, add `required_risk_flags` to each eligible serialized offer and append one machine-generated `output_contract` object listing the exact top-level fields and allowed decision values. Update the system prompt to say:

```text
Set the top-level `decision` field to `recommend` or `manual_review`.
Never create a field or wrapper named `recommend` or `manual_review`.
Copy the selected offer's application-generated `required_risk_flags`.
```

Set `inferenceConfig.maxTokens` to `2_048`. The context remains capped at 200 KB and the retry/repair counts remain unchanged. The repair message repeats the flat-object rule and required-warning rule but never echoes raw model output.

- [ ] **Step 5: Attach a bounded validation category without raw output**

Extend `LlmOutputInvalidError` with a safe category limited to `decode`, `schema`, `evidence`, or `warning`. Propagate only that category to the existing sanitized LangGraph failure event; do not add model text, prompts, evidence bodies, or identifiers to logs or metric labels.

- [ ] **Step 6: Run the complete focused agent boundary**

Run:

```bash
uv run pytest -q \
  tests/unit/adapters/aws/test_bedrock.py \
  tests/unit/agent/test_prompt_boundary.py \
  tests/unit/agent/test_recommendation_schema.py \
  tests/unit/agent/test_walking_skeleton.py \
  tests/unit/bootstrap/test_api.py
```

Expected: all pass, including wrapper rejection, exact copied fields, required warnings, two transient retries, one repair, and safe fallback.

- [ ] **Step 7: Commit the isolated model-boundary repair**

```bash
git add src/procurement/ports/llm.py \
  src/procurement/adapters/aws/bedrock.py \
  src/procurement/agent/prompts/procurement_system.md \
  src/procurement/agent/recommendation_schema.py \
  src/procurement/agent/nodes/walking_skeleton.py \
  tests/unit/adapters/aws/test_bedrock.py \
  tests/unit/agent/test_prompt_boundary.py \
  tests/unit/agent/test_recommendation_schema.py \
  tests/unit/agent/test_walking_skeleton.py
git commit -m "fix(agent): repair T27 Bedrock output contract"
```

### Task 2: Preserve truthful historical results and apply the T27 UI references

**Files:**
- Modify: `src/procurement/agent/state.py`
- Modify: `src/procurement/api/services/scans.py`
- Modify: `src/procurement/api/routes/scans.py`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/OverviewPage.tsx`
- Modify: `frontend/src/pages/ScanPage.tsx`
- Modify: `frontend/src/styles.css`
- Test: `tests/unit/api/test_scans.py`
- Test: `frontend/tests/api-client.test.ts`
- Test: `frontend/tests/overview.test.tsx`
- Test: `frontend/tests/scan.test.tsx`

**Interfaces:**
- Consumes: current scan list/detail responses and stored pre-T27 approval-ready records.
- Produces: `approval_ready` results with `validation_level: "t27" | "legacy"`, while T27-only offer fields remain required only when `validation_level == "t27"`.
- Preserves: `manual_review` exclusively for genuine model/evidence/dependency fallback.

- [ ] **Step 1: Add failing API compatibility tests**

Add a stored pre-T27 approval-ready record with no offer-level fields and assert the API returns:

```json
{
  "outcome": "approval_ready",
  "validation_level": "legacy",
  "offer_id": null,
  "risk_flags": ["LEGACY_RECOMMENDATION"]
}
```

Add a new T27 record assertion with `validation_level: "t27"` and all strict offer fields. Keep actual invalid output as `manual_review`.

- [ ] **Step 2: Run the focused API tests and confirm the legacy case fails**

Run:

```bash
uv run pytest -q tests/unit/api/test_scans.py
```

Expected: the legacy record is currently downgraded to `manual_review`.

- [ ] **Step 3: Implement the explicit legacy compatibility result**

Add a distinct internal legacy result and a strict Pydantic response branch. Do not synthesize an offer ID or copied T27 values. New T27 success continues through the existing strict result type; only records proven to have the older stored approval-ready shape receive `validation_level: "legacy"`.

- [ ] **Step 4: Add failing React tests for truthful counts and presentations**

Cover:

```text
Home: approval-ready count includes only approval_ready results.
Home: needs-review count includes manual_review and terminal no-valid-offer results.
Home: scan rows show outcome labels instead of calling every success approval ready.
Detail: T27 success shows AI reasoning plus read-only/no-draft copy.
Detail: legacy success keeps the four-card presentation and shows “Predates T27 validation”.
Detail: actual fallback retains the manual-review panel.
```

- [ ] **Step 5: Implement the reference-led T27 UI**

Reuse existing components and CSS classes. Follow the supplied images for the overview summary, recommendation header, decision cards, reasoning panel, evidence grouping, and status styling. Derive every number from `Scan[]` or the selected scan. Do not implement mockup-only trends, approval queues, confirmed-order actions, help routes, insight routes, or multi-result scan aggregation.

- [ ] **Step 6: Run the frontend quality gate**

Run:

```bash
cd frontend
npm run typecheck
npm run lint
npm test -- --run
npm run build
```

Expected: all commands pass and no unsupported control is rendered.

- [ ] **Step 7: Commit the compatibility and UI slice**

```bash
git add src/procurement/agent/state.py \
  src/procurement/api/services/scans.py \
  src/procurement/api/routes/scans.py \
  tests/unit/api/test_scans.py \
  frontend/src/api/client.ts \
  frontend/src/pages/OverviewPage.tsx \
  frontend/src/pages/ScanPage.tsx \
  frontend/src/styles.css \
  frontend/tests/api-client.test.ts \
  frontend/tests/overview.test.tsx \
  frontend/tests/scan.test.tsx
git commit -m "fix(ui): preserve T27 and legacy recommendation states"
```

### Task 3: Reconcile four exact idempotent Odoo demo scenarios

**Files:**
- Modify: `scripts/odoo/seed.py`
- Modify: `scripts/odoo/verify_seed.py`
- Modify: `tests/integration/test_odoo_bootstrap.py`
- Modify: `tests/integration/test_mcp_real_odoo.py`
- Modify: `tests/config/test_odoo_image_contract.py`

**Interfaces:**
- Consumes: `STOCKAI_ODOO_SEED_ENVIRONMENT=dev|prod` and the existing Odoo shell environment.
- Produces: four active environment-prefixed demonstration products, exactly three supplier offers per product, and stable scenario metadata from `verify_seed.py`.
- Keeps: historical purchase/receipt records required for vendor-performance and coverage evidence; obsolete demo products are archived rather than deleted.

- [ ] **Step 1: Add failing exact seed-contract assertions**

For both environment prefixes, require verifier output shaped as:

```json
{
  "scenario_outcomes": {
    "no-replenishment": "skipped",
    "three-eligible": "llm_safe_set_3",
    "two-eligible": "llm_safe_set_2",
    "no-valid-offer": "no_valid_offer"
  },
  "active_products": 4,
  "offers_per_product": 3
}
```

Run the seed twice and assert identical active stable references and counts.

- [ ] **Step 2: Run the focused contract and observe failure**

Run:

```bash
uv run pytest -q \
  tests/config/test_odoo_image_contract.py \
  tests/integration/test_odoo_bootstrap.py::test_seed_and_verification_are_stable_across_reruns
```

Expected: the current seed exposes three active products and uneven offer counts.

- [ ] **Step 3: Implement four stable scenarios with three offers each**

Use four stable codes per environment:

```text
STOCKAI-<ENV>-NO-NEED
STOCKAI-<ENV>-CHOICE-3
STOCKAI-<ENV>-CHOICE-2
STOCKAI-<ENV>-NO-OFFER
```

Reconcile one no-demand inventory scenario and three shortage scenarios. Use three approved fictional vendors with product-specific price and lead-time supplier info so all three are timely for `CHOICE-3`, exactly two are timely for `CHOICE-2`, and all three arrive after need-by for `NO-OFFER`. Archive obsolete active demo templates; never delete historical orders, receipts, or audit-relevant records.

- [ ] **Step 4: Make verification prove outcomes, not just counts**

`verify_seed.py` must inspect active templates, supplier-info rows, reordering rules, confirmed demand dates, vendor approval, budgets/preferences, and stable references. It emits only bounded identifiers, counts, and expected outcome labels—no API keys, credentials, or business payload dumps.

- [ ] **Step 5: Run the clean Odoo and real-MCP paths**

Run:

```bash
make odoo-contract
uv run pytest -q tests/integration/test_mcp_real_odoo.py
```

Expected: the clean image installs, both environment seeds rerun idempotently, MCP returns the four expected evidence scenarios, and LangGraph selects only from an eligible safe set.

- [ ] **Step 6: Commit the seed slice**

```bash
git add scripts/odoo/seed.py scripts/odoo/verify_seed.py \
  tests/config/test_odoo_image_contract.py \
  tests/integration/test_odoo_bootstrap.py \
  tests/integration/test_mcp_real_odoo.py
git commit -m "feat(seed): add four deterministic procurement scenarios"
```

### Task 4: Run regression, publish dev, and close live acceptance

**Files:**
- Modify after actual results: `docs/implementation-status.md`
- Generated by existing workflow only: `deploy/releases/dev.json`
- Generated by existing workflow only: `deploy/kubernetes/overlays/dev/kustomization.yaml`

**Interfaces:**
- Consumes: Tasks 1–3 and the existing four-image dev GitHub/Argo release path.
- Produces: one exact immutable dev release with authenticated Bedrock smoke evidence.

- [ ] **Step 1: Run local repository regression**

Run:

```bash
make ACTIONLINT=/tmp/actionlint check
make test-integration
make kubernetes-validate
git diff --check
```

Expected: all local code, frontend, real-transport, observability, render, and schema checks pass. Network-blocked pinned-resource fetches are rerun with network permission, not bypassed.

- [ ] **Step 2: Merge the reviewed feature into dev and let GitOps publish**

```bash
git switch dev
git pull --ff-only origin dev
git merge --no-ff feature/t27
git push origin dev
```

Wait for the existing dev-images workflow to publish all four immutable images, commit only generated dev desired state, and for Argo `stockai-dev` to become `Synced` and `Healthy`.

- [ ] **Step 3: Fast-forward local dev before smoke**

```bash
git pull --ff-only origin dev
uv run python -m scripts.release.verify_manifest deploy/releases/dev.json
```

The first command prevents the already diagnosed stale-local-manifest failure.
The second verifies dev manifest integrity without incorrectly requiring the
older prod manifest to contain the not-yet-promoted T27 release.

- [ ] **Step 4: Run authenticated live dev acceptance**

```bash
make smoke-dev
```

Expected: a real Cognito-authenticated scan reaches Odoo through MCP, invokes `openai.gpt-oss-20b-1:0`, returns `approval_ready`, persists the exact recommendation, increments successful LLM/token/latency metrics, and emits no fallback for that request. Confirm the seed Job reports the four bounded scenarios and no Odoo draft was created.

- [ ] **Step 5: Record only observed results**

Update `docs/implementation-status.md` with exact test counts, release ID, Argo revision, smoke run ID, evidence digest, seed verification output, and remaining production promotion state. Do not claim prod seed reconciliation or smoke before protected promotion actually runs.

- [ ] **Step 6: Commit status and prepare exact promotion**

```bash
git switch feature/t27
git fetch origin
make promote-dev
make verify-release
git add docs/implementation-status.md deploy/releases/prod.json \
  deploy/kubernetes/overlays/prod/kustomization.yaml
git commit -m "chore(release): prepare validated T27 production promotion"
```

Push `feature/t27`, open the PR to protected `main`, and let the existing main workflow promote the exact dev-tested four-image digest map without rebuild or direct `kubectl` deployment.
