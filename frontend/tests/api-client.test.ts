import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createManualScan,
  getCase,
  getScanAggregate,
  getSession,
  listRecentCases,
  listScans,
  refineCase,
} from "../src/api/client";

const QUEUED_SCAN_PAYLOAD = {
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

const QUEUED_SCAN_AGGREGATE = {
  scan_id: "scan-queued",
  status: "queued",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: null,
  completed_at: null,
  results: [],
  outcomeCounts: {},
  error: null,
};

const CASE_DETAIL_PAYLOAD = {
  scan_id: "scan-queued",
  case_id: "scan-queued:product-101",
  status: "queued",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: null,
  completed_at: null,
  evidence: [],
  result: null,
  error: null,
  refinement_count: 0,
};

const CASE_SUMMARY_PAYLOAD = {
  case_id: "scan-recent:product-1",
  product_id: "product-1",
  product_name: "Fictional Widget",
  outcome: "approval_ready",
  amount: "120.500000",
  need_by_date: "2026-08-20",
  scan_id: "scan-recent",
  budget_status: "within_budget",
  completed_at: "2026-08-18T10:00:40Z",
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

describe("scan API client", () => {
  it("accepts only the documented 202 manual-scan response", async () => {
    document.cookie = "stockai_csrf=opaque-csrf-token; path=/";
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(QUEUED_SCAN_PAYLOAD, 202));
    vi.stubGlobal("fetch", fetchMock);

    await expect(createManualScan()).resolves.toEqual(QUEUED_SCAN_AGGREGATE);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/scans",
      expect.objectContaining({
        credentials: "same-origin",
        headers: expect.objectContaining({
          "X-CSRF-Token": "opaque-csrf-token",
        }),
        method: "POST",
      }),
    );
  });

  it("parses only the bounded current-session view", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          user_id: "cognito-user-001",
          email: "officer@example.invalid",
          role: "officer",
        }),
      ),
    );

    await expect(getSession()).resolves.toEqual({
      user_id: "cognito-user-001",
      email: "officer@example.invalid",
      role: "officer",
    });
  });

  it("parses bounded scan-list, scan-aggregate, and case-detail responses", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ scans: [QUEUED_SCAN_PAYLOAD] }))
      .mockResolvedValueOnce(jsonResponse(QUEUED_SCAN_PAYLOAD))
      .mockResolvedValueOnce(jsonResponse(CASE_DETAIL_PAYLOAD));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listScans()).resolves.toEqual([QUEUED_SCAN_AGGREGATE]);
    await expect(getScanAggregate("scan-queued")).resolves.toEqual(
      QUEUED_SCAN_AGGREGATE,
    );
    await expect(
      getCase("scan-queued", "scan-queued:product-101"),
    ).resolves.toEqual(CASE_DETAIL_PAYLOAD);
  });

  it("parses recent cases spanning multiple scans", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ cases: [CASE_SUMMARY_PAYLOAD] })),
    );

    await expect(listRecentCases()).resolves.toEqual([
      {
        case_id: "scan-recent:product-1",
        product_id: "product-1",
        product_name: "Fictional Widget",
        outcome: "approval_ready",
        amount: "120.500000",
        need_by_date: "2026-08-20",
        scan_id: "scan-recent",
        budget_status: "within_budget",
        completed_at: "2026-08-18T10:00:40Z",
      },
    ]);
  });

  it("parses a case result with a no_valid_offer outcome", async () => {
    const noValidOffer = {
      ...CASE_DETAIL_PAYLOAD,
      status: "succeeded",
      completed_at: "2026-08-05T10:00:05Z",
      result: {
        outcome: "no_valid_offer",
        product_id: "product-101",
        product_name: "Fictional No-Valid-Offer Component",
        rationale: "No approved vendor offer is eligible for this product.",
        evidence_limitations: [],
        read_only: true,
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(noValidOffer)));

    await expect(
      getCase("scan-queued", "scan-queued:product-101"),
    ).resolves.toEqual(noValidOffer);
  });

  it("parses a historical approval without inventing T27 fields", async () => {
    const legacy = {
      ...CASE_DETAIL_PAYLOAD,
      status: "succeeded",
      completed_at: "2026-08-05T10:00:05Z",
      result: {
        outcome: "approval_ready",
        validation_level: "legacy",
        product_id: "product-legacy",
        product_name: "Legacy Product",
        offer_id: null,
        rationale: "One eligible candidate was available.",
        trade_offs: ["Authoritative evidence remains available."],
        risk_flags: ["LEGACY_RECOMMENDATION"],
        uncertainty: "This recommendation predates T27 validation.",
        evidence_limitations: ["Offer-level metadata is unavailable."],
        read_only: true,
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(legacy)));

    await expect(
      getCase("scan-queued", "scan-queued:product-legacy"),
    ).resolves.toEqual(legacy);
  });

  it("posts a bounded note and parses the resulting running case", async () => {
    document.cookie = "stockai_csrf=opaque-csrf-token; path=/";
    const runningCase = {
      ...CASE_DETAIL_PAYLOAD,
      status: "running",
      started_at: "2026-08-05T10:05:00Z",
      refinement_count: 0,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(runningCase, 202));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      refineCase("scan-queued", "scan-queued:product-101", "Prioritize delivery."),
    ).resolves.toEqual(runningCase);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/scans/scan-queued/cases/scan-queued%3Aproduct-101/refine",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ note: "Prioritize delivery." }),
        headers: expect.objectContaining({
          "X-CSRF-Token": "opaque-csrf-token",
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("rejects a refine response missing refinement_count", async () => {
    const malformed: Record<string, unknown> = { ...CASE_DETAIL_PAYLOAD };
    delete malformed.refinement_count;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(malformed, 202)),
    );

    const error = await refineCase(
      "scan-queued",
      "scan-queued:product-101",
      "Prioritize delivery.",
    ).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("rejects malformed success payloads with a safe error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          scans: [
            { ...QUEUED_SCAN_PAYLOAD, status: "unexpected", secret: "do-not-show" },
          ],
        }),
      ),
    );

    const error = await listScans().catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      code: "INVALID_RESPONSE",
      message: "The server returned an invalid response.",
      retryable: true,
    });
    expect(String(error)).not.toContain("do-not-show");
  });

  it("uses only the safe public error fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error_code: "SCAN_ALREADY_RUNNING",
            message: "A procurement scan is already running.",
            retryable: false,
            internal_detail: "database-password",
          },
          409,
        ),
      ),
    );

    const error = await createManualScan().catch((reason: unknown) => reason);

    expect(error).toMatchObject({
      code: "SCAN_ALREADY_RUNNING",
      message: "A procurement scan is already running.",
      retryable: false,
    });
    expect(String(error)).not.toContain("database-password");
  });
});
