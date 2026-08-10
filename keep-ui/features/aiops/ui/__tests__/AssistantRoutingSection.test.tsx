import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import {
  AssistantRoutingSection,
  providerSelection,
} from "../AssistantRoutingSection";
import type { AgentConfig, AssistantView } from "@/entities/aiops/model/types";

function view(overrides: Partial<AssistantView> = {}): AssistantView {
  return {
    function: "workflow_builder",
    purpose: "Drafts and edits workflows in the builder chat",
    provider: "deepseek",
    provider_id: null,
    model: "deepseek-chat",
    thinking: "auto",
    inherited: [],
    detected_downgrades: [],
    detected_evidence: null,
    ...overrides,
  };
}

function config(assistants: AssistantView[]): AgentConfig {
  return {
    assistants,
    available_thinking_modes: ["auto", "on", "off"],
  } as unknown as AgentConfig;
}

const PROVIDERS = [
  { id: "1", type: "deepseek", label: "DeepSeek", configured: true, suggested_model: "deepseek-chat" },
  { id: "2", type: "openai", label: "OpenAI", configured: false, suggested_model: "gpt-4o" },
];

function renderSection(assistants: AssistantView[], onSave = jest.fn()) {
  render(
    <AssistantRoutingSection
      config={config(assistants)}
      providers={PROVIDERS}
      onSave={onSave}
      isSaving={false}
    />
  );
  return onSave;
}

describe("AssistantRoutingSection", () => {
  it("lists every feature with what it is for", () => {
    renderSection([
      view(),
      view({ function: "rca", purpose: "Writes the root-cause analysis" }),
    ]);

    expect(screen.getByText("workflow builder")).toBeInTheDocument();
    expect(screen.getByText(/Writes the root-cause analysis/)).toBeInTheDocument();
  });

  it("says when a value was inherited rather than chosen here", () => {
    // Showing a fallback as a choice makes an operator believe they
    // configured something they did not — and surprises whoever changes
    // the default next.
    renderSection([view({ inherited: ["provider", "model"] })]);

    expect(screen.getAllByText(/Using the default below/).length).toBe(2);
  });

  it("does not claim inheritance for a field that was set", () => {
    renderSection([view({ inherited: ["provider"] })]);

    expect(screen.getAllByText(/Using the default below/).length).toBe(1);
  });

  it("shows what was adjusted automatically, apart from what was chosen", () => {
    renderSection([
      view({
        detected_downgrades: ["tool_choice"],
        detected_evidence: "400 Thinking mode does not support this tool_choice",
      }),
    ]);

    expect(screen.getByText(/Adjusted automatically/)).toBeInTheDocument();
    // Described in terms of the consequence, not the internal name.
    expect(screen.getByText(/suggestions may come back empty/)).toBeInTheDocument();
  });

  it("keeps the provider's own words available as the cause", () => {
    // A workaround nobody can trace is indistinguishable from a bug.
    renderSection([
      view({
        detected_downgrades: ["tool_choice"],
        detected_evidence: "400 Thinking mode does not support this tool_choice",
      }),
    ]);

    expect(screen.getByText(/Why — what the provider said/)).toBeInTheDocument();
    expect(screen.getByText(/Thinking mode does not support/)).toBeInTheDocument();
  });

  it("shows no adjustment block when the model accepted everything", () => {
    renderSection([view({ detected_downgrades: [] })]);

    expect(screen.queryByText(/Adjusted automatically/)).not.toBeInTheDocument();
  });

  it("saves only the feature that changed", async () => {
    // The server merges per function, but the payload has to be narrow for
    // that to mean anything.
    const onSave = renderSection([
      view(),
      view({ function: "rca", purpose: "Writes the RCA" }),
    ]);

    const [modelInput] = screen.getAllByPlaceholderText("inherit the default");
    fireEvent.change(modelInput, { target: { value: "deepseek-reasoner" } });
    fireEvent.click(screen.getAllByText("Save")[0]);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith("workflow_builder", {
      model: "deepseek-reasoner",
    });
  });

  it("cannot be saved until something changes", () => {
    renderSection([view()]);

    expect(screen.getByText("Save").closest("button")).toBeDisabled();
  });

  it("clearing the model sends null, meaning inherit", () => {
    // Distinct from the empty string, which would store a model named "".
    const onSave = renderSection([view()]);

    fireEvent.change(screen.getByPlaceholderText("inherit the default"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByText("Save"));

    expect(onSave).toHaveBeenCalledWith("workflow_builder", { model: null });
  });

  it("explains what each thinking mode does", () => {
    renderSection([view({ thinking: "auto" })]);

    expect(screen.getByText(/adapt only if the provider refuses/)).toBeInTheDocument();
  });

  it("marks a provider that has no credential", () => {
    renderSection([view()]);

    expect(screen.getByText(/no credential/)).toBeInTheDocument();
  });

  it("says so when the AI plane reported no features at all", () => {
    renderSection([]);

    expect(screen.getByText(/did not report any features/)).toBeInTheDocument();
  });
});

describe("choosing between two accounts of the same vendor", () => {
  const TWO_DEEPSEEK = [
    { id: "cheap", type: "deepseek", label: "DeepSeek Flash", configured: true, suggested_model: "deepseek-chat" },
    { id: "strong", type: "deepseek", label: "DeepSeek Reasoner", configured: true, suggested_model: "deepseek-reasoner" },
  ];


  /**
   * Tremor's Select renders a hidden native <option> alongside the visible
   * listbox item, so a plain text query is ambiguous. Pick the one a person
   * could actually click.
   */
  function option(label: string) {
    const matches = screen.getAllByText(label);
    return matches.find((el) => el.tagName !== "OPTION") ?? matches[0];
  }

  function renderWith(v: AssistantView, onSave = jest.fn()) {
    render(
      <AssistantRoutingSection
        config={config([v])}
        providers={TWO_DEEPSEEK}
        onSave={onSave}
        isSaving={false}
      />
    );
    return onSave;
  }

  it("offers each installation separately, not one entry per type", () => {
    // Two rows of type `deepseek` are indistinguishable by type — which is
    // exactly why the id exists.
    renderWith(view());

    expect(option("DeepSeek Flash")).toBeInTheDocument();
    expect(option("DeepSeek Reasoner")).toBeInTheDocument();
  });

  it("saves the installation and its type together", () => {
    // The id selects the account; the type is what LiteLLM prefixes with.
    // Saving one without the other leaves the routing half-resolved.
    expect(providerSelection("cheap", TWO_DEEPSEEK)).toEqual({
      provider_id: "cheap",
      provider: "deepseek",
    });
  });

  it("clearing the provider clears both fields, not just one", () => {
    expect(providerSelection("", TWO_DEEPSEEK)).toEqual({
      provider_id: null,
      provider: null,
    });
  });
});
