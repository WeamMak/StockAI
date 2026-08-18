import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  createManualScan,
  isAbortError,
  listRecentCases,
  listScans,
  type CaseSummary,
  type ScanAggregate,
} from "../api/client";
import { Icon } from "../components/Icon";
import { formatCurrency, formatDate, formatDateTime, OUTCOME_LABEL } from "../presentation";

interface OverviewPageProps {
  onSelectScan: (scanId: string) => void;
  onSelectCase: (scanId: string, caseId: string) => void;
  view?: "home" | "scans";
}

function safeMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The request could not be completed.";
}

function displayStatus(status: ScanAggregate["status"]): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

/** One scan can now produce several outcomes; pick the most attention-worthy
 * one to represent it in the list, deferring any richer per-outcome
 * breakdown to the scan-detail page. */
function representativeOutcome(scan: ScanAggregate): "manual_review" | "approval_ready" | null {
  if ((scan.outcomeCounts.manual_review ?? 0) > 0) {
    return "manual_review";
  }
  if ((scan.outcomeCounts.approval_ready ?? 0) > 0) {
    return "approval_ready";
  }
  return null;
}

function displayScanOutcome(scan: ScanAggregate): string {
  if (scan.status === "succeeded") {
    const outcome = representativeOutcome(scan);
    if (outcome === "approval_ready") {
      return "Approval ready";
    }
    if (outcome === "manual_review") {
      return "Manual review";
    }
  }
  return displayStatus(scan.status);
}

function scanCounts(scans: ScanAggregate[]) {
  let inProgress = 0;
  let approvalReady = 0;
  let needsReview = 0;
  for (const scan of scans) {
    if (scan.status === "queued" || scan.status === "running") {
      inProgress += 1;
    } else if (scan.status === "succeeded") {
      const outcome = representativeOutcome(scan);
      if (outcome === "approval_ready") {
        approvalReady += 1;
      } else if (outcome === "manual_review") {
        needsReview += 1;
      }
    } else if (scan.status === "failed" && scan.error?.retryable === false) {
      needsReview += 1;
    }
  }
  return { approvalReady, inProgress, needsReview, total: scans.length };
}

function overBudgetCount(scans: ScanAggregate[]): number {
  let count = 0;
  for (const scan of scans) {
    for (const row of scan.results) {
      if (row.budget_status === "exception_required") {
        count += 1;
      }
    }
  }
  return count;
}

function outcomeClass(scan: ScanAggregate): string {
  if (scan.status === "succeeded") {
    const outcome = representativeOutcome(scan);
    if (outcome === "approval_ready") {
      return "approval";
    }
    if (outcome === "manual_review") {
      return "review";
    }
  }
  return scan.status;
}

function recommendationIcon(outcome: string): "check" | "alert" {
  return outcome === "approval_ready" || outcome === "confirmed" ? "check" : "alert";
}

export function OverviewPage({
  onSelectScan,
  onSelectCase,
  view = "home",
}: OverviewPageProps) {
  const [scans, setScans] = useState<ScanAggregate[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const startController = useRef<AbortController | null>(null);
  const [recentCases, setRecentCases] = useState<CaseSummary[] | null>(null);
  const [recentCasesError, setRecentCasesError] = useState<string | null>(null);

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

  useEffect(() => {
    const controller = new AbortController();
    void listRecentCases({ limit: 5, signal: controller.signal })
      .then((cases) => {
        setRecentCases(cases);
        setRecentCasesError(null);
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setRecentCasesError(safeMessage(error));
        }
      });
    return () => controller.abort();
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
  const overBudget = scans === null ? 0 : overBudgetCount(scans);
  const scanContent = loadError ? (
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
            <span className={`scan-list-icon scan-list-icon--${outcomeClass(scan)}`}>
              <Icon
                name={
                  outcomeClass(scan) === "approval"
                    ? "check"
                    : outcomeClass(scan) === "failed" ||
                        outcomeClass(scan) === "review"
                      ? "alert"
                      : "document"
                }
              />
            </span>
            <span className="scan-list-copy">
              <strong>{scan.scan_id}</strong>
              <small>
                {scan.trigger === "manual" ? "Manual" : "Scheduled"} scan
                {scan.completed_at
                  ? ` · Completed ${formatDateTime(scan.completed_at)}`
                  : ` · Started ${formatDateTime(scan.created_at)}`}
              </small>
            </span>
            <span className={`status status--${outcomeClass(scan)}`}>
              {displayScanOutcome(scan)}
            </span>
            <span aria-hidden="true" className="scan-chevron">›</span>
          </button>
        </li>
      ))}
    </ul>
  );

  const recentCasesContent = recentCasesError ? (
    <p className="notice notice--error" role="alert">
      {recentCasesError}
    </p>
  ) : recentCases === null ? (
    <div className="loading-skeleton" role="status">
      <span className="visually-hidden">Loading recent recommendations…</span>
      <span />
      <span />
      <span />
    </div>
  ) : recentCases.length === 0 ? (
    <div className="empty-state">
      <h3>No recommendations yet</h3>
      <p>Run a manual scan to create the first recommendation.</p>
    </div>
  ) : (
    <ul className="scan-list" aria-label="Recent procurement recommendations">
      {recentCases.map((row) => (
        <li key={row.case_id}>
          <button
            className="scan-link"
            type="button"
            onClick={() => onSelectCase(row.scan_id, row.case_id)}
            aria-label={`Open ${row.product_name}, ${
              OUTCOME_LABEL[row.outcome] ?? row.outcome
            }`}
          >
            <span
              className={`scan-list-icon scan-list-icon--${
                recommendationIcon(row.outcome) === "check" ? "approval" : "review"
              }`}
            >
              <Icon name={recommendationIcon(row.outcome)} />
            </span>
            <span className="scan-list-copy">
              <strong>{row.product_name}</strong>
              <small>
                Scan #{row.scan_id} · Need by {formatDate(row.need_by_date)}
              </small>
            </span>
            <span className={`status status--${row.outcome}`}>
              {OUTCOME_LABEL[row.outcome] ?? row.outcome}
            </span>
            <span>{row.amount ? formatCurrency(row.amount, "USD") : "—"}</span>
          </button>
        </li>
      ))}
    </ul>
  );

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

      {view === "home" ? (
        <div className="home-dashboard-grid">
          <section
            aria-label="Recent recommendations"
            className="panel dashboard-panel"
          >
            <div className="panel-heading">
              <span className="summary-icon summary-icon--blue">
                <Icon name="recommendation" />
              </span>
              <h2>Recent recommendations</h2>
            </div>
            {recentCasesContent}
          </section>
          <section aria-label="Recent scan activity" className="panel dashboard-panel">
            <div className="panel-heading">
              <span className="summary-icon summary-icon--blue"><Icon name="scans" /></span>
              <h2 id="recent-scans-title">Recent scans</h2>
            </div>
            {scanContent}
          </section>
          {counts ? (
            <section aria-label="What needs attention" className="panel dashboard-panel attention-panel">
              <div className="panel-heading">
                <span className="summary-icon summary-icon--blue"><Icon name="alert" /></span>
                <h2>What needs attention</h2>
              </div>
              <div className="attention-list">
                <article className="attention-card attention-card--review">
                  <span className="summary-icon summary-icon--red"><Icon name="alert" /></span>
                  <strong>{counts.needsReview}</strong>
                  <span>Needs review</span>
                  <small>Require officer evaluation</small>
                </article>
                <article className="attention-card attention-card--ready">
                  <span className="summary-icon summary-icon--green"><Icon name="check" /></span>
                  <strong>{counts.approvalReady}</strong>
                  <span>Approval ready</span>
                  <small>Read-only recommendations</small>
                </article>
                <article className="attention-card attention-card--exception">
                  <span className="summary-icon summary-icon--amber">
                    <Icon name="alert" />
                  </span>
                  <strong>{overBudget}</strong>
                  <span>Over-budget exceptions</span>
                  <small>Exceed budget thresholds</small>
                </article>
              </div>
            </section>
          ) : null}
        </div>
      ) : (
        <section aria-labelledby="recent-scans-title" className="panel dashboard-panel scans-panel">
          <div className="panel-heading">
            <span className="summary-icon summary-icon--blue"><Icon name="scans" /></span>
            <h2 id="recent-scans-title">All scans</h2>
          </div>
          {scanContent}
        </section>
      )}
    </section>
  );
}
