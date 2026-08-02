import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SuggestedRules } from "../SuggestedRules";

const mockUseSWR = jest.fn();
const mockMutate = jest.fn();

jest.mock("swr", () => ({
  __esModule: true,
  default: (key: unknown, fetcher: unknown, config: unknown) =>
    mockUseSWR(key, fetcher, config),
  useSWRConfig: () => ({ mutate: mockMutate }),
}));

const SUGGESTION = {
  id: "sug-1",
  name: "orders-api correlation",
  cel: "service == 'orders-api' && source == 'prometheus'",
  grouping_criteria: ["service"],
  timeframe_seconds: 600,
  occurrences: 3,
  alerts_covered: 6,
  rationale: "Seen 3 times covering 6 alerts.",
  status: "pending" as const,
  created_rule_id: null,
  created_at: "2026-08-02T21:00:00Z",
};

function mockSuggestions(data: unknown, error?: unknown) {
  mockUseSWR.mockReturnValue({ data, error, isLoading: false });
}

beforeEach(() => {
  mockUseSWR.mockReset();
  mockMutate.mockReset();
  global.fetch = jest.fn();
});

describe("SuggestedRules", () => {
  it("shows the evidence behind a proposal", () => {
    mockSuggestions([SUGGESTION]);

    render(<SuggestedRules />);

    expect(screen.getByText("orders-api correlation")).toBeInTheDocument();
    expect(screen.getByText("seen 3×")).toBeInTheDocument();
    expect(screen.getByText("6 alerts")).toBeInTheDocument();
    expect(screen.getByText(SUGGESTION.cel)).toBeInTheDocument();
  });

  it("renders nothing when the AIOps plane is absent", () => {
    // The rules page must work identically without AIOps installed.
    mockSuggestions(undefined, new Error("unreachable"));

    const { container } = render(<SuggestedRules />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there is nothing to propose", () => {
    mockSuggestions([]);

    const { container } = render(<SuggestedRules />);

    expect(container).toBeEmptyDOMElement();
  });

  it("creates the rule through the accept endpoint", async () => {
    mockSuggestions([SUGGESTION]);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ status: "accepted", rule_id: "rule-1" }),
    });

    render(<SuggestedRules />);
    fireEvent.click(screen.getByRole("button", { name: /Create rule/ }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = (global.fetch as jest.Mock).mock.calls[0];
    expect(url).toBe("/api/aiops/v1/correlation/suggestions/sug-1:accept");
    expect(init.method).toBe("POST");
  });

  it("says the created rule still needs approval", async () => {
    mockSuggestions([SUGGESTION]);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ status: "accepted", rule_id: "rule-1" }),
    });

    render(<SuggestedRules />);
    fireEvent.click(screen.getByRole("button", { name: /Create rule/ }));

    expect(await screen.findByText(/needs approval/)).toBeInTheDocument();
  });

  it("dismisses through the dismiss endpoint", async () => {
    mockSuggestions([SUGGESTION]);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ status: "dismissed" }),
    });

    render(<SuggestedRules />);
    fireEvent.click(screen.getByRole("button", { name: /Dismiss/ }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect((global.fetch as jest.Mock).mock.calls[0][0]).toBe(
      "/api/aiops/v1/correlation/suggestions/sug-1:dismiss"
    );
  });

  it("surfaces a rejection instead of implying the rule was created", async () => {
    mockSuggestions([SUGGESTION]);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({ detail: "Invalid CEL expression" }),
    });

    render(<SuggestedRules />);
    fireEvent.click(screen.getByRole("button", { name: /Create rule/ }));

    expect(await screen.findByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Invalid CEL expression")).toBeInTheDocument();
  });
});
