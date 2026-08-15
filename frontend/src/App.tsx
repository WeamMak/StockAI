import { useEffect, useState } from "react";

import { ApiError, getSession, isAbortError, type Session } from "./api/client";
import { AppNavigation } from "./components/AppNavigation";
import { OverviewPage } from "./pages/OverviewPage";
import { ScanPage } from "./pages/ScanPage";
import { SignInPage } from "./pages/SignInPage";

export function App() {
  const [selectedScanId, setSelectedScanId] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [sessionError, setSessionError] = useState<string | undefined>();

  useEffect(() => {
    const controller = new AbortController();
    void getSession({ signal: controller.signal })
      .then((currentSession) => {
        setSession(currentSession);
        setSessionError(undefined);
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) {
          return;
        }
        setSession(null);
        if (!(error instanceof ApiError) || error.code !== "AUTH_REQUIRED") {
          setSessionError("The session service is temporarily unavailable.");
        }
      });
    return () => controller.abort();
  }, []);

  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="site-header">
        <div className="site-header__inner">
          <a
            className="brand"
            href="/"
            onClick={(event) => {
              event.preventDefault();
              setSelectedScanId(null);
            }}
          >
            StockAI <span>Procurement</span>
          </a>
          {session === undefined || session === null ? (
            <span className="environment-label">Fictional data</span>
          ) : (
            <span className="environment-label">{session.email}</span>
          )}
        </div>
      </header>
      <div className={session ? "app-layout" : undefined}>
        {session ? (
          <AppNavigation
            active={selectedScanId === null ? "home" : "scans"}
            onHome={() => setSelectedScanId(null)}
            onScans={() => setSelectedScanId(null)}
          />
        ) : null}
        <main className="app-shell" id="main-content">
          {session === undefined ? (
            <p role="status">Loading session…</p>
          ) : session === null ? (
            <SignInPage message={sessionError} />
          ) : selectedScanId === null ? (
            <OverviewPage onSelectScan={setSelectedScanId} />
          ) : (
            <ScanPage
              scanId={selectedScanId}
              onBack={() => setSelectedScanId(null)}
            />
          )}
        </main>
      </div>
    </>
  );
}
