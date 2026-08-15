import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("application authentication state", () => {
  it("shows the Cognito sign-in action when no session exists", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error_code: "AUTH_REQUIRED",
            message: "Authentication is required.",
            retryable: false,
          },
          401,
        ),
      ),
    );

    render(<App />);

    const signIn = await screen.findByRole("link", {
      name: "Sign in with Cognito",
    });
    expect(signIn).toHaveAttribute("href", "/auth/login");
  });

  it("loads the overview only after a valid session", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          user_id: "cognito-user-001",
          email: "manager@example.invalid",
          role: "manager",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ scans: [] }));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("No scans yet")).toBeInTheDocument();
    expect(screen.getByText("manager@example.invalid")).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", {
      name: "Application navigation",
    });
    expect(navigation).toHaveTextContent("Home");
    expect(navigation).toHaveTextContent("Scans");
    expect(navigation).not.toHaveTextContent(/analytics|vendors|settings/i);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/session",
      expect.objectContaining({ credentials: "same-origin", method: "GET" }),
    );
  });

  it("opens a distinct scans workspace from the application navigation", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          user_id: "cognito-user-001",
          email: "manager@example.invalid",
          role: "manager",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ scans: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Procurement overview" }),
    ).toBeInTheDocument();
    const scans = screen.getByRole("button", { name: "Scans" });
    await user.click(scans);

    expect(scans).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("heading", { name: "Procurement scans" }),
    ).toBeInTheDocument();
  });
});
