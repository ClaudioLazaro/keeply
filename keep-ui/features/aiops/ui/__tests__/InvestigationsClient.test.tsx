import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { InvestigationsClient } from "../InvestigationsClient";
import { Investigation } from "@/entities/investigation/model/types";

const mockUseSWR = jest.fn();

jest.mock("swr", () => ({
  __esModule: true,
  default: (key: string | null, fetcher: unknown, config: unknown) =>
    mockUseSWR(key, fetcher, config),
}));

function investigation(overrides: Partial<Investigation> = {}): Investigation {
  return {
    id: "inv-1",
    tenant_id: "keep",
    incident_id: "abcdef12-3456-7890-abcd-ef1234567890",
    status: "rca_ready",
    rca_draft: "draft",
    error: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function mockList(data: Investigation[] | undefined, extra = {}) {
  mockUseSWR.mockReturnValue({
    data,
    error: undefined,
    isLoading: false,
    ...extra,
  });
}

beforeEach(() => {
  mockUseSWR.mockReset();
});

describe("InvestigationsClient", () => {
  it("lists investigations newest first", () => {
    // aiops-api returns oldest-first; the console must invert that.
    mockList([
      investigation({ id: "old", incident_id: "11111111-aaaa" }),
      investigation({ id: "new", incident_id: "22222222-bbbb" }),
    ]);

    render(<InvestigationsClient />);

    const links = screen.getAllByText(/^(11111111|22222222)$/);
    expect(links[0]).toHaveTextContent("22222222");
    expect(links[1]).toHaveTextContent("11111111");
  });

  it("links each row to its incident", () => {
    mockList([investigation()]);

    render(<InvestigationsClient />);

    expect(screen.getByText("abcdef12").closest("a")).toHaveAttribute(
      "href",
      "/incidents/abcdef12-3456-7890-abcd-ef1234567890"
    );
  });

  it("shows the failure reason on a failed investigation", () => {
    mockList([
      investigation({
        status: "failed",
        error: "BudgetExceeded(tool_calls): tool_calls=2 > limit=1",
      }),
    ]);

    render(<InvestigationsClient />);

    expect(screen.getByText(/BudgetExceeded\(tool_calls\)/)).toBeInTheDocument();
  });

  it("filters by incident id", () => {
    mockList([
      investigation({ id: "a", incident_id: "aaaaaaaa-1111" }),
      investigation({ id: "b", incident_id: "bbbbbbbb-2222" }),
    ]);

    render(<InvestigationsClient />);
    fireEvent.change(
      screen.getByPlaceholderText(/Filter by incident or investigation id/),
      { target: { value: "aaaaaaaa" } }
    );

    expect(screen.getByText("aaaaaaaa")).toBeInTheDocument();
    expect(screen.queryByText("bbbbbbbb")).not.toBeInTheDocument();
  });

  it("distinguishes an empty backend from an over-filtered view", () => {
    mockList([]);
    const { rerender } = render(<InvestigationsClient />);
    expect(screen.getByText(/No investigations yet/)).toBeInTheDocument();

    mockList([investigation({ incident_id: "aaaaaaaa-1111" })]);
    rerender(<InvestigationsClient />);
    fireEvent.change(
      screen.getByPlaceholderText(/Filter by incident or investigation id/),
      { target: { value: "zzzzzzzz" } }
    );
    expect(screen.getByText(/No investigations match this filter/)).toBeInTheDocument();
  });

  it("renders an error state when the request fails", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });

    render(<InvestigationsClient />);

    expect(screen.getByText(/Could not load investigations/)).toBeInTheDocument();
  });
});
