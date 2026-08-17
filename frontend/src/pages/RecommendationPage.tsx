import { useEffect, useState } from "react";

import {
  ApiError,
  getScan,
  isAbortError,
  type ProcurementEvidence as ProcurementEvidenceRecord,
  type Scan,
  type ScanFailure,
} from "../api/client";
import { ProcurementEvidence } from "../components/ProcurementEvidence";
import { RecommendationHeader } from "../components/RecommendationHeader";
import { Icon } from "../components/Icon";
import { formatDateTime } from "../presentation";

const DEFAULT_POLL_INTERVAL_MS = 1_000;
const DEFAULT_MAX_POLL_ATTEMPTS = 130;

interface RecommendationPageProps {
  scanId: string;
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

function findRecommendedEvidence(scan: Scan): ProcurementEvidenceRecord | null {
  const result = scan.result;
  if (result === null || result.outcome !== "approval_ready") {
    return null;
  }
  return scan.evidence.find((item) => item.product_id === result.product_id) ?? null;
}

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
            <span className={`validation-badge ${isLegacy ? "validation-badge--legacy" : ""}`}>
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
                  {result.trade_offs.map((item) => <li key={item}>{item}</li>)}
                </ul>
              ) : (
                <p>No additional trade-offs recorded.</p>
              )}
            </section>
            <section aria-labelledby="risks-title">
              <h4 id="risks-title">Risks and limitations</h4>
              {result.risk_flags.length === 0 ? (
                <p className="risk-state risk-state--clear">
                  <span className="summary-icon summary-icon--green"><Icon name="check" /></span>
                  No risk flags identified
                </p>
              ) : (
                <div className="risk-state risk-state--warning">
                  <span className="summary-icon summary-icon--amber"><Icon name="alert" /></span>
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

export function RecommendationPage({
  scanId,
  onBack,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
}: RecommendationPageProps) {
  const [scan, setScan] = useState<Scan | null>(null);
  const [requestError, setRequestError] = useState<UiError | null>(null);

  useEffect(() => {
    let active = true;
    let attempts = 0;
    let timer: number | undefined;
    let controller: AbortController | undefined;

    async function poll() {
      attempts += 1;
      controller = new AbortController();
      try {
        const nextScan = await getScan(scanId, { signal: controller.signal });
        if (!active) {
          return;
        }
        setScan(nextScan);
        setRequestError(null);
        if (nextScan.status === "queued" || nextScan.status === "running") {
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
  }, [maxPollAttempts, pollIntervalMs, scanId]);

  return (
    <section aria-labelledby="scan-title" className="page-stack">
      <ScanHeading
        completedAt={scan?.completed_at ?? null}
        onBack={onBack}
        scanId={scanId}
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
