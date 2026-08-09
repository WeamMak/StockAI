import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createManualScan,
  getScan,
  getSession,
  listScans,
} from "../src/api/client";

const QUEUED_SCAN = {
  scan_id: "scan-queued",
  status: "queued",
  trigger: "manual",
  created_at: "2026-08-05T10:00:00Z",
  started_at: null,
  completed_at: null,
  result: null,
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

describe("scan API client", () => {
  it("accepts only the documented 202 manual-scan response", async () => {
    document.cookie = "stockai_csrf=opaque-csrf-token; path=/";
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(QUEUED_SCAN, 202));
    vi.stubGlobal("fetch", fetchMock);

    await expect(createManualScan()).resolves.toEqual(QUEUED_SCAN);
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

  it("parses bounded scan-list and detail responses", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ scans: [QUEUED_SCAN] }))
      .mockResolvedValueOnce(jsonResponse(QUEUED_SCAN));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listScans()).resolves.toEqual([QUEUED_SCAN]);
    await expect(getScan("scan-queued")).resolves.toEqual(QUEUED_SCAN);
  });

  it("rejects malformed success payloads with a safe error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          scans: [{ ...QUEUED_SCAN, status: "unexpected", secret: "do-not-show" }],
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
