import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AiPoliciesTab } from "../AiPoliciesTab";
import { Policy } from "@/entities/aiops/model/types";

const mockUseSWR = jest.fn();
const mockMutate = jest.fn();

jest.mock("swr", () => ({
  __esModule: true,
  default: (key: string | null, fetcher: unknown, config: unknown) =>
    mockUseSWR(key, fetcher, config),
  useSWRConfig: () => ({ mutate: mockMutate }),
}));

const POLICY: Policy = {
  id: "m0-suggest-only",
  tenant_id: "*",
  description: "Suggest-only posture",
  enabled: true,
  rules: [
    { execution_class: "read", decision: "allow", tools: ["*"], environments: ["*"] },
    { execution_class: "mutate", decision: "deny", tools: ["*"], environments: ["*"] },
  ],
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

function mockPolicies(data: Policy[] | undefined, overrides = {}) {
  mockUseSWR.mockReturnValue({
    data,
    error: undefined,
    isLoading: false,
    ...overrides,
  });
}

beforeEach(() => {
  mockUseSWR.mockReset();
  mockMutate.mockReset();
  global.fetch = jest.fn();
});

describe("AiPoliciesTab", () => {
  it("renders rules read-only until Edit is pressed", () => {
    mockPolicies([POLICY]);

    render(<AiPoliciesTab />);

    expect(screen.getByText("m0-suggest-only")).toBeInTheDocument();
    expect(screen.getByText("global")).toBeInTheDocument();
    expect(screen.queryByText("Add rule")).not.toBeInTheDocument();
  });

  it("opens an editor with one row per rule", () => {
    mockPolicies([POLICY]);

    render(<AiPoliciesTab />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(screen.getAllByText("Class")).toHaveLength(2);
    expect(screen.getByText("Add rule")).toBeInTheDocument();
  });

  it("PUTs the edited policy", async () => {
    mockPolicies([POLICY]);
    (global.fetch as jest.Mock).mockResolvedValue({ ok: true, json: async () => ({}) });

    render(<AiPoliciesTab />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
    fireEvent.click(screen.getByRole("button", { name: /Save policy/ }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = (global.fetch as jest.Mock).mock.calls[0];
    expect(url).toBe("/api/aiops/v1/policies/m0-suggest-only");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body).rules).toHaveLength(3);
  });

  it("keeps at least one rule — remove is disabled on the last one", () => {
    mockPolicies([
      { ...POLICY, rules: [POLICY.rules[0]] },
    ]);

    render(<AiPoliciesTab />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    const removeButtons = screen
      .getAllByRole("button")
      .filter((button) => button.getAttribute("disabled") !== null);
    expect(removeButtons.length).toBeGreaterThan(0);
  });

  it("surfaces a server rejection instead of pretending it saved", async () => {
    mockPolicies([POLICY]);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: [{ msg: "rules must contain at least 1 item" }] }),
    });

    render(<AiPoliciesTab />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: /Save policy/ }));

    expect(await screen.findByText("Could not save")).toBeInTheDocument();
    expect(screen.getByText(/at least 1 item/)).toBeInTheDocument();
    // Still in edit mode so the operator can fix it.
    expect(screen.getByText("Add rule")).toBeInTheDocument();
  });

  it("warns that the empty policy set means deny-all", () => {
    mockPolicies([]);

    render(<AiPoliciesTab />);

    expect(
      screen.getByText(/fail-closed default applies: everything is denied/)
    ).toBeInTheDocument();
  });
});
