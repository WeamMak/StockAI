import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OverviewPage } from "../src/pages/OverviewPage";

const QUEUED_SCAN = {
  scan_id: "scan-queued",
  status: "queued",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: null,
  completed_at: null,
  evidence: [],
  result: null,
  error: null,
};

const SUCCEEDED_SCAN = {
  ...QUEUED_SCAN,
  scan_id: "scan-succeeded",
  status: "succeeded",
  completed_at: "2026-08-05T10:00:05Z",
  result: {
    outcome: "approval_ready",
    product_id: "product-101",
    product_name: "Fictional Safety Gloves",
    rationale: "Replenishment is required.",
    risk_flags: [],
    read_only: true,
  },
};

const FAILED_SCAN = {
  ...QUEUED_SCAN,
  scan_id: "scan-review",
  status: "failed",
  completed_at: "2026-08-05T10:00:06Z",
  error: {
    error_code: "NO_VALID_OFFER",
    message: "Manual review is required.",
    retryable: false,
    retry_count: 0,
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OverviewPage", () => {
  it("shows the loading state and then the empty state", async () => {
    let resolveRequest: ((response: Response) => void) | undefined;
    const request = new Promise<Response>((resolve) => {
      resolveRequest = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(request));

    render(<OverviewPage onSelectScan={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading scans");
    resolveRequest?.(jsonResponse({ scans: [] }));

    expect(await screen.findByText("No scans yet")).toBeInTheDocument();
  });

  it("lists existing scans and opens the selected scan", async () => {
    const onSelectScan = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ scans: [QUEUED_SCAN] })),
    );

    render(<OverviewPage onSelectScan={onSelectScan} />);

    await userEvent.click(await screen.findByRole("button", { name: /scan-queued/i }));

    expect(onSelectScan).toHaveBeenCalledWith("scan-queued");
  });

  it("summarizes loaded scan outcomes without inventing data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          scans: [
            QUEUED_SCAN,
            { ...QUEUED_SCAN, scan_id: "scan-running", status: "running" },
            SUCCEEDED_SCAN,
            FAILED_SCAN,
          ],
        }),
      ),
    );

    render(<OverviewPage onSelectScan={vi.fn()} />);

    const summary = await screen.findByRole("region", { name: "Scan summary" });
    expect(summary).toHaveTextContent("4Total");
    expect(summary).toHaveTextContent("2In progress");
    expect(summary).toHaveTextContent("1Approval ready");
    expect(summary).toHaveTextContent("1Needs review");
    expect(screen.getAllByText(/Completed Aug 5, 2026/)).toHaveLength(2);
  });

  it("starts a manual scan from a 202 response", async () => {
    const onSelectScan = vi.fn();
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ scans: [] }))
      .mockResolvedValueOnce(jsonResponse(QUEUED_SCAN, 202));
    vi.stubGlobal("fetch", fetchMock);

    render(<OverviewPage onSelectScan={onSelectScan} />);
    await screen.findByText("No scans yet");
    const manualScanButton = screen.getByRole("button", {
      name: "Run manual scan",
    });
    await user.tab();
    expect(manualScanButton).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(onSelectScan).toHaveBeenCalledWith("scan-queued"));
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/scans",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows a safe list error without exposing an unsafe response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error_code: "ODOO_UNAVAILABLE",
            message: "Scans are temporarily unavailable.",
            retryable: true,
            unsafe_detail: "secret-token",
          },
          503,
        ),
      ),
    );

    render(<OverviewPage onSelectScan={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Scans are temporarily unavailable.",
    );
    expect(screen.queryByText(/secret-token/i)).not.toBeInTheDocument();
  });
});
