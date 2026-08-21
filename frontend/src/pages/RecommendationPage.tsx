import { useEffect, useState } from "react";

import {
  ApiError,
  getCase,
  isAbortError,
  type CaseDetail,
  type ProcurementEvidence as ProcurementEvidenceRecord,
  type ScanFailure,
  type Session,
} from "../api/client";
import { AuditTimeline } from "../components/AuditTimeline";
import { ManagerDecisionPanel } from "../components/ManagerDecisionPanel";
import { ProcurementEvidence } from "../components/ProcurementEvidence";
import { RecommendationHeader } from "../components/RecommendationHeader";
import { RefinementPanel } from "../components/RefinementPanel";
import { Icon } from "../components/Icon";
import { formatCurrency, formatDateTime } from "../presentation";

const DEFAULT_POLL_INTERVAL_MS = 1_000;
const DEFAULT_MAX_POLL_ATTEMPTS = 130;

interface RecommendationPageProps {
  scanId: string;
  caseId: string;
  session?: Session;
  onBack: () => void;
  pollIntervalMs?: number;
  maxPollAttempts?: number;
}

interface UiError {
  code: string;
  message: string;
}

function safeError(error: unknown): UiError {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message };
  }
  return {
    code: "REQUEST_FAILED",
    message: "The request could not be completed.",
  };
}

function ErrorState({ error }: { error: UiError | ScanFailure }) {
  const code = "code" in error ? error.code : error.error_code;
  return (
    <section className="notice notice--error" role="alert">
      <h2>Scan could not be completed</h2>
      <p>{error.message}</p>
      <p className="error-code">{code}</p>
    </section>
  );
}

function ScanHeading({
  completedAt,
  onBack,
  scanId,
}: {
  completedAt: string | null;
  onBack: () => void;
  scanId: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copyScanId() {
    if (navigator.clipboard === undefined) {
      return;
    }
    try {
      await navigator.clipboard.writeText(scanId);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="scan-heading">
      <button className="back-button" type="button" onClick={onBack}>
        ← Back to scans
      </button>
      <p className="eyebrow">Scan detail</p>
      <h1 id="scan-title">Procurement recommendation</h1>
      <div className="scan-metadata">
        <span className="identifier" title={scanId}>{scanId}</span>
        <button
          aria-label="Copy scan ID"
          className="copy-button"
          type="button"
          onClick={() => void copyScanId()}
        >
          {copied ? "Copied" : "Copy ID"}
        </button>
        {completedAt ? <span>Completed {formatDateTime(completedAt)}</span> : null}
      </div>
    </div>
  );
}

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

function RecommendationSummary({
  scan,
  evidence,
}: {
  scan: CaseDetail;
  evidence: ProcurementEvidenceRecord | null;
}) {
  if (scan.result === null) {
    return null;
  }
  const result = scan.result;
  const isLegacy =
    result.outcome === "approval_ready" && result.validation_level === "legacy";
  const rationale = "rationale" in result ? result.rationale : null;
  const tradeOffs = "trade_offs" in result ? result.trade_offs : null;
  const riskFlags = "risk_flags" in result ? result.risk_flags : null;
  const uncertainty = "uncertainty" in result ? result.uncertainty : null;
  const evidenceLimitations =
    "evidence_limitations" in result ? result.evidence_limitations : [];

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
            <span className={`validation-badge ${isLegacy ? "validation-badge--legacy" : ""}`}>
              <Icon name={isLegacy ? "document" : "check"} />
              {isLegacy ? "Predates T27 validation" : "Validated against evidence"}
            </span>
          </div>
          {rationale !== null ? (
            <p className="reasoning-rationale">{rationale}</p>
          ) : null}
          {tradeOffs !== null || riskFlags !== null ? (
            <div className="reasoning-details">
              {tradeOffs !== null ? (
                <section aria-labelledby="tradeoffs-title">
                  <h4 id="tradeoffs-title">Key trade-offs</h4>
                  {tradeOffs.length > 0 ? (
                    <ul>
                      {tradeOffs.map((item) => <li key={item}>{item}</li>)}
                    </ul>
                  ) : (
                    <p>No additional trade-offs recorded.</p>
                  )}
                </section>
              ) : null}
              {riskFlags !== null ? (
                <section aria-labelledby="risks-title">
                  <h4 id="risks-title">Risks and limitations</h4>
                  {riskFlags.length === 0 ? (
                    <p className="risk-state risk-state--clear">
                      <span className="summary-icon summary-icon--green"><Icon name="check" /></span>
                      No risk flags identified
                    </p>
                  ) : (
                    <div className="risk-state risk-state--warning">
                      <span className="summary-icon summary-icon--amber"><Icon name="alert" /></span>
                      <ul className="tag-list">
                        {riskFlags.map((flag) => (
                          <li key={flag}>{flag.replaceAll("_", " ")}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>
              ) : null}
            </div>
          ) : null}
          {uncertainty !== null ? (
            <div className="uncertainty-block">
              <h4>Uncertainty</h4>
              <p>{uncertainty}</p>
              {evidenceLimitations.length > 0 ? (
                <ul>
                  {evidenceLimitations.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </section>
      </div>
    </section>
  );
}

export function RecommendationPage({
  scanId,
  caseId,
  session = { user_id: "unknown", email: "", role: "officer" },
  onBack,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
}: RecommendationPageProps) {
  const [scan, setScan] = useState<CaseDetail | null>(null);
  const [requestError, setRequestError] = useState<UiError | null>(null);
  const [refinementNonce, setRefinementNonce] = useState(0);

  function handleRefined(nextScan: CaseDetail) {
    setScan(nextScan);
    setRequestError(null);
    setRefinementNonce((value) => value + 1);
  }

  useEffect(() => {
    let active = true;
    let attempts = 0;
    let timer: number | undefined;
    let controller: AbortController | undefined;

    async function poll() {
      attempts += 1;
      controller = new AbortController();
      try {
        const nextScan = await getCase(scanId, caseId, {
          signal: controller.signal,
        });
        if (!active) {
          return;
        }
        setScan(nextScan);
        setRequestError(null);
        if (["queued", "running", "approved", "rejected", "confirming"].includes(nextScan.status)) {
          if (attempts >= maxPollAttempts) {
            setRequestError({
              code: "POLL_LIMIT_REACHED",
              message:
                "The scan is still running. Return to the overview and check again shortly.",
            });
            return;
          }
          timer = window.setTimeout(() => void poll(), pollIntervalMs);
        }
      } catch (error) {
        if (active && !isAbortError(error)) {
          setRequestError(safeError(error));
        }
      }
    }

    void poll();
    return () => {
      active = false;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      controller?.abort();
    };
  }, [caseId, maxPollAttempts, pollIntervalMs, scanId, refinementNonce]);

  return (
    <section aria-labelledby="scan-title" className="page-stack">
      <ScanHeading
        completedAt={scan?.completed_at ?? null}
        onBack={onBack}
        scanId={caseId}
      />

      {requestError ? (
        <ErrorState error={requestError} />
      ) : scan === null ? (
        <div className="panel loading-skeleton loading-skeleton--detail" role="status">
          <span className="visually-hidden">Loading scan…</span>
          <span />
          <span />
          <span />
          <span />
        </div>
      ) : scan.status === "queued" || scan.status === "running" ? (
        <section className="panel" role="status" aria-live="polite">
          <p className={`status status--${scan.status}`}>
            {scan.status === "queued" ? "Queued" : "Running"}
          </p>
          <h2>Scan in progress</h2>
          <p>This page will update automatically.</p>
        </section>
      ) : scan.status === "failed" && scan.error?.retryable === false ? (
        <>
          <section className="notice notice--review">
            <h2>Manual review required</h2>
            <p>{scan.error.message}</p>
            <p className="error-code">{scan.error.error_code}</p>
          </section>
          {scan.evidence.map((item) => (
            <ProcurementEvidence
              evidence={item}
              key={item.evidence_id}
              selectedOfferId={null}
            />
          ))}
        </>
      ) : scan.status === "failed" && scan.error ? (
        <ErrorState error={scan.error} />
      ) : scan.result ? (
        <>
          {scan.status === "pending_approval" && scan.draft ? (
            <section className="notice notice--review" role="status">
              <h2>Pending manager approval</h2>
              <p>
                A draft purchase order has been created and is awaiting a
                manager decision.
              </p>
              <p>
                Draft PO #{scan.draft.po_id} ·{" "}
                {formatCurrency(scan.draft.amount_total, "USD")}
              </p>
            </section>
          ) : null}
          {scan.decision ? (
            <section className="notice notice--review" role="status">
              <h2>{scan.decision.status.replaceAll("_", " ")}</h2>
              <p>
                Fictional Odoo PO {scan.decision.po_reference} is {scan.decision.odoo_state}.
              </p>
            </section>
          ) : null}
          <ManagerDecisionPanel
            session={session}
            caseDetail={scan}
            onAccepted={() => setRefinementNonce((value) => value + 1)}
          />
          <RecommendationSummary scan={scan} evidence={findRecommendedEvidence(scan)} />
          {findRecommendedEvidence(scan) ? (
            <ProcurementEvidence
              evidence={findRecommendedEvidence(scan)!}
              selectedOfferId={
                scan.result.outcome === "approval_ready" && scan.result.offer_id !== null
                  ? scan.result.offer_id
                  : null
              }
            />
          ) : null}
          {scan.result.outcome === "approval_ready" &&
          scan.status === "succeeded" ? (
            <RefinementPanel
              scanId={scanId}
              caseId={caseId}
              refinementCount={scan.refinement_count}
              onRefined={handleRefined}
            />
          ) : null}
          {scan.status !== "succeeded" ? (
            <AuditTimeline caseId={caseId} refreshKey={refinementNonce} />
          ) : null}
        </>
      ) : (
        <ErrorState
          error={{
            code: "INVALID_RESPONSE",
            message: "The scan result is unavailable.",
          }}
        />
      )}
    </section>
  );
}
