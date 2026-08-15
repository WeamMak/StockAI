import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScanPage } from "../src/pages/ScanPage";

const BASE_SCAN = {
  scan_id: "scan-101",
  status: "succeeded",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: "2026-08-05T10:00:01Z",
  completed_at: "2026-08-05T10:00:02Z",
  evidence: [
    {
      environment: "dev",
      evidence_id: "dev:evidence-product-101",
      product_id: "product-101",
      product_name: "Fictional Safety Gloves",
      category_id: "category-safety",
      captured_at: "2026-08-05T10:00:01Z",
      shortage: {
        horizon_start: "2026-08-05",
        horizon_end: "2026-08-19",
        reorder_trigger_date: "2026-08-08",
        need_by_date: "2026-08-12",
        reorder_minimum: "10.000000",
        reorder_maximum: "40.000000",
        minimum_projected_quantity: "0.000000",
        timeline: Array.from({ length: 15 }, (_, offset) => ({
          projection_date: `2026-08-${String(5 + offset).padStart(2, "0")}`,
          quantity: offset < 7 ? "8.000000" : "0.000000",
        })),
      },
      coverage: {
        status: "partial",
        covered_quantity: "5.000000",
        residual_quantity: "35.000000",
        source_count: 1,
      },
      offers: [
        {
          offer_id: "offer-101",
          vendor_id: "vendor-101",
          vendor_name: "Fictional Approved Supplies",
          status: "eligible",
          reason_codes: [],
          currency: "USD",
          unit_price: "12.500000",
          company_currency: "USD",
          normalized_unit_price: "12.500000",
          delivery_date: "2026-08-10",
          quantity: "35.000000",
          normalized_cost: "437.500000",
          projected_inventory_after_receipt: "40.000000",
          excess_inventory: "0.000000",
          performance: {
            completed_order_count: 2,
            on_time_rate: "0.500000",
            history_status: "limited",
          },
        },
      ],
      budget: {
        period_start: "2026-08-01",
        currency: "USD",
        budget_amount: "5000.000000",
        confirmed_commitment: "160.000000",
        proposed_amount: "437.500000",
        remaining_before: "4840.000000",
        remaining_after: "4402.500000",
        overage: "0.000000",
        exception_required: false,
      },
      skip_reason_code: null,
      preferences: {
        profile_id: "preference-3",
        company_id: "1",
        category_id: "category-safety",
        product_id: "product-101",
        scope: "product",
        scope_id: "product-101",
        revision: 6,
        ordered_criteria: ["price", "reliability", "delivery"],
        max_price_premium_percent: "10.000000",
        enforcement_mode: "advisory",
        precedence_source: "product",
        cheapest_eligible_cost: "437.500000",
        offer_results: [
          {
            offer_id: "offer-101",
            premium_percent: "0.000000",
            exceeds_cap: false,
            outcome: "within_cap",
          },
        ],
      },
    },
  ],
  result: {
    outcome: "approval_ready",
    product_id: "product-101",
    product_name: "Fictional Safety Gloves",
    rationale: "Projected stock is below the reorder minimum.",
    risk_flags: ["LIMITED_EVIDENCE"],
    read_only: true,
  },
  error: null,
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

describe("ScanPage", () => {
  it("shows loading before rendering an approval-ready result", async () => {
    const user = userEvent.setup();
    let resolveRequest: ((response: Response) => void) | undefined;
    const request = new Promise<Response>((resolve) => {
      resolveRequest = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(request));

    render(<ScanPage scanId="scan-101" onBack={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading scan");
    resolveRequest?.(jsonResponse(BASE_SCAN));

    expect(
      await screen.findByRole("heading", {
        name: "Fictional Safety Gloves",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Read-only recommendation")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Deterministic procurement evidence" }),
    ).toBeInTheDocument();

    await user.click(screen.getByText("Vendor offers", { selector: "summary > span" }));
    expect(screen.getByText(/50%/)).toBeInTheDocument();

    await user.click(
      screen.getByText("Inventory projection", { selector: "summary > span" }),
    );
    expect(screen.getByText("14-day inventory projection")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();

    await user.click(
      screen.getByText("Applied preferences", { selector: "summary > span" }),
    );
    expect(
      screen.getByRole("heading", { name: "Applied preferences" }),
    ).toBeInTheDocument();
    expect(screen.getByText("price → reliability → delivery")).toBeInTheDocument();
    expect(screen.getByText("10% (advisory)")).toBeInTheDocument();
  });

  it("shows a decision summary before expandable supporting evidence", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(BASE_SCAN)));

    render(<ScanPage scanId="scan-101" onBack={vi.fn()} />);

    const summary = await screen.findByRole("region", {
      name: "Recommendation summary",
    });
    expect(summary).toHaveTextContent("Approval ready");
    expect(summary).toHaveTextContent("Aug 12, 2026");
    expect(summary).toHaveTextContent("35 units");
    expect(summary).toHaveTextContent("1 eligible offer");
    expect(summary).toHaveTextContent("$437.50");
    expect(summary).toHaveTextContent("Within budget");

    const inventory = screen
      .getByText("Inventory projection")
      .closest("details");
    expect(inventory).not.toBeNull();
    expect(inventory).not.toHaveAttribute("open");

    await user.click(screen.getByText("Inventory projection"));
    expect(inventory).toHaveAttribute("open");
    expect(screen.getByRole("table")).toHaveAccessibleName(
      "14-day inventory projection",
    );
  });

  it("shows a non-retryable unresolved result as manual review", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          status: "failed",
          result: null,
          error: {
            error_code: "NO_VALID_OFFER",
            message: "No approval-ready replenishment candidate was found.",
            retryable: false,
            retry_count: 0,
          },
        }),
      ),
    );

    render(<ScanPage scanId="scan-101" onBack={vi.fn()} />);

    expect(
      await screen.findByRole("heading", { name: "Manual review required" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/no approval-ready/i)).toBeInTheDocument();
  });

  it("shows retryable failures as safe errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          ...BASE_SCAN,
          status: "failed",
          result: null,
          error: {
            error_code: "MCP_TIMEOUT",
            message: "The procurement source timed out.",
            retryable: true,
            retry_count: 1,
          },
        }),
      ),
    );

    render(<ScanPage scanId="scan-101" onBack={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The procurement source timed out.",
    );
    expect(screen.getByText("MCP_TIMEOUT")).toBeInTheDocument();
  });

  it("polls running scans until they finish", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          ...BASE_SCAN,
          status: "running",
          completed_at: null,
          result: null,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(BASE_SCAN));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScanPage
        scanId="scan-101"
        onBack={vi.fn()}
        pollIntervalMs={20}
        maxPollAttempts={2}
      />,
    );

    expect(await screen.findByText("Scan in progress")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", {
        name: "Fictional Safety Gloves",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops after the configured polling limit", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        jsonResponse({
          ...BASE_SCAN,
          status: "running",
          completed_at: null,
          result: null,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScanPage
        scanId="scan-101"
        onBack={vi.fn()}
        pollIntervalMs={1}
        maxPollAttempts={2}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The scan is still running.",
    );
    expect(screen.getByText("POLL_LIMIT_REACHED")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops scheduled polling when the page unmounts", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        ...BASE_SCAN,
        status: "running",
        completed_at: null,
        result: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const view = render(
      <ScanPage
        scanId="scan-101"
        onBack={vi.fn()}
        pollIntervalMs={20}
        maxPollAttempts={5}
      />,
    );
    await screen.findByText("Scan in progress");
    view.unmount();

    await new Promise((resolve) => window.setTimeout(resolve, 30));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });
});
