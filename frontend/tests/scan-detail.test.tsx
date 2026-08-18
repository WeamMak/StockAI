import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScanDetailPage } from "../src/pages/ScanDetailPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const AGGREGATE = {
  scan_id: "scan-4278",
  status: "succeeded",
  trigger: "manual",
  created_at: "2026-08-18T14:40:00Z",
  started_at: "2026-08-18T14:40:01Z",
  completed_at: "2026-08-18T14:41:00Z",
  results: [
    {
      case_id: "scan-4278:product-1",
      product_id: "product-1",
      product_name: "PROD Fictional Happy-Path Component",
      outcome: "approval_ready",
      amount: "1080.000000",
      need_by_date: "2026-08-18",
    },
    {
      case_id: "scan-4278:product-2",
      product_id: "product-2",
      product_name: "PROD Fictional No-Valid-Offer Component",
      outcome: "no_valid_offer",
      amount: null,
      need_by_date: "2026-08-18",
    },
  ],
  outcome_counts: { approval_ready: 1, no_valid_offer: 1 },
  error: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ScanDetailPage", () => {
  it("shows a results row and outcome label per case", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(AGGREGATE)));

    render(
      <ScanDetailPage scanId="scan-4278" onBack={vi.fn()} onSelectCase={vi.fn()} />,
    );

    expect(
      await screen.findByText("PROD Fictional Happy-Path Component"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("PROD Fictional No-Valid-Offer Component"),
    ).toBeInTheDocument();
    const results = screen.getByRole("region", { name: "Results from this scan" });
    expect(within(results).getByText("No valid offer")).toBeInTheDocument();
    const donut = screen.getByRole("img", { name: /outcome breakdown/i });
    expect(donut).toBeInTheDocument();
  });

  it("calls onSelectCase with the row's case id when clicked", async () => {
    const user = userEvent.setup();
    const onSelectCase = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(AGGREGATE)));

    render(
      <ScanDetailPage
        scanId="scan-4278"
        onBack={vi.fn()}
        onSelectCase={onSelectCase}
      />,
    );

    const buttons = await screen.findAllByRole("button", {
      name: "View recommendation",
    });
    await user.click(buttons[0]);
    expect(onSelectCase).toHaveBeenCalledWith("scan-4278:product-1");
  });

  it("shows an empty state when no product needed replenishment", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ ...AGGREGATE, results: [], outcome_counts: {} }),
      ),
    );

    render(
      <ScanDetailPage scanId="scan-4278" onBack={vi.fn()} onSelectCase={vi.fn()} />,
    );

    expect(
      await screen.findByText("No products needed replenishment in this scan."),
    ).toBeInTheDocument();
  });

  it("polls a running scan until it reaches a terminal state", async () => {
    const running = { ...AGGREGATE, status: "running", completed_at: null };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(running))
      .mockResolvedValueOnce(jsonResponse(running))
      .mockResolvedValueOnce(jsonResponse(AGGREGATE));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScanDetailPage
        scanId="scan-4278"
        onBack={vi.fn()}
        onSelectCase={vi.fn()}
        pollIntervalMs={1}
      />,
    );

    await waitFor(() =>
      expect(
        screen.getByText("PROD Fictional Happy-Path Component"),
      ).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText(/succeeded/i)).toBeInTheDocument(),
    );
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});
