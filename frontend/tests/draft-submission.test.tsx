import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CaseDetail } from "../src/api/client";
import { DraftSubmissionPanel } from "../src/components/DraftSubmissionPanel";

const CASE: CaseDetail = {
  scan_id: "scan-101",
  case_id: "scan-101:product-101",
  revision: 3,
  status: "succeeded",
  trigger: "manual",
  created_at: "2026-08-21T10:00:00Z",
  started_at: "2026-08-21T10:00:01Z",
  completed_at: "2026-08-21T10:00:02Z",
  evidence: [],
  result: null,
  error: null,
  refinement_count: 0,
  draft: null,
  decision: null,
};

function jsonResponse(body: unknown, status = 202): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("DraftSubmissionPanel", () => {
  it("submits once with a stable session key and explains the lock", async () => {
    const user = userEvent.setup();
    document.cookie = "stockai_csrf=csrf-token; path=/";
    vi.stubGlobal("crypto", { randomUUID: () => "fixed-uuid" });
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        case_id: CASE.case_id,
        status: "creating_draft",
        created: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSubmitted = vi.fn();

    render(
      <DraftSubmissionPanel
        caseDetail={CASE}
        onSubmitted={onSubmitted}
      />,
    );

    expect(screen.getByText(/locks this recommendation/i)).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Create draft and send to manager" }),
    );

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/scans/scan-101/cases/scan-101%3Aproduct-101/draft",
      expect.objectContaining({
        headers: expect.objectContaining({
          "Idempotency-Key": "draft-fixed-uuid",
        }),
      }),
    );
    expect(
      sessionStorage.getItem(`stockai:draft:${CASE.case_id}:3`),
    ).toBe("draft-fixed-uuid");
  });

  it("disables the action while submission is pending", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("crypto", { randomUUID: () => "pending-uuid" });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
    render(
      <DraftSubmissionPanel caseDetail={CASE} onSubmitted={vi.fn()} />,
    );

    const button = screen.getByRole("button", {
      name: "Create draft and send to manager",
    });
    await user.click(button);

    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Creating draft");
  });
});
