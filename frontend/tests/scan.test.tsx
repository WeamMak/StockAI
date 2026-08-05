import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScanPage } from "../src/pages/ScanPage";

const BASE_SCAN = {
  scan_id: "scan-101",
  status: "succeeded",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: "2026-08-05T10:00:01Z",
  completed_at: "2026-08-05T10:00:02Z",
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
    let resolveRequest: ((response: Response) => void) | undefined;
    const request = new Promise<Response>((resolve) => {
      resolveRequest = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(request));

    render(<ScanPage scanId="scan-101" onBack={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading scan");
    resolveRequest?.(jsonResponse(BASE_SCAN));

    expect(
      await screen.findByRole("heading", { name: "Fictional Safety Gloves" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Read-only recommendation")).toBeInTheDocument();
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
      await screen.findByRole("heading", { name: "Fictional Safety Gloves" }),
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
