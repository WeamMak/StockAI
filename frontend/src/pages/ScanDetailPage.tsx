import { useEffect, useState } from "react";

import {
  ApiError,
  getScanAggregate,
  isAbortError,
  type ScanAggregate,
} from "../api/client";
import {
  formatCurrency,
  formatDate,
  formatDateTime,
  OUTCOME_COLOR,
  OUTCOME_LABEL,
} from "../presentation";

const DEFAULT_POLL_INTERVAL_MS = 1_000;
const DEFAULT_MAX_POLL_ATTEMPTS = 130;

function OutcomeDonut({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, count]) => count > 0);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  if (total === 0) {
    return null;
  }
  let cumulative = 0;
  const radius = 60;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className="outcome-donut">
      <svg
        role="img"
        aria-label={`Outcome breakdown: ${entries
          .map(([outcome, count]) => `${count} ${OUTCOME_LABEL[outcome] ?? outcome}`)
          .join(", ")}`}
        viewBox="0 0 140 140"
      >
        {entries.map(([outcome, count]) => {
          const fraction = count / total;
          const dashArray = `${fraction * circumference} ${circumference}`;
          const dashOffset = -cumulative * circumference;
          cumulative += fraction;
          return (
            <circle
              key={outcome}
              cx="70"
              cy="70"
              r={radius}
              fill="none"
              stroke={OUTCOME_COLOR[outcome] ?? "#94a3b8"}
              strokeWidth="20"
              strokeDasharray={dashArray}
              strokeDashoffset={dashOffset}
              transform="rotate(-90 70 70)"
            />
          );
        })}
      </svg>
      <ul className="outcome-donut__legend">
        {entries.map(([outcome, count]) => (
          <li key={outcome}>
            <span
              className="outcome-donut__swatch"
              style={{ background: OUTCOME_COLOR[outcome] ?? "#94a3b8" }}
            />
            {OUTCOME_LABEL[outcome] ?? outcome}
            <strong>{count}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ScanDetailPage({
  scanId,
  onBack,
  onSelectCase,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
}: {
  scanId: string;
  onBack: () => void;
  onSelectCase: (caseId: string) => void;
  pollIntervalMs?: number;
  maxPollAttempts?: number;
}) {
  const [aggregate, setAggregate] = useState<ScanAggregate | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let attempts = 0;
    let timer: number | undefined;
    let controller: AbortController | undefined;

    async function poll() {
      attempts += 1;
      controller = new AbortController();
      try {
        const nextAggregate = await getScanAggregate(scanId, {
          signal: controller.signal,
        });
        if (!active) {
          return;
        }
        setAggregate(nextAggregate);
        setError(null);
        if (
          nextAggregate.status === "queued" ||
          nextAggregate.status === "running"
        ) {
          if (attempts >= maxPollAttempts) {
            setError(
              "The scan is still running. Return to the overview and check again shortly.",
            );
            return;
          }
          timer = window.setTimeout(() => void poll(), pollIntervalMs);
        }
      } catch (requestError: unknown) {
        if (active && !isAbortError(requestError)) {
          setError(
            requestError instanceof ApiError
              ? requestError.message
              : "The request could not be completed.",
          );
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
  }, [scanId, pollIntervalMs, maxPollAttempts]);

  return (
    <section aria-labelledby="scan-detail-title" className="page-stack">
      <button className="back-button" type="button" onClick={onBack}>
        ← Back to scans
      </button>
      <p className="eyebrow">Scan detail</p>
      <h1 id="scan-detail-title">Manual scan results</h1>

      {error ? (
        <p className="notice notice--error" role="alert">
          {error}
        </p>
      ) : aggregate === null ? (
        <div className="panel loading-skeleton" role="status">
          <span className="visually-hidden">Loading scan…</span>
        </div>
      ) : (
        <>
          <section className="panel" aria-label="Run summary">
            <span className={`status status--${aggregate.status}`}>
              {aggregate.status}
            </span>
            <span className="identifier">{aggregate.scan_id}</span>
            {aggregate.completed_at ? (
              <span>Completed {formatDateTime(aggregate.completed_at)}</span>
            ) : null}
          </section>

          <div className="scan-detail-grid">
            <section className="panel" aria-label="Results from this scan">
              <h2>Results from this scan</h2>
              <ul className="scan-results-list">
                {aggregate.results.map((row) => (
                  <li key={row.case_id}>
                    <div>
                      <strong>{row.product_name}</strong>
                      <span>Need by {formatDate(row.need_by_date)}</span>
                    </div>
                    <span className={`status status--${row.outcome}`}>
                      {OUTCOME_LABEL[row.outcome] ?? row.outcome}
                    </span>
                    <span>
                      {row.amount ? formatCurrency(row.amount, "USD") : "—"}
                    </span>
                    <button type="button" onClick={() => onSelectCase(row.case_id)}>
                      View recommendation
                    </button>
                  </li>
                ))}
              </ul>
              {aggregate.results.length === 0 ? (
                <p>No products needed replenishment in this scan.</p>
              ) : null}
            </section>

            <section className="panel" aria-label="Outcome breakdown">
              <h2>Outcome breakdown</h2>
              <OutcomeDonut counts={aggregate.outcomeCounts} />
            </section>
          </div>
        </>
      )}
    </section>
  );
}
