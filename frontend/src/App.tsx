import { useState } from "react";

import { OverviewPage } from "./pages/OverviewPage";
import { ScanPage } from "./pages/ScanPage";

export function App() {
  const [selectedScanId, setSelectedScanId] = useState<string | null>(null);

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
          <span className="environment-label">Fictional data</span>
        </div>
      </header>
      <main className="app-shell" id="main-content">
        {selectedScanId === null ? (
          <OverviewPage onSelectScan={setSelectedScanId} />
        ) : (
          <ScanPage
            scanId={selectedScanId}
            onBack={() => setSelectedScanId(null)}
          />
        )}
      </main>
    </>
  );
}
