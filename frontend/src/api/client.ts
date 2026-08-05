const SCANS_PATH = "/api/v1/scans";
const MAX_SCAN_LIST_LENGTH = 100;

export type ScanStatus = "queued" | "running" | "succeeded" | "failed";
export type ScanTrigger = "manual" | "cron";

export interface ApprovalReadyResult {
  outcome: "approval_ready";
  product_id: string;
  product_name: string;
  rationale: string;
  risk_flags: string[];
  read_only: true;
}

export interface ScanFailure {
  error_code: string;
  message: string;
  retryable: boolean;
  retry_count: number;
}

export interface Scan {
  scan_id: string;
  status: ScanStatus;
  trigger: ScanTrigger;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: ApprovalReadyResult | null;
  error: ScanFailure | null;
}

interface RequestOptions {
  signal?: AbortSignal;
}

interface ApiResponse {
  body: unknown;
  status: number;
}

export class ApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(code: string, message: string, retryable: boolean) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function invalidResponse(): never {
  throw new ApiError(
    "INVALID_RESPONSE",
    "The server returned an invalid response.",
    true,
  );
}

function parseResult(value: unknown): ApprovalReadyResult | null {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    value.outcome !== "approval_ready" ||
    typeof value.product_id !== "string" ||
    typeof value.product_name !== "string" ||
    typeof value.rationale !== "string" ||
    !Array.isArray(value.risk_flags) ||
    !value.risk_flags.every((flag) => typeof flag === "string") ||
    value.read_only !== true
  ) {
    return invalidResponse();
  }
  return {
    outcome: "approval_ready",
    product_id: value.product_id,
    product_name: value.product_name,
    rationale: value.rationale,
    risk_flags: value.risk_flags,
    read_only: true,
  };
}

function parseFailure(value: unknown): ScanFailure | null {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    typeof value.error_code !== "string" ||
    typeof value.message !== "string" ||
    typeof value.retryable !== "boolean" ||
    !Number.isInteger(value.retry_count) ||
    (value.retry_count as number) < 0
  ) {
    return invalidResponse();
  }
  return {
    error_code: value.error_code,
    message: value.message,
    retryable: value.retryable,
    retry_count: value.retry_count as number,
  };
}

function parseScan(value: unknown): Scan {
  if (
    !isRecord(value) ||
    typeof value.scan_id !== "string" ||
    typeof value.status !== "string" ||
    !["queued", "running", "succeeded", "failed"].includes(
      value.status,
    ) ||
    typeof value.trigger !== "string" ||
    !["manual", "cron"].includes(value.trigger) ||
    typeof value.created_at !== "string" ||
    !isNullableString(value.started_at) ||
    !isNullableString(value.completed_at)
  ) {
    return invalidResponse();
  }

  const result = parseResult(value.result);
  const error = parseFailure(value.error);
  if (
    (value.status === "succeeded" && result === null) ||
    (value.status === "failed" && error === null) ||
    (["queued", "running"].includes(value.status) &&
      (result !== null || error !== null))
  ) {
    return invalidResponse();
  }

  return {
    scan_id: value.scan_id,
    status: value.status as ScanStatus,
    trigger: value.trigger as ScanTrigger,
    created_at: value.created_at,
    started_at: value.started_at,
    completed_at: value.completed_at,
    result,
    error,
  };
}

function parsePublicError(value: unknown): ApiError {
  if (
    isRecord(value) &&
    typeof value.error_code === "string" &&
    typeof value.message === "string" &&
    typeof value.retryable === "boolean"
  ) {
    return new ApiError(value.error_code, value.message, value.retryable);
  }
  return new ApiError(
    "REQUEST_FAILED",
    "The request could not be completed.",
    true,
  );
}

async function request(
  path: string,
  init: RequestInit,
): Promise<ApiResponse> {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      ...init,
    });
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new ApiError(
      "NETWORK_ERROR",
      "The service could not be reached.",
      true,
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return invalidResponse();
  }
  if (!response.ok) {
    throw parsePublicError(body);
  }
  return { body, status: response.status };
}

export async function createManualScan(
  options: RequestOptions = {},
): Promise<Scan> {
  const response = await request(SCANS_PATH, {
    method: "POST",
    signal: options.signal,
  });
  if (response.status !== 202) {
    return invalidResponse();
  }
  return parseScan(response.body);
}

export async function listScans(
  options: RequestOptions = {},
): Promise<Scan[]> {
  const response = await request(SCANS_PATH, {
    method: "GET",
    signal: options.signal,
  });
  if (
    !isRecord(response.body) ||
    !Array.isArray(response.body.scans) ||
    response.body.scans.length > MAX_SCAN_LIST_LENGTH
  ) {
    return invalidResponse();
  }
  return response.body.scans.map(parseScan);
}

export async function getScan(
  scanId: string,
  options: RequestOptions = {},
): Promise<Scan> {
  const response = await request(`${SCANS_PATH}/${encodeURIComponent(scanId)}`, {
    method: "GET",
    signal: options.signal,
  });
  return parseScan(response.body);
}
