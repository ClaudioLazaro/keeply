import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AgentSettingsTab } from "../AgentSettingsTab";
import { AgentConfig } from "@/entities/aiops/model/types";

const mockUseSWR = jest.fn();
const mockMutate = jest.fn();

jest.mock("swr", () => ({
  __esModule: true,
  default: (key: string | null, fetcher: unknown, config: unknown) =>
    mockUseSWR(key, fetcher, config),
  useSWRConfig: () => ({ mutate: mockMutate }),
}));

const CONFIG: AgentConfig = {
  tenant_id: "*",
  assistants: [
    {
      function: "workflow_builder",
      purpose: "Drafts and edits workflows in the builder chat",
      provider: "deepseek",
      provider_id: null,
      model: "deepseek/deepseek-v4-pro",
      thinking: "auto",
      // Nothing set for this feature yet — every field fell through.
      inherited: ["model", "provider"],
      detected_downgrades: [],
      detected_evidence: null,
    },
  ],
  available_thinking_modes: ["auto", "on", "off"],
  llm_provider: "deepseek",
  llm_model: "deepseek/deepseek-v4-pro",
  llm_enabled: true,
  llm_api_key: {
    env_var: null,
    present: true,
    source: "Keep provider (DeepSeek)",
    masked: "••••••••af24",
    provider_id: "prov-ds",
    provider_type: "deepseek",
  },
  budget_max_tool_calls: 50,
  budget_max_wall_time_seconds: 120,
  budget_max_llm_tokens: 200000,
  context_timeline_limit: 50,
  llm_embedding_model: null,
  auto_investigate_severities: ["critical", "high"],
  disabled_specialists: [],
  available_specialists: ["kubernetes", "prometheus", "datadog"],
  available_severities: ["critical", "high", "info", "low", "warning"],
};

const PROVIDERS = {
  providers: [
    {
      id: "prov-ds",
      type: "deepseek",
      label: "DeepSeek",
      configured: true,
      suggested_model: "deepseek/deepseek-v4-pro",
    },
  ],
  install_url: "/providers",
};

function mockSwr(config: AgentConfig | undefined, overrides = {}) {
  mockUseSWR.mockImplementation((key: string) => {
    if (typeof key === "string" && key.includes("llm-providers")) {
      return { data: PROVIDERS, error: undefined, isLoading: false };
    }
    return { data: config, error: undefined, isLoading: false, ...overrides };
  });
}

beforeEach(() => {
  mockUseSWR.mockReset();
  mockMutate.mockReset();
  global.fetch = jest.fn();
});

describe("AgentSettingsTab", () => {
  it("shows the active model and that the credential resolves", () => {
    mockSwr(CONFIG);

    render(<AgentSettingsTab />);

    expect(screen.getByText("LLM active")).toBeInTheDocument();
    // Origin names the Keep provider supplying the credential.
    expect(screen.getByText(/via Keep provider \(DeepSeek\)/)).toBeInTheDocument();
  });

  it("never offers a field for the key — Keep providers own credentials", () => {
    mockSwr(CONFIG);

    render(<AgentSettingsTab />);

    expect(screen.queryByText(/API key/)).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText(/Paste the provider API key/)
    ).not.toBeInTheDocument();
    expect(screen.getByText(/via Keep provider \(DeepSeek\)/)).toBeInTheDocument();
  });

  it("links to Providers to manage the credential", () => {
    mockSwr(CONFIG);

    render(<AgentSettingsTab />);

    expect(
      screen.getByText(/Manage this credential in Providers/).closest("a")
    ).toHaveAttribute("href", "/providers");
  });

  it("points at Providers when no AI provider is installed", () => {
    mockUseSWR.mockImplementation((key: string) => {
      if (typeof key === "string" && key.includes("llm-providers")) {
        return { data: { providers: [], install_url: "/providers" }, error: undefined, isLoading: false };
      }
      return { data: CONFIG, error: undefined, isLoading: false };
    });

    render(<AgentSettingsTab />);

    expect(screen.getByText("No AI provider installed")).toBeInTheDocument();
  });

  it("falls back to deterministic rules when no credential resolves", () => {
    mockSwr({
      ...CONFIG,
      llm_enabled: false,
      llm_api_key: { ...CONFIG.llm_api_key, present: false, source: "not configured" },
    });

    render(<AgentSettingsTab />);

    expect(screen.getByText("deterministic fallback")).toBeInTheDocument();
    // Nothing claims a credential is available.
    expect(screen.queryByText(/via /)).not.toBeInTheDocument();
  });

  it("tests the stored credential without sending it from the browser", async () => {
    mockSwr(CONFIG);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        detail: "Connected.",
        models: ["deepseek-v4-flash", "deepseek-v4-pro"],
        model_tested: "deepseek/deepseek-v4-pro",
      }),
    });

    render(<AgentSettingsTab />);
    fireEvent.click(screen.getByRole("button", { name: /Test connection/ }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = (global.fetch as jest.Mock).mock.calls[0];
    expect(url).toBe("/api/aiops/v1/config/llm:test");
    // No credential in the body: it comes from the Keep provider.
    expect(JSON.parse(init.body).api_key).toBeUndefined();
    expect(await screen.findByText("Connection OK")).toBeInTheDocument();
  });

  it("offers discovered models as a dropdown after a successful test", async () => {
    mockSwr(CONFIG);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        detail: "Connected.",
        models: ["deepseek-v4-flash", "deepseek-v4-pro"],
        model_tested: "deepseek/deepseek-v4-pro",
      }),
    });

    render(<AgentSettingsTab />);
    fireEvent.click(screen.getByRole("button", { name: /Test connection/ }));

    expect(await screen.findByText(/2 model\(s\) available/)).toBeInTheDocument();
    expect(screen.getByText(/Discovered from your provider account/)).toBeInTheDocument();
  });

  it("reports a failed probe instead of implying the key works", async () => {
    mockSwr(CONFIG);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: false,
        detail: "AuthenticationError: invalid api key",
        models: [],
        model_tested: "deepseek/deepseek-v4-pro",
      }),
    });

    render(<AgentSettingsTab />);
    fireEvent.click(screen.getByRole("button", { name: /Test connection/ }));

    expect(await screen.findByText("Connection failed")).toBeInTheDocument();
    expect(screen.getByText(/invalid api key/)).toBeInTheDocument();
  });

  it("keeps Save disabled until something changes", () => {
    mockSwr(CONFIG);

    render(<AgentSettingsTab />);

    expect(screen.getByRole("button", { name: /Save changes/ })).toBeDisabled();
  });

  it("sends only the changed fields", async () => {
    mockSwr(CONFIG);
    (global.fetch as jest.Mock).mockResolvedValue({ ok: true, json: async () => ({}) });

    render(<AgentSettingsTab />);
    fireEvent.click(screen.getByRole("button", { name: "datadog" }));
    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [, init] = (global.fetch as jest.Mock).mock.calls[0];
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ disabled_specialists: ["datadog"] });
  });

  it("surfaces the server validation message on rejection", async () => {
    mockSwr(CONFIG);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [{ msg: "Value error, llm_api_key_env must be the NAME of an environment variable" }],
      }),
    });

    render(<AgentSettingsTab />);
    fireEvent.click(screen.getByRole("button", { name: "datadog" }));
    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));

    expect(await screen.findByText("Could not save")).toBeInTheDocument();
    expect(
      screen.getByText(/must be the NAME of an environment variable/)
    ).toBeInTheDocument();
  });

  it("renders an error state when config cannot be loaded", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });

    render(<AgentSettingsTab />);

    expect(
      screen.getByText(/Could not load agent configuration/)
    ).toBeInTheDocument();
  });
});

/**
 * Accessibility of the surface where the agent is configured.
 *
 * Found by auditing against the UI/UX rule set: the captions were visible
 * but attached to nothing, selection was carried by an emerald tint alone,
 * and the result of saving was never announced. All three are invisible to
 * sighted mouse users and blocking for everyone else.
 */
describe("AgentSettingsTab accessibility", () => {
  it("associates every caption with its control", () => {
    mockSwr(CONFIG);
    render(<AgentSettingsTab />);

    // getByLabelText only resolves through a real label association, so this
    // fails if the caption goes back to being a detached sibling.
    expect(screen.getByLabelText("Model")).toBeInTheDocument();
    expect(screen.getByLabelText("Provider")).toBeInTheDocument();
  });

  it("exposes severity selection as pressed state, not only as colour", () => {
    mockSwr(CONFIG);
    render(<AgentSettingsTab />);

    const critical = screen.getByRole("button", { name: /critical/i });
    expect(critical).toHaveAttribute("aria-pressed");
  });

  it("announces a failed save instead of only colouring it red", async () => {
    mockSwr(CONFIG);
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "boom" }),
      text: async () => "boom",
    });

    render(<AgentSettingsTab />);
    // Save stays disabled until something changes, so dirty the form first.
    fireEvent.click(screen.getByRole("button", { name: /critical/i }));
    // Exact: each AI feature card now has its own Save, so a loose match
    // would be ambiguous — and would silently start testing a different
    // button the day the page grows another one.
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not save/i);
  });
});
