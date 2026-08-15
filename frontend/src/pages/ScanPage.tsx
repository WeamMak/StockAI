import { useEffect, useState } from "react";

import {
  ApiError,
  getScan,
  isAbortError,
  type Scan,
  type ScanFailure,
} from "../api/client";
import { ProcurementEvidence } from "../components/ProcurementEvidence";
import { Icon } from "../components/Icon";
import {
  formatCurrency,
  formatDate,
  formatDateTime,
  formatQuantity,
} from "../presentation";

const DEFAULT_POLL_INTERVAL_MS = 1_000;
const DEFAULT_MAX_POLL_ATTEMPTS = 130;

interface ScanPageProps {
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

function RecommendationSummary({ scan }: { scan: Scan }) {
  if (scan.result === null) {
    return null;
  }
  const evidence = scan.evidence.find(
    (item) => item.product_id === scan.result?.product_id,
  );
  const eligibleOfferCount =
    evidence?.offers.filter((offer) => offer.status === "eligible").length ?? 0;
  const onlyEligibleOffer =
    eligibleOfferCount === 1
      ? evidence?.offers.find((offer) => offer.status === "eligible") ?? null
      : null;
  const budget = evidence?.budget ?? null;

  return (
    <section
      aria-label="Recommendation summary"
      className="panel recommendation-summary"
    >
      <div className="result-heading">
        <div>
          <p className="eyebrow">Approval ready</p>
          <h2>{scan.result.product_name}</h2>
          <p className="muted identifier">{scan.result.product_id}</p>
        </div>
        <span className="read-only-badge">Read-only recommendation</span>
      </div>

      {evidence ? (
        <section aria-label="Decision highlights">
        <dl className="decision-grid">
          <div className="decision-card decision-card--coverage">
            <dt><span className="summary-icon summary-icon--green"><Icon name="coverage" /></span>Coverage</dt>
            <dd>
              {evidence.coverage.status.charAt(0).toUpperCase() +
                evidence.coverage.status.slice(1)}
              <small>{formatQuantity(evidence.coverage.covered_quantity)} covered</small>
            </dd>
          </div>
          <div className="decision-card decision-card--shortage">
            <dt><span className="summary-icon summary-icon--amber"><Icon name="shortage" /></span>Shortage</dt>
            <dd title={evidence.coverage.residual_quantity}>
              {formatQuantity(evidence.coverage.residual_quantity)}
              <small>Need by {formatDate(evidence.shortage.need_by_date)}</small>
            </dd>
          </div>
          <div className="decision-card decision-card--offer">
            <dt><span className="summary-icon summary-icon--blue"><Icon name="offer" /></span>Offer</dt>
            <dd>
              {onlyEligibleOffer
                ? formatCurrency(
                    onlyEligibleOffer.normalized_cost,
                    onlyEligibleOffer.company_currency,
                  )
                : `${eligibleOfferCount} eligible offers`}
              <small>
                {eligibleOfferCount} eligible {eligibleOfferCount === 1 ? "offer" : "offers"}
                {onlyEligibleOffer ? " · Only eligible offer" : ""}
              </small>
            </dd>
          </div>
          <div className="decision-card decision-card--recommendation">
            <dt><span className="summary-icon summary-icon--green"><Icon name="recommendation" /></span>Recommendation</dt>
            <dd>
              Approval ready
              <small>
                {budget
                  ? `${formatCurrency(budget.proposed_amount, budget.currency)} · ${budget.exception_required ? "Exception required" : "Within budget"}`
                  : "Budget not available"}
              </small>
            </dd>
          </div>
        </dl>
        </section>
      ) : null}

      <div className="recommendation-copy">
        <section aria-labelledby="rationale-title">
          <h3 id="rationale-title">Why this is recommended</h3>
          <p>{scan.result.rationale}</p>
        </section>
        <section aria-labelledby="risks-title">
          <h3 id="risks-title">Risks and limitations</h3>
          {scan.result.risk_flags.length === 0 ? (
            <p className="risk-state risk-state--clear">
              <span className="summary-icon summary-icon--green"><Icon name="check" /></span>
              No risk flags identified
            </p>
          ) : (
            <div className="risk-state risk-state--warning">
              <span className="summary-icon summary-icon--amber"><Icon name="alert" /></span>
              <ul className="tag-list">
                {scan.result.risk_flags.map((flag) => (
                  <li key={flag}>{flag.replaceAll("_", " ")}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

export function ScanPage({
  scanId,
  onBack,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
}: ScanPageProps) {
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
          <ProcurementEvidence evidence={scan.evidence} />
        </>
      ) : scan.status === "failed" && scan.error ? (
        <ErrorState error={scan.error} />
      ) : scan.result ? (
        <>
          <RecommendationSummary scan={scan} />
          <ProcurementEvidence evidence={scan.evidence} />
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
