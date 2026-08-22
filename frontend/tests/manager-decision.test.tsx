import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { CaseDetail, Session } from "../src/api/client";
import { ManagerDecisionPanel } from "../src/components/ManagerDecisionPanel";

const MANAGER: Session = {
  user_id: "manager-001",
  email: "manager@example.invalid",
  role: "manager",
};
const OFFICER: Session = { ...MANAGER, role: "officer" };
const PENDING_CASE = {
  case_id: "scan-001:product-101",
  revision: 3,
  status: "pending_approval",
  draft: { po_id: 41, write_date: "2026-08-21 12:00:00" },
  result: {
    outcome: "approval_ready",
    validation_level: "t27",
    product_id: "product-101",
    offer_id: "offer-101",
    quantity: "25.000000",
    normalized_cost: "312.500000",
    evidence_digest: `sha256:${"a".repeat(64)}`,
  },
  evidence: [
    {
      product_id: "product-101",
      offers: [
        {
          offer_id: "offer-101",
          vendor_id: "vendor-101",
          vendor_name: "Fictional Supplies",
          currency: "USD",
        },
      ],
      budget: {
        exception_required: true,
        remaining_after: "-12.500000",
        overage: "12.500000",
        currency: "USD",
      },
    },
  ],
} as CaseDetail;

describe("ManagerDecisionPanel", () => {
  it("requires an explicit over-budget exception and justification", async () => {
    const user = userEvent.setup();
    render(<ManagerDecisionPanel session={MANAGER} caseDetail={PENDING_CASE} />);

    expect(screen.queryByLabelText("Justification")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    const approve = screen.getByRole("button", { name: "Confirm approval" });
    expect(approve).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", { name: /approve budget exception/i }),
    );
    await user.type(
      screen.getByLabelText("Justification"),
      "Avoid a projected stockout.",
    );
    expect(approve).toBeEnabled();
  });

  it("removes duplicated bindings and discloses rejection only on demand", async () => {
    const user = userEvent.setup();
    render(<ManagerDecisionPanel session={MANAGER} caseDetail={PENDING_CASE} />);

    expect(screen.queryByText("Evidence digest", { selector: "dt" })).toBeNull();
    expect(screen.queryByLabelText("Rejection reason")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.getByLabelText("Rejection reason")).toHaveFocus();
    expect(
      screen.getByRole("button", { name: "Confirm rejection" }),
    ).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Cancel rejection" }));
    expect(screen.queryByLabelText("Rejection reason")).toBeNull();
  });

  it("does not render decision controls for an officer", () => {
    render(<ManagerDecisionPanel session={OFFICER} caseDetail={PENDING_CASE} />);

    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reject/i })).not.toBeInTheDocument();
  });
});
