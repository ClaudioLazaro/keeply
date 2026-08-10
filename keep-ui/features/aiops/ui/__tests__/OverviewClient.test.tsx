import React from "react";
import { render, screen } from "@testing-library/react";
import { OverviewClient } from "../OverviewClient";

const mockUseSWR = jest.fn();

jest.mock("swr", () => ({
  __esModule: true,
  default: (key: string | null, fetcher: unknown, config: unknown) =>
    mockUseSWR(key, fetcher, config),
}));

function stats(overrides: Record<string, unknown> = {}) {
  return {
    investigations_total: 22,
    investigations_by_status: {
      queued: 0,
      gathering: 0,
      hypothesizing: 0,
      rca_ready: 16,
      failed: 6,
      cancelled: 0,
    },
    investigations_last_24h: 3,
    evidence_total: 304,
    evidence_gaps: 31,
    evidence_by_provenance: { live: 35, stub: 238, gap: 31 },
    investigations_daily: [
      { date: "2026-08-01", count: 2 },
      { date: "2026-08-02", count: 0 },
      { date: "2026-08-03", count: 5 },
    ],
    feedback_useful: 3,
    feedback_not_useful: 1,
    budget: { max_tool_calls: 50, max_wall_time_seconds: 120, max_llm_tokens: 200000 },
    mode: "suggest",
    llm_enabled: false,
    llm_spend: { usd: 1.23, priced_completions: 4, unpriced_completions: 0 },
    ...overrides,
  };
}

function mockStats(data: unknown) {
  mockUseSWR.mockReturnValue({ data, error: undefined, isLoading: false });
}

beforeEach(() => mockUseSWR.mockReset());

describe("OverviewClient", () => {
  it("leads with how much of the evidence is real", () => {
    // The first question is not "how many investigations" but "can I trust
    // this". 35 live of 304 is 12%.
    mockStats(stats());
    render(<OverviewClient />);
    // Twice on purpose: the headline and the table row. The table exists
    // because nothing here may be available only as colour.
    expect(screen.getAllByText("12%").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText(/came from a real system/).length).toBeGreaterThan(0);
  });

  it("warns when the analysis rests mostly on demo data", () => {
    mockStats(stats());
    render(<OverviewClient />);
    expect(screen.getByText("Mostly demo data")).toBeInTheDocument();
  });

  it("escalates the warning when nothing was read from a real system", () => {
    mockStats(stats({ evidence_by_provenance: { stub: 100, gap: 4 } }));
    render(<OverviewClient />);
    expect(screen.getByText("No live evidence")).toBeInTheDocument();
    expect(screen.getByText(/questions, not findings/)).toBeInTheDocument();
  });

  it("stays quiet when the evidence is predominantly real", () => {
    mockStats(stats({ evidence_by_provenance: { live: 90, stub: 5, gap: 5 } }));
    render(<OverviewClient />);
    expect(screen.queryByText("Mostly demo data")).not.toBeInTheDocument();
    expect(screen.queryByText("No live evidence")).not.toBeInTheDocument();
  });

  it("marks spend as a floor when some completions could not be priced", () => {
    // An unpriced model contributes nothing to the total, so the figure is an
    // underestimate — reporting it flat would be the same error as showing
    // stub evidence as live.
    mockStats(
      stats({ llm_spend: { usd: 1.23, priced_completions: 4, unpriced_completions: 2 } })
    );
    render(<OverviewClient />);
    expect(screen.getByText(/this is a floor/)).toBeInTheDocument();
  });

  it("offers the provenance breakdown as a table, not only as colour", () => {
    mockStats(stats());
    render(<OverviewClient />);
    expect(screen.getByText("Evidence provenance as a table")).toBeInTheDocument();
    // Every state is listed even at zero, so absence is visible rather than
    // indistinguishable from a state that was never possible.
    expect(screen.getAllByText("unknown").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/did not say where this came from/)).toBeInTheDocument();
  });

  it("describes the composition for a screen reader", () => {
    mockStats(stats());
    render(<OverviewClient />);
    const chart = screen.getByRole("img", { name: /live: 35/ });
    expect(chart).toHaveAccessibleName(/stub: 238/);
  });

  it("says why the LLM path is inactive rather than only that it is", () => {
    mockStats(stats({ llm_enabled: false }));
    render(<OverviewClient />);
    expect(screen.getByText(/install an AI provider/)).toBeInTheDocument();
  });
});
