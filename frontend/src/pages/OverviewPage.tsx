import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  createManualScan,
  isAbortError,
  listScans,
  type Scan,
} from "../api/client";
import { Icon } from "../components/Icon";
import { formatDateTime } from "../presentation";

interface OverviewPageProps {
  onSelectScan: (scanId: string) => void;
  view?: "home" | "scans";
}

function safeMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The request could not be completed.";
}

function displayStatus(status: Scan["status"]): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function displayScanOutcome(scan: Scan): string {
  if (scan.status === "succeeded" && scan.result?.outcome === "approval_ready") {
    return "Approval ready";
  }
  if (scan.status === "succeeded" && scan.result?.outcome === "manual_review") {
    return "Manual review";
  }
  return displayStatus(scan.status);
}

function scanCounts(scans: Scan[]) {
  let inProgress = 0;
  let approvalReady = 0;
  let needsReview = 0;
  for (const scan of scans) {
    if (scan.status === "queued" || scan.status === "running") {
      inProgress += 1;
    } else if (
      scan.status === "succeeded" &&
      scan.result?.outcome === "approval_ready"
    ) {
      approvalReady += 1;
    } else if (
      scan.status === "succeeded" &&
      scan.result?.outcome === "manual_review"
    ) {
      needsReview += 1;
    } else if (scan.status === "failed" && scan.error?.retryable === false) {
      needsReview += 1;
    }
  }
  return { approvalReady, inProgress, needsReview, total: scans.length };
}

export function OverviewPage({ onSelectScan, view = "home" }: OverviewPageProps) {
  const [scans, setScans] = useState<Scan[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const startController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void listScans({ signal: controller.signal })
      .then((loadedScans) => {
        setScans(loadedScans);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setLoadError(safeMessage(error));
        }
      });
    return () => {
      controller.abort();
      startController.current?.abort();
    };
  }, []);

  async function startManualScan() {
    const controller = new AbortController();
    startController.current = controller;
    setIsStarting(true);
    setStartError(null);
    try {
      const scan = await createManualScan({ signal: controller.signal });
      onSelectScan(scan.scan_id);
    } catch (error) {
      if (!isAbortError(error)) {
        setStartError(safeMessage(error));
      }
    } finally {
      if (startController.current === controller) {
        startController.current = null;
        setIsStarting(false);
      }
    }
  }

  const counts = scans === null ? null : scanCounts(scans);

  return (
    <section aria-labelledby="overview-title" className="page-stack">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Procurement workspace</p>
          <h1 id="overview-title">
            {view === "home" ? "Procurement overview" : "Procurement scans"}
          </h1>
          <p className="lede">
            {view === "home"
              ? "Monitor scan activity and recent procurement recommendations."
              : "Run and review read-only replenishment recommendations."}
          </p>
        </div>
        <button
          className="primary-button"
          type="button"
          onClick={() => void startManualScan()}
          disabled={isStarting}
          aria-busy={isStarting}
        >
          {isStarting ? "Starting scan…" : "Run manual scan"}
        </button>
      </div>

      {startError ? (
        <p className="notice notice--error" role="alert">
          {startError}
        </p>
      ) : null}

      {view === "home" && counts ? (
        <section aria-label="Scan summary" className="overview-summary">
          <article>
            <span className="summary-icon summary-icon--blue"><Icon name="document" /></span>
            <strong>{counts.total}</strong>
            <span>Total</span>
          </article>
          <article>
            <span className="summary-icon summary-icon--amber"><Icon name="scans" /></span>
            <strong>{counts.inProgress}</strong>
            <span>In progress</span>
          </article>
          <article>
            <span className="summary-icon summary-icon--green"><Icon name="check" /></span>
            <strong>{counts.approvalReady}</strong>
            <span>Approval ready</span>
          </article>
          <article>
            <span className="summary-icon summary-icon--red"><Icon name="alert" /></span>
            <strong>{counts.needsReview}</strong>
            <span>Needs review</span>
          </article>
        </section>
      ) : null}

      <section aria-labelledby="recent-scans-title" className="panel">
        <h2 id="recent-scans-title">
          {view === "home" ? "Recent scans" : "All scans"}
        </h2>
        {loadError ? (
          <p className="notice notice--error" role="alert">
            {loadError}
          </p>
        ) : scans === null ? (
          <div className="loading-skeleton" role="status">
            <span className="visually-hidden">Loading scans…</span>
            <span />
            <span />
            <span />
          </div>
        ) : scans.length === 0 ? (
          <div className="empty-state">
            <h3>No scans yet</h3>
            <p>Run a manual scan to create the first result.</p>
          </div>
        ) : (
          <ul className="scan-list" aria-label="Recent procurement scans">
            {scans.map((scan) => (
              <li key={scan.scan_id}>
                <button
                  className="scan-link"
                  type="button"
                  onClick={() => onSelectScan(scan.scan_id)}
                  aria-label={`Open ${scan.scan_id}, ${displayScanOutcome(scan)}`}
                >
                  <span>
                    <strong>{scan.scan_id}</strong>
                    <small>
                      {scan.trigger === "manual" ? "Manual" : "Scheduled"} scan
                      {scan.completed_at
                        ? ` · Completed ${formatDateTime(scan.completed_at)}`
                        : ` · Started ${formatDateTime(scan.created_at)}`}
                    </small>
                  </span>
                  <span className={`status status--${scan.status}`}>
                    {displayScanOutcome(scan)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
