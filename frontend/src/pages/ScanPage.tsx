import { useEffect, useState } from "react";

import {
  ApiError,
  getScan,
  isAbortError,
  type Scan,
  type ScanFailure,
} from "../api/client";

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
      <div>
        <button className="back-button" type="button" onClick={onBack}>
          ← Back to scans
        </button>
        <p className="eyebrow">Scan detail</p>
        <h1 id="scan-title">{scanId}</h1>
      </div>

      {requestError ? (
        <ErrorState error={requestError} />
      ) : scan === null ? (
        <p className="panel" role="status">
          Loading scan…
        </p>
      ) : scan.status === "queued" || scan.status === "running" ? (
        <section className="panel" role="status" aria-live="polite">
          <p className={`status status--${scan.status}`}>
            {scan.status === "queued" ? "Queued" : "Running"}
          </p>
          <h2>Scan in progress</h2>
          <p>This page will update automatically.</p>
        </section>
      ) : scan.status === "failed" && scan.error?.retryable === false ? (
        <section className="notice notice--review">
          <h2>Manual review required</h2>
          <p>{scan.error.message}</p>
          <p className="error-code">{scan.error.error_code}</p>
        </section>
      ) : scan.status === "failed" && scan.error ? (
        <ErrorState error={scan.error} />
      ) : scan.result ? (
        <article className="panel result-card">
          <div className="result-heading">
            <div>
              <p className="eyebrow">Approval-ready result</p>
              <h2>{scan.result.product_name}</h2>
              <p className="muted">{scan.result.product_id}</p>
            </div>
            <span className="read-only-badge">Read-only recommendation</span>
          </div>
          <section aria-labelledby="rationale-title">
            <h3 id="rationale-title">Agent rationale</h3>
            <p>{scan.result.rationale}</p>
          </section>
          <section aria-labelledby="risks-title">
            <h3 id="risks-title">Risk flags</h3>
            {scan.result.risk_flags.length === 0 ? (
              <p>No risk flags were returned.</p>
            ) : (
              <ul className="tag-list">
                {scan.result.risk_flags.map((flag) => (
                  <li key={flag}>{flag.replaceAll("_", " ")}</li>
                ))}
              </ul>
            )}
          </section>
        </article>
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
