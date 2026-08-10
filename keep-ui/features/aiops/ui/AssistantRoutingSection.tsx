"use client";

import { useState } from "react";
import { Badge, Button, Card, Select, SelectItem, Text, TextInput } from "@tremor/react";
import type {
  AgentConfig,
  AssistantUpdate,
  AssistantView,
  LlmProvider,
  ThinkingMode,
} from "@/entities/aiops/model/types";

/**
 * Which model each AI feature uses.
 *
 * The features have genuinely different jobs — drafting a workflow wants
 * fast and cheap, writing a root-cause analysis wants the strongest model
 * available — and a single global setting forces the expensive model onto
 * the cheap job or the weak model onto the one that matters.
 *
 * Two kinds of fact appear on each card and are deliberately styled apart:
 *
 * - **inherited** — this field was not set here, it fell through to the
 *   default. Shown as a hint on the control, because presenting a fallback
 *   as a choice makes an operator think they configured something they did
 *   not, and then surprises the next person who changes the default.
 * - **detected** — the system worked this out by being refused. Shown in
 *   its own block, with the provider's verbatim error, because a workaround
 *   nobody can trace is indistinguishable from a bug.
 */

const THINKING_HELP: Record<ThinkingMode, string> = {
  auto: "Try the full request; adapt only if the provider refuses, and remember what it needed.",
  on: "Assume a reasoning model and apply every known workaround up front.",
  off: "Never adapt. Use when you know the model accepts the standard request.",
};

const DOWNGRADE_LABEL: Record<string, string> = {
  developer_role: "Sends the system role instead of OpenAI's developer role",
  tool_choice: "Offers tools instead of compelling one — suggestions may come back empty",
  reasoning_content: "Adds the empty reasoning field this model requires when replaying a tool call",
};

function inheritedHint(view: AssistantView, field: string): string | undefined {
  return view.inherited.includes(field)
    ? "Using the default below — not set for this feature."
    : undefined;
}

function AssistantCard({
  view,
  providers,
  thinkingModes,
  onSave,
  isSaving,
}: {
  view: AssistantView;
  providers: LlmProvider[];
  thinkingModes: ThinkingMode[];
  onSave: (fn: string, update: AssistantUpdate) => Promise<void>;
  isSaving: boolean;
}) {
  const [draft, setDraft] = useState<AssistantUpdate>({});
  const dirty = Object.keys(draft).length > 0;

  const provider = draft.provider ?? view.provider ?? "";
  const model = draft.model ?? view.model ?? "";
  const thinking = draft.thinking ?? view.thinking;

  function set<K extends keyof AssistantUpdate>(key: K, value: AssistantUpdate[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  return (
    <Card className="mb-3">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <Text className="font-medium">{view.function.replace(/_/g, " ")}</Text>
          <Text className="text-xs">{view.purpose}</Text>
        </div>
        {view.inherited.length === 3 && (
          <Badge color="gray" size="xs">
            using defaults
          </Badge>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <label className="block">
          <span className="block text-sm text-tremor-content-emphasis mb-1">
            Provider
          </span>
          <Select
            value={provider}
            onValueChange={(next) => set("provider", next || null)}
            enableClear
          >
            {providers.map((item) => (
              <SelectItem key={item.type} value={item.type}>
                {item.label}
                {item.configured ? "" : " — no credential"}
              </SelectItem>
            ))}
          </Select>
          <HintText text={inheritedHint(view, "provider")} />
        </label>

        <label className="block">
          <span className="block text-sm text-tremor-content-emphasis mb-1">
            Model
          </span>
          <TextInput
            value={model}
            placeholder="inherit the default"
            onValueChange={(next) => set("model", next || null)}
          />
          <HintText text={inheritedHint(view, "model")} />
        </label>

        <label className="block">
          <span className="block text-sm text-tremor-content-emphasis mb-1">
            Thinking mode
          </span>
          <Select
            value={thinking}
            onValueChange={(next) => set("thinking", next as ThinkingMode)}
          >
            {thinkingModes.map((mode) => (
              <SelectItem key={mode} value={mode}>
                {mode}
              </SelectItem>
            ))}
          </Select>
          <HintText text={THINKING_HELP[thinking]} />
        </label>
      </div>

      {view.detected_downgrades.length > 0 && (
        <div className="mt-3 rounded border border-tremor-border dark:border-dark-tremor-border p-3">
          <Text className="text-xs font-medium mb-1">
            Adjusted automatically for {view.model ?? "this model"}
          </Text>
          <ul className="text-xs space-y-1 text-tremor-content dark:text-dark-tremor-content">
            {view.detected_downgrades.map((name) => (
              <li key={name}>• {DOWNGRADE_LABEL[name] ?? name}</li>
            ))}
          </ul>
          {view.detected_evidence && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
                Why — what the provider said
              </summary>
              <pre className="mt-1 whitespace-pre-wrap break-all text-[11px] text-tremor-content dark:text-dark-tremor-content">
                {view.detected_evidence}
              </pre>
            </details>
          )}
        </div>
      )}

      <div className="mt-3 flex justify-end">
        <Button
          size="xs"
          disabled={!dirty || isSaving}
          loading={isSaving}
          onClick={async () => {
            await onSave(view.function, draft);
            setDraft({});
          }}
        >
          Save
        </Button>
      </div>
    </Card>
  );
}

function HintText({ text }: { text?: string }) {
  if (!text) return null;
  return <p className="text-xs text-tremor-content mt-1">{text}</p>;
}

export function AssistantRoutingSection({
  config,
  providers,
  onSave,
  isSaving,
}: {
  config: AgentConfig;
  providers: LlmProvider[];
  onSave: (fn: string, update: AssistantUpdate) => Promise<void>;
  isSaving: boolean;
}) {
  const assistants = config.assistants ?? [];

  return (
    <div>
      <Text className="font-medium">AI features</Text>
      <Text className="text-xs mb-3">
        Which provider and model each feature uses. Leave a field empty to
        inherit the default below.
      </Text>

      {assistants.length === 0 ? (
        <Card className="mb-3">
          <Text className="text-sm">
            The AI plane did not report any features. Check that it is
            reachable and up to date.
          </Text>
        </Card>
      ) : (
        assistants.map((view) => (
          <AssistantCard
            key={view.function}
            view={view}
            providers={providers}
            thinkingModes={config.available_thinking_modes ?? ["auto", "on", "off"]}
            onSave={onSave}
            isSaving={isSaving}
          />
        ))
      )}
    </div>
  );
}
