"use client";

import { useEffect, useState } from "react";
import {
  Badge,
  Button,
  Callout,
  Card,
  NumberInput,
  Select,
  SelectItem,
  Text,
  TextInput,
} from "@tremor/react";
import {
  useAgentConfig,
  useAgentConfigActions,
  useLlmConnectionTest,
  useLlmProviders,
} from "@/entities/aiops/model/useAiops";
import Link from "next/link";
import { AgentConfigUpdate } from "@/entities/aiops/model/types";
import { ErrorState, LoadingState, PageHeader } from "./shared";

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="mb-3">
      <Text className="font-medium">{title}</Text>
      <Text className="text-xs mb-3">{description}</Text>
      {children}
    </Card>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-3">
      <label className="block text-sm text-tremor-content-emphasis mb-1">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-tremor-content mt-1">{hint}</p>}
    </div>
  );
}

export function AgentSettingsTab() {
  const { config, isLoading, error } = useAgentConfig();
  const { providers } = useLlmProviders();
  const { save, isSaving } = useAgentConfigActions();
  const { test, isTesting, result: testResult, clear: clearTest } =
    useLlmConnectionTest();

  const [draft, setDraft] = useState<AgentConfigUpdate>({});
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(
    null
  );

  // Models the provider actually offers, once a successful test has run.
  const discovered = testResult?.ok ? testResult.models : [];

  // Reset the draft whenever the server state changes, so the form always
  // shows what is actually stored rather than a stale edit.
  useEffect(() => {
    setDraft({});
  }, [config?.tenant_id, config?.llm_model, config?.budget_max_tool_calls]);

  if (error) return <ErrorState what="agent configuration" />;
  if (isLoading || !config) return <LoadingState what="agent configuration" />;

  const value = <K extends keyof AgentConfigUpdate>(
    key: K,
    fallback: NonNullable<AgentConfigUpdate[K]>
  ) => (draft[key] !== undefined ? draft[key] : fallback) as NonNullable<
    AgentConfigUpdate[K]
  >;

  const set = <K extends keyof AgentConfigUpdate>(
    key: K,
    next: AgentConfigUpdate[K]
  ) => {
    setDraft((current) => ({ ...current, [key]: next }));
    setFeedback(null);
    // A changed credential or model invalidates the previous probe.
    // Changing provider or model invalidates the previous probe.
    if (key === "llm_provider" || key === "llm_model") clearTest();
  };

  const dirty = Object.keys(draft).length > 0;

  async function onSave() {
    const result = await save(draft);
    setFeedback(
      result.ok
        ? { ok: true, message: "Configuration saved." }
        : { ok: false, message: result.error ?? "Save failed." }
    );
    if (result.ok) setDraft({});
  }

  async function onTest() {
    // No key is sent: the credential lives in the Keep provider, so the
    // browser has nothing to forward.
    await test({
      llm_model: value("llm_model", config!.llm_model ?? "") || undefined,
    });
  }

  return (
    <div>
      <PageHeader
        title="Agent Settings"
        description="How the investigation agents behave: model routing, cost ceilings, and what they are allowed to run."
      />

      {feedback && (
        <Callout
          title={feedback.ok ? "Saved" : "Could not save"}
          color={feedback.ok ? "emerald" : "red"}
          className="mb-3"
        >
          {feedback.message}
        </Callout>
      )}

      <Section
        title="LLM routing"
        description="Which model writes the RCA draft. Credentials come from the AI provider you install under Providers — there is no second key to manage here."
      >
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <Badge color={config.llm_enabled ? "emerald" : "gray"} size="xs">
            {config.llm_enabled ? "LLM active" : "deterministic fallback"}
          </Badge>
          {config.llm_model && <code className="text-xs">{config.llm_model}</code>}
          {config.llm_api_key.present && (
            <Text className="text-xs">via {config.llm_api_key.source}</Text>
          )}
        </div>

        {providers.length === 0 ? (
          <Callout title="No AI provider installed" color="amber" className="mb-3">
            Install one under{" "}
            <Link href="/providers" className="underline">
              Providers
            </Link>{" "}
            — Anthropic, OpenAI, DeepSeek, Gemini and Ollama are all
            supported. Until then the agents use deterministic rules, which
            still work but produce blunter analysis.
          </Callout>
        ) : (
          <>
            <Field label="Provider" hint="Installed AI providers from Keep.">
              <Select
                value={value("llm_provider", config.llm_provider ?? "")}
                onValueChange={(next: string) => set("llm_provider", next)}
                enableClear={false}
              >
                {providers.map((provider) => (
                  <SelectItem key={provider.type} value={provider.type}>
                    {provider.label}
                    {provider.configured ? "" : " (no credential)"}
                  </SelectItem>
                ))}
              </Select>
            </Field>

            <Field
              label="Model"
              hint={
                discovered.length > 0
                  ? "Discovered from your provider account."
                  : "Test the connection to list the models this account offers."
              }
            >
              {discovered.length > 0 ? (
                <Select
                  value={value("llm_model", config.llm_model ?? "")}
                  onValueChange={(next: string) => set("llm_model", next)}
                  enableClear={false}
                >
                  {discovered.map((model) => (
                    <SelectItem key={model} value={model}>
                      {model}
                    </SelectItem>
                  ))}
                </Select>
              ) : (
                <TextInput
                  value={value("llm_model", config.llm_model ?? "")}
                  onValueChange={(next: string) => set("llm_model", next)}
                  placeholder="provider/model-name"
                />
              )}
            </Field>

            <div className="flex items-center gap-2 mb-2">
              <Button
                size="xs"
                variant="secondary"
                loading={isTesting}
                disabled={isTesting}
                onClick={onTest}
              >
                Test connection
              </Button>
              <Text className="text-xs">
                Runs a real completion using the installed provider credential.
              </Text>
            </div>

            {testResult && (
              <Callout
                title={testResult.ok ? "Connection OK" : "Connection failed"}
                color={testResult.ok ? "emerald" : "red"}
                className="mb-3"
              >
                {testResult.detail}
                {testResult.models.length > 0 && (
                  <span className="block mt-1 text-xs">
                    {testResult.models.length} model(s) available.
                  </span>
                )}
              </Callout>
            )}

            {config.llm_api_key.provider_id && (
              <Link
                href="/providers"
                className="text-xs text-orange-600 hover:underline"
              >
                Manage this credential in Providers →
              </Link>
            )}
          </>
        )}
      </Section>

      <Section
        title="Cost budget"
        description="Per-investigation ceilings. A breach fails the investigation rather than letting it run away."
      >
        <Field label="Max tool calls">
          <NumberInput
            value={value("budget_max_tool_calls", config.budget_max_tool_calls)}
            onValueChange={(next) => set("budget_max_tool_calls", next)}
            min={1}
            max={10000}
          />
        </Field>
        <Field label="Max wall time (seconds)">
          <NumberInput
            value={value(
              "budget_max_wall_time_seconds",
              config.budget_max_wall_time_seconds
            )}
            onValueChange={(next) => set("budget_max_wall_time_seconds", next)}
            min={1}
            max={3600}
          />
        </Field>
        <Field label="Max LLM tokens">
          <NumberInput
            value={value("budget_max_llm_tokens", config.budget_max_llm_tokens)}
            onValueChange={(next) => set("budget_max_llm_tokens", next)}
            min={1}
            max={10000000}
          />
        </Field>
      </Section>

      <Section
        title="Auto-investigation"
        description="Incident severities that automatically start an investigation."
      >
        <div className="flex flex-wrap gap-2">
          {config.available_severities.map((severity) => {
            const current = value(
              "auto_investigate_severities",
              config.auto_investigate_severities
            );
            const enabled = current.includes(severity);
            return (
              <button
                key={severity}
                type="button"
                onClick={() =>
                  set(
                    "auto_investigate_severities",
                    enabled
                      ? current.filter((item) => item !== severity)
                      : [...current, severity]
                  )
                }
                className={`px-2 py-1 rounded-tremor-default border text-xs transition-colors ${
                  enabled
                    ? "border-emerald-600 text-emerald-700 bg-emerald-50"
                    : "border-tremor-border text-tremor-content"
                }`}
              >
                {severity}
              </button>
            );
          })}
        </div>
      </Section>

      <Section
        title="Specialists"
        description="Each specialist contributes evidence from one integration. Turn off the ones whose backend is still on stub to keep demo data out of your investigations."
      >
        <div className="flex flex-wrap gap-2">
          {config.available_specialists.map((specialist) => {
            const disabled = value(
              "disabled_specialists",
              config.disabled_specialists
            );
            const enabled = !disabled.includes(specialist);
            return (
              <button
                key={specialist}
                type="button"
                onClick={() =>
                  set(
                    "disabled_specialists",
                    enabled
                      ? [...disabled, specialist]
                      : disabled.filter((item) => item !== specialist)
                  )
                }
                className={`px-2 py-1 rounded-tremor-default border text-xs transition-colors ${
                  enabled
                    ? "border-emerald-600 text-emerald-700 bg-emerald-50"
                    : "border-tremor-border text-tremor-content line-through"
                }`}
              >
                {specialist}
              </button>
            );
          })}
        </div>
      </Section>

      <div className="flex items-center gap-3">
        <Button onClick={onSave} disabled={!dirty || isSaving} loading={isSaving}>
          Save changes
        </Button>
        {dirty && (
          <Button variant="secondary" onClick={() => setDraft({})} disabled={isSaving}>
            Discard
          </Button>
        )}
        <Text className="text-xs">
          Changes apply to the next investigation — no restart needed.
        </Text>
      </div>
    </div>
  );
}
