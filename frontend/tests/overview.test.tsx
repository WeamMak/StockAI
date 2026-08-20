import { render, screen, waitFor, within } from "@testing-library/react";
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
      scan_id: "scan-succeeded",
      budget_status: "within_budget",
      completed_at: "2026-08-05T10:00:05Z",
      status: "succeeded",
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
      scan_id: "scan-manual-review",
      budget_status: "not_evaluated",
      completed_at: "2026-08-05T10:00:07Z",
      status: "succeeded",
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

const OVER_BUDGET_SCAN = {
  ...QUEUED_SCAN,
  scan_id: "scan-over-budget",
  status: "succeeded",
  completed_at: "2026-08-05T10:00:08Z",
  results: [
    {
      case_id: "scan-over-budget:product-103",
      product_id: "product-103",
      product_name: "Fictional Industrial Fasteners",
      outcome: "approval_ready",
      amount: "980.000000",
      need_by_date: "2026-08-14",
      scan_id: "scan-over-budget",
      budget_status: "exception_required",
      completed_at: "2026-08-05T10:00:08Z",
      status: "succeeded",
    },
  ],
  outcome_counts: { approval_ready: 1 },
};

const RECENT_CASES = [
  {
    case_id: "scan-succeeded:product-101",
    product_id: "product-101",
    product_name: "Fictional Safety Gloves",
    outcome: "approval_ready",
    amount: "437.500000",
    need_by_date: "2026-08-12",
    scan_id: "scan-succeeded",
    budget_status: "within_budget",
    completed_at: "2026-08-05T10:00:05Z",
    status: "succeeded",
  },
  {
    case_id: "scan-manual-review:product-102",
    product_id: "product-102",
    product_name: "Fictional Cable Ties",
    outcome: "manual_review",
    amount: null,
    need_by_date: null,
    scan_id: "scan-manual-review",
    budget_status: "not_evaluated",
    completed_at: "2026-08-05T10:00:07Z",
    status: "succeeded",
  },
];

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
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        url.startsWith("/api/v1/cases")
          ? Promise.resolve(jsonResponse({ cases: [] }))
          : request,
      ),
    );

    render(<OverviewPage onSelectScan={vi.fn()} onSelectCase={vi.fn()} />);

    const recentScans = screen.getByRole("region", { name: "Recent scan activity" });
    expect(within(recentScans).getByRole("status")).toHaveTextContent(
      "Loading scans",
    );
    resolveRequest?.(jsonResponse({ scans: [] }));

    expect(await screen.findByText("No scans yet")).toBeInTheDocument();
  });

  it("lists existing scans and opens the selected scan", async () => {
    const onSelectScan = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(
          url.startsWith("/api/v1/cases")
            ? jsonResponse({ cases: [] })
            : jsonResponse({ scans: [QUEUED_SCAN] }),
        ),
      ),
    );

    render(<OverviewPage onSelectScan={onSelectScan} onSelectCase={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: /scan-queued/i }));

    expect(onSelectScan).toHaveBeenCalledWith("scan-queued");
  });

  it("summarizes loaded scan outcomes without inventing data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(
          url.startsWith("/api/v1/cases")
            ? jsonResponse({ cases: [] })
            : jsonResponse({
                scans: [
                  QUEUED_SCAN,
                  { ...QUEUED_SCAN, scan_id: "scan-running", status: "running" },
                  SUCCEEDED_SCAN,
                  MANUAL_REVIEW_SCAN,
                  FAILED_SCAN,
                  OVER_BUDGET_SCAN,
                ],
              }),
        ),
      ),
    );

    render(<OverviewPage onSelectScan={vi.fn()} onSelectCase={vi.fn()} />);

    const summary = await screen.findByRole("region", { name: "Scan summary" });
    expect(summary).toHaveTextContent("6Total");
    expect(summary).toHaveTextContent("2In progress");
    expect(summary).toHaveTextContent("2Approval ready");
    expect(summary).toHaveTextContent("2Needs review");
    expect(screen.getAllByText(/Completed Aug 5, 2026/)).toHaveLength(4);
    expect(screen.getByText("Manual review")).toBeInTheDocument();
    const attention = screen.getByRole("region", { name: "What needs attention" });
    expect(attention).toHaveTextContent("2Needs review");
    expect(attention).toHaveTextContent("2Approval ready");
    expect(attention).toHaveTextContent("1Over-budget exceptions");
    expect(attention).not.toHaveTextContent("In progress");
    expect(
      screen.getByRole("region", { name: "Recent scan activity" }),
    ).toBeInTheDocument();
  });

  it("lists recent recommendations with a link to their case", async () => {
    const onSelectCase = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.startsWith("/api/v1/cases")) {
          return Promise.resolve(jsonResponse({ cases: RECENT_CASES }));
        }
        return Promise.resolve(jsonResponse({ scans: [] }));
      }),
    );

    render(<OverviewPage onSelectScan={vi.fn()} onSelectCase={onSelectCase} />);

    const panel = await screen.findByRole("region", {
      name: "Recent recommendations",
    });
    expect(within(panel).getByText("Fictional Safety Gloves")).toBeInTheDocument();
    expect(within(panel).getByText("Fictional Cable Ties")).toBeInTheDocument();
    expect(within(panel).getByText("Manual review")).toBeInTheDocument();

    await userEvent.click(within(panel).getByText("Fictional Safety Gloves"));
    expect(onSelectCase).toHaveBeenCalledWith(
      "scan-succeeded",
      "scan-succeeded:product-101",
    );
  });

  it("shows an empty state when there are no recent recommendations", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.startsWith("/api/v1/cases")) {
          return Promise.resolve(jsonResponse({ cases: [] }));
        }
        return Promise.resolve(jsonResponse({ scans: [] }));
      }),
    );

    render(<OverviewPage onSelectScan={vi.fn()} onSelectCase={vi.fn()} />);

    const panel = await screen.findByRole("region", {
      name: "Recent recommendations",
    });
    expect(within(panel).getByText(/no recommendations yet/i)).toBeInTheDocument();
  });

  it("starts a manual scan from a 202 response", async () => {
    const onSelectScan = vi.fn();
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockImplementation((url: string, options?: RequestInit) => {
        if (url.startsWith("/api/v1/cases")) {
          return Promise.resolve(jsonResponse({ cases: [] }));
        }
        if (options?.method === "POST") {
          return Promise.resolve(jsonResponse(QUEUED_SCAN, 202));
        }
        return Promise.resolve(jsonResponse({ scans: [] }));
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<OverviewPage onSelectScan={onSelectScan} onSelectCase={vi.fn()} />);
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
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve(
          url.startsWith("/api/v1/cases")
            ? jsonResponse({ cases: [] })
            : jsonResponse(
                {
                  error_code: "ODOO_UNAVAILABLE",
                  message: "Scans are temporarily unavailable.",
                  retryable: true,
                  unsafe_detail: "secret-token",
                },
                503,
              ),
        ),
      ),
    );

    render(<OverviewPage onSelectScan={vi.fn()} onSelectCase={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Scans are temporarily unavailable.",
    );
    expect(screen.queryByText(/secret-token/i)).not.toBeInTheDocument();
  });
});
