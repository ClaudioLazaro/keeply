import React from "react";
import { render, screen } from "@testing-library/react";
import { ToolsClient } from "../ToolsClient";
import { ToolCatalogResponse } from "@/entities/aiops/model/types";

const mockUseSWR = jest.fn();

jest.mock("swr", () => ({
  __esModule: true,
  default: (key: string | null, fetcher: unknown, config: unknown) =>
    mockUseSWR(key, fetcher, config),
}));

function mockCatalog(
  catalog: Partial<ToolCatalogResponse> | undefined,
  extra = {},
  integrations: unknown[] = []
) {
  mockUseSWR.mockImplementation((key: string | null) => {
    if (typeof key === "string" && key.includes("integrations")) {
      return { data: integrations, error: undefined, isLoading: false };
    }
    return {
      data: catalog as ToolCatalogResponse | undefined,
      error: undefined,
      isLoading: false,
      ...extra,
    };
  });
}

const READ_TOOL = {
  name: "get_pods",
  description: "List pods",
  execution_class: "read",
  mode: "stub" as const,
  input_schema: {},
  decision: "allow" as const,
  policy_id: "m0-suggest-only",
};

beforeEach(() => {
  mockUseSWR.mockReset();
});

describe("ToolsClient", () => {
  it("renders each tool with its class, decision and policy", () => {
    mockCatalog({
      gateway_url: "http://mcp-gateway:8090",
      gateway_available: true,
      tools: [READ_TOOL],
      error: null,
    });

    render(<ToolsClient />);

    expect(screen.getByText("get_pods")).toBeInTheDocument();
    expect(screen.getByText("read")).toBeInTheDocument();
    expect(screen.getByText("allow")).toBeInTheDocument();
    expect(screen.getByText("m0-suggest-only")).toBeInTheDocument();
  });

  it("flags a catalog that is not entirely read-class", () => {
    mockCatalog({
      gateway_url: "http://mcp-gateway:8090",
      gateway_available: true,
      tools: [
        READ_TOOL,
        {
          name: "restart_pod",
          description: "Restart a pod",
          execution_class: "mutate",
          mode: "stub" as const,
          input_schema: {},
          decision: "deny" as const,
          policy_id: "m0-suggest-only",
        },
      ],
      error: null,
    });

    render(<ToolsClient />);

    // The suggest-only guarantee is the headline: say so when it breaks.
    expect(screen.getByText("1 non-read")).toBeInTheDocument();
    expect(screen.queryByText("all read-class")).not.toBeInTheDocument();
    expect(screen.getByText("deny")).toBeInTheDocument();
  });

  it("confirms the all-read posture when every tool is read-class", () => {
    mockCatalog({
      gateway_url: "http://mcp-gateway:8090",
      gateway_available: true,
      tools: [READ_TOOL],
      error: null,
    });

    render(<ToolsClient />);

    expect(screen.getByText("all read-class")).toBeInTheDocument();
  });

  it("shows the fail-closed default when no policy matched", () => {
    mockCatalog({
      gateway_url: "http://mcp-gateway:8090",
      gateway_available: true,
      tools: [{ ...READ_TOOL, decision: "deny" as const, policy_id: null }],
      error: null,
    });

    render(<ToolsClient />);

    expect(screen.getByText("fail-closed default")).toBeInTheDocument();
  });

  it("renders a callout instead of an empty table when the gateway is down", () => {
    mockCatalog({
      gateway_url: "http://mcp-gateway:8090",
      gateway_available: false,
      tools: [],
      error: "ConnectError: connection refused",
    });

    render(<ToolsClient />);

    expect(screen.getByText("MCP gateway unreachable")).toBeInTheDocument();
    expect(
      screen.getByText(/ConnectError: connection refused/)
    ).toBeInTheDocument();
  });

  it("renders an error state when the request itself fails", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });

    render(<ToolsClient />);

    expect(
      screen.getByText(/Could not load the MCP tool catalog/)
    ).toBeInTheDocument();
  });

  it("shows which Keep provider backs each tool, or an install link", () => {
    mockCatalog(
      {
        gateway_url: "http://mcp-gateway:8090",
        gateway_available: true,
        tools: [READ_TOOL],
        error: null,
      },
      {},
      [
        {
          name: "k8s",
          label: "Kubernetes",
          mode: "live",
          tools: ["get_pods"],
          notes: "",
          provider: { id: "p1", type: "kubernetes", display_name: "Kubernetes" },
          provider_types: ["kubernetes"],
          ambient_credentials: false,
        },
      ]
    );

    render(<ToolsClient />);

    const link = screen.getByText("Kubernetes", { selector: "a" });
    expect(link).toHaveAttribute("href", "/providers");
  });

  it("offers the install flow when no provider backs the tool", () => {
    mockCatalog(
      {
        gateway_url: "http://mcp-gateway:8090",
        gateway_available: true,
        tools: [READ_TOOL],
        error: null,
      },
      {},
      [
        {
          name: "k8s",
          label: "Kubernetes",
          mode: "stub" as const,
          tools: ["get_pods"],
          notes: "",
          provider: null,
          provider_types: ["kubernetes"],
          ambient_credentials: false,
        },
      ]
    );

    render(<ToolsClient />);

    expect(screen.getByText(/install kubernetes/)).toBeInTheDocument();
  });

});
