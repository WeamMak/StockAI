import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { AuditTimeline } from "../src/components/AuditTimeline";

afterEach(() => vi.unstubAllGlobals());

it("renders server-ordered audit events and manager text as text", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          case_id: "scan-001:product-101",
          events: [
            {
              event_id: "001",
              event_type: "manager_rejected",
              actor_id: "manager-001",
              occurred_at: "2026-08-21T12:00:00Z",
              correlation_id: "request-001",
              source_revision: 3,
              outcome: "rejected",
              evidence_digest: `sha256:${"a".repeat(64)}`,
              decision_id: "decision-001",
              decision_type: "reject",
              justification: null,
              reason: "<strong>Do not render HTML</strong>",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );

  render(<AuditTimeline caseId="scan-001:product-101" />);

  expect(await screen.findByText("manager rejected")).toBeInTheDocument();
  expect(screen.getByText(/<strong>Do not render HTML<\/strong>/)).toBeInTheDocument();
  expect(document.querySelector(".audit-timeline strong strong")).toBeNull();
});
