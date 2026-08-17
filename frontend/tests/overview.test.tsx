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
  results: [],
  outcome_counts: {},
  error: null,
};

const SUCCEEDED_SCAN = {
  ...QUEUED_SCAN,
  scan_id: "scan-succeeded",
  status: "succeeded",
  completed_at: "2026-08-05T10:00:05Z",
  results: [
    {
      case_id: "scan-succeeded:product-101",
      product_id: "product-101",
      product_name: "Fictional Safety Gloves",
      outcome: "approval_ready",
      amount: "437.500000",
      need_by_date: "2026-08-12",
    },
  ],
  outcome_counts: { approval_ready: 1 },
};

const MANUAL_REVIEW_SCAN = {
  ...QUEUED_SCAN,
  scan_id: "scan-manual-review",
  status: "succeeded",
  completed_at: "2026-08-05T10:00:07Z",
  results: [
    {
      case_id: "scan-manual-review:product-102",
      product_id: "product-102",
      product_name: "Fictional Cable Ties",
      outcome: "manual_review",
      amount: null,
      need_by_date: null,
    },
  ],
  outcome_counts: { manual_review: 1 },
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
            MANUAL_REVIEW_SCAN,
            FAILED_SCAN,
          ],
        }),
      ),
    );

    render(<OverviewPage onSelectScan={vi.fn()} />);

    const summary = await screen.findByRole("region", { name: "Scan summary" });
    expect(summary).toHaveTextContent("5Total");
    expect(summary).toHaveTextContent("2In progress");
    expect(summary).toHaveTextContent("1Approval ready");
    expect(summary).toHaveTextContent("2Needs review");
    expect(screen.getAllByText(/Completed Aug 5, 2026/)).toHaveLength(3);
    expect(screen.getByText("Manual review")).toBeInTheDocument();
    const attention = screen.getByRole("region", { name: "What needs attention" });
    expect(attention).toHaveTextContent("2Needs review");
    expect(attention).toHaveTextContent("1Approval ready");
    expect(
      screen.getByRole("region", { name: "Recent scan activity" }),
    ).toBeInTheDocument();
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
