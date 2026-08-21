import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RefinementPanel } from "../src/components/RefinementPanel";
import type { CaseDetail } from "../src/api/client";

function jsonResponse(body: unknown, status = 202): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RefinementPanel", () => {
  it("submits a note and reports the running case back to the caller", async () => {
    const user = userEvent.setup();
    const runningCase: CaseDetail = {
      scan_id: "scan-101",
      case_id: "scan-101:product-101",
      revision: 2,
      status: "running",
      trigger: "manual",
      created_at: "2026-08-05T10:00:00Z",
      started_at: "2026-08-05T10:05:00Z",
      completed_at: null,
      evidence: [],
      result: null,
      error: null,
      refinement_count: 0,
      draft: null,
      decision: null,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(runningCase)));
    const onRefined = vi.fn();

    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={0}
        onRefined={onRefined}
      />,
    );

    await user.type(
      screen.getByLabelText("Refinement note"),
      "Prioritize delivery speed.",
    );
    await user.click(screen.getByRole("button", { name: "Submit refinement" }));

    await waitFor(() => {
      expect(onRefined).toHaveBeenCalledWith(runningCase);
    });
  });

  it("shows how many of the three refinements have been used", () => {
    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={2}
        onRefined={vi.fn()}
      />,
    );

    expect(screen.getByText("2 of 3 refinements used")).toBeInTheDocument();
  });

  it("disables the input once the cap is reached", () => {
    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={3}
        onRefined={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("Refinement note")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Refinement limit reached (3/3). Run a new scan for a fresh recommendation.",
      ),
    ).toBeInTheDocument();
  });

  it("shows a safe error message when the request fails", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { error_code: "VALIDATION_FAILED", message: "Note is invalid.", retryable: false },
          422,
        ),
      ),
    );

    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={0}
        onRefined={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("Refinement note"), "A note.");
    await user.click(screen.getByRole("button", { name: "Submit refinement" }));

    expect(await screen.findByText("Note is invalid.")).toBeInTheDocument();
  });

  it("disables submit until a note is entered", () => {
    render(
      <RefinementPanel
        scanId="scan-101"
        caseId="scan-101:product-101"
        refinementCount={0}
        onRefined={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Submit refinement" })).toBeDisabled();
  });
});
