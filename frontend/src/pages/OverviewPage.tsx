import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  createManualScan,
  isAbortError,
  listScans,
  type Scan,
} from "../api/client";

interface OverviewPageProps {
  onSelectScan: (scanId: string) => void;
}

function safeMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The request could not be completed.";
}

function displayStatus(status: Scan["status"]): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export function OverviewPage({ onSelectScan }: OverviewPageProps) {
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

  return (
    <section aria-labelledby="overview-title" className="page-stack">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Walking skeleton</p>
          <h1 id="overview-title">Procurement scans</h1>
          <p className="lede">
            Run and inspect a fictional, read-only replenishment recommendation.
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

      <section aria-labelledby="recent-scans-title" className="panel">
        <h2 id="recent-scans-title">Recent scans</h2>
        {loadError ? (
          <p className="notice notice--error" role="alert">
            {loadError}
          </p>
        ) : scans === null ? (
          <p role="status">Loading scans…</p>
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
                  aria-label={`Open ${scan.scan_id}, ${displayStatus(scan.status)}`}
                >
                  <span>
                    <strong>{scan.scan_id}</strong>
                    <small>
                      {scan.trigger === "manual" ? "Manual" : "Scheduled"} scan
                    </small>
                  </span>
                  <span className={`status status--${scan.status}`}>
                    {displayStatus(scan.status)}
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
