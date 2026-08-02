import React from "react";
import { render, screen } from "@testing-library/react";
import { IntegrationsClient } from "../IntegrationsClient";
import { Integration } from "@/entities/aiops/model/types";

const mockUseSWR = jest.fn();

jest.mock("swr", () => ({
  __esModule: true,
  default: (key: string | null, fetcher: unknown, config: unknown) =>
    mockUseSWR(key, fetcher, config),
  useSWRConfig: () => ({ mutate: jest.fn() }),
}));

function integration(overrides: Partial<Integration> = {}): Integration {
  return {
    name: "datadog",
    label: "Datadog",
    mode: "stub",
    tools: ["dd_query_metrics", "dd_list_events"],
    notes: "",
    provider: null,
    provider_types: ["datadog"],
    ambient_credentials: false,
    ...overrides,
  };
}

function mockList(data: Integration[] | undefined, overrides = {}) {
  mockUseSWR.mockReturnValue({ data, error: undefined, isLoading: false, ...overrides });
}

beforeEach(() => {
  mockUseSWR.mockReset();
});

describe("IntegrationsClient", () => {
  it("warns how many backends are still returning demo data", () => {
    mockList([
      integration(),
      integration({ name: "jira", label: "Jira", mode: "live" }),
    ]);

    render(<IntegrationsClient />);

    expect(screen.getByText("Demo data in play")).toBeInTheDocument();
    expect(screen.getByText(/2 integrations are on stub/)).toBeInTheDocument();
  });

  it("says nothing when every backend is live", () => {
    mockList([integration({ mode: "live" })]);

    render(<IntegrationsClient />);

    expect(screen.queryByText("Demo data in play")).not.toBeInTheDocument();
  });

  it("shows the installed Keep provider backing an integration", () => {
    mockList([
      integration({
        mode: "live",
        provider: { id: "prov-dd", type: "datadog", display_name: "Datadog" },
      }),
    ]);

    render(<IntegrationsClient />);

    const link = screen.getByText("Datadog", { selector: "a" });
    expect(link).toHaveAttribute("href", "/providers");
  });

  it("links to the install flow when no provider backs it", () => {
    mockList([integration()]);

    render(<IntegrationsClient />);

    expect(screen.getByText(/install datadog/)).toBeInTheDocument();
  });

  it("does not ask for credentials — Keep providers own them", () => {
    mockList([integration()]);

    render(<IntegrationsClient />);

    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/type to replace/)).not.toBeInTheDocument();
  });

  it("marks backends that use ambient credentials", () => {
    mockList([
      integration({
        name: "k8s",
        label: "Kubernetes",
        mode: "live",
        ambient_credentials: true,
        provider_types: ["kubernetes"],
        tools: ["get_pods"],
      }),
    ]);

    render(<IntegrationsClient />);

    expect(screen.getByText("ambient credentials")).toBeInTheDocument();
  });

  it("renders an error state when the request fails", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });

    render(<IntegrationsClient />);

    expect(screen.getByText(/Could not load integrations/)).toBeInTheDocument();
  });
});
