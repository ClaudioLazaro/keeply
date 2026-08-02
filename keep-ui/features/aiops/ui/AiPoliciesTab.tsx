"use client";

import { useState } from "react";
import {
  Badge,
  Button,
  Callout,
  Card,
  Select,
  SelectItem,
  Text,
  TextInput,
} from "@tremor/react";
import { RiDeleteBinLine } from "react-icons/ri";
import { useAiopsPolicies, usePolicyActions } from "@/entities/aiops/model/useAiops";
import { Policy, PolicyDecision, PolicyRule } from "@/entities/aiops/model/types";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  PolicyDecisionBadge,
  formatDateTime,
} from "./shared";

const EXECUTION_CLASSES = ["read", "mutate"];
const DECISIONS: PolicyDecision[] = ["allow", "deny", "approval_required"];

/** "a, b , ,c" -> ["a", "b", "c"] — the editor takes comma-separated lists. */
function parseList(raw: string): string[] {
  return raw
    .split(",")
    .map((item: string) => item.trim())
    .filter((item: string) => item.length > 0);
}

function ReadOnlyRule({ rule }: { rule: PolicyRule }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm py-1.5 border-b border-tremor-border last:border-0">
      <Badge color="gray" size="xs">
        {rule.execution_class}
      </Badge>
      <PolicyDecisionBadge decision={rule.decision} />
      <span className="text-xs text-tremor-content">
        tools: <code>{rule.tools.join(", ")}</code>
      </span>
      <span className="text-xs text-tremor-content">
        env: <code>{rule.environments.join(", ")}</code>
      </span>
    </li>
  );
}

function RuleEditor({
  rule,
  onChange,
  onRemove,
  canRemove,
}: {
  rule: PolicyRule;
  onChange: (next: PolicyRule) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  return (
    <li className="flex flex-wrap items-end gap-2 py-2 border-b border-tremor-border last:border-0">
      <div className="w-28">
        <label className="block text-xs text-tremor-content mb-1">Class</label>
        <Select
          value={rule.execution_class}
          onValueChange={(next) => onChange({ ...rule, execution_class: next })}
          enableClear={false}
        >
          {EXECUTION_CLASSES.map((item) => (
            <SelectItem key={item} value={item}>
              {item}
            </SelectItem>
          ))}
        </Select>
      </div>
      <div className="w-44">
        <label className="block text-xs text-tremor-content mb-1">Decision</label>
        <Select
          value={rule.decision}
          onValueChange={(next) =>
            onChange({ ...rule, decision: next as PolicyDecision })
          }
          enableClear={false}
        >
          {DECISIONS.map((item) => (
            <SelectItem key={item} value={item}>
              {item.replace("_", " ")}
            </SelectItem>
          ))}
        </Select>
      </div>
      <div className="flex-1 min-w-[10rem]">
        <label className="block text-xs text-tremor-content mb-1">
          Tools (comma separated, * = any)
        </label>
        <TextInput
          value={rule.tools.join(", ")}
          onValueChange={(next: string) =>
            onChange({ ...rule, tools: parseList(next) })
          }
          placeholder="*"
        />
      </div>
      <div className="flex-1 min-w-[10rem]">
        <label className="block text-xs text-tremor-content mb-1">
          Environments
        </label>
        <TextInput
          value={rule.environments.join(", ")}
          onValueChange={(next: string) =>
            onChange({ ...rule, environments: parseList(next) })
          }
          placeholder="*"
        />
      </div>
      <Button
        size="xs"
        variant="light"
        color="red"
        icon={RiDeleteBinLine}
        onClick={onRemove}
        disabled={!canRemove}
        tooltip={canRemove ? "Remove rule" : "A policy needs at least one rule"}
      />
    </li>
  );
}

function PolicyCard({ policy }: { policy: Policy }) {
  const { savePolicy, isSaving } = usePolicyActions();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Policy>(policy);
  const [error, setError] = useState<string | null>(null);

  function startEditing() {
    setDraft(policy);
    setError(null);
    setEditing(true);
  }

  async function onSave() {
    setError(null);
    const result = await savePolicy(policy.id, {
      tenant_id: draft.tenant_id,
      description: draft.description ?? "",
      rules: draft.rules,
      enabled: draft.enabled,
    });
    if (result.ok) setEditing(false);
    else setError(result.error ?? "Save failed.");
  }

  const current = editing ? draft : policy;

  return (
    <Card className="mb-3">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-medium">{policy.id}</span>
          <Badge color={current.enabled ? "emerald" : "gray"} size="xs">
            {current.enabled ? "enabled" : "disabled"}
          </Badge>
          <Badge color="blue" size="xs">
            {/* "*" is the global scope every tenant inherits. */}
            {policy.tenant_id === "*" ? "global" : policy.tenant_id}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Text className="text-xs">
            updated {formatDateTime(policy.updated_at)}
          </Text>
          {!editing && (
            <Button size="xs" variant="secondary" onClick={startEditing}>
              Edit
            </Button>
          )}
        </div>
      </div>

      {error && (
        <Callout title="Could not save" color="red" className="mb-2">
          {error}
        </Callout>
      )}

      {editing ? (
        <>
          <div className="mb-2">
            <label className="block text-xs text-tremor-content mb-1">
              Description
            </label>
            <TextInput
              value={draft.description ?? ""}
              onValueChange={(next) => setDraft({ ...draft, description: next })}
            />
          </div>

          <ul className="mb-2">
            {draft.rules.map((rule, index) => (
              <RuleEditor
                key={index}
                rule={rule}
                canRemove={draft.rules.length > 1}
                onChange={(next) =>
                  setDraft({
                    ...draft,
                    rules: draft.rules.map((item, i) => (i === index ? next : item)),
                  })
                }
                onRemove={() =>
                  setDraft({
                    ...draft,
                    rules: draft.rules.filter((_, i) => i !== index),
                  })
                }
              />
            ))}
          </ul>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="xs"
              variant="secondary"
              onClick={() =>
                setDraft({
                  ...draft,
                  rules: [
                    ...draft.rules,
                    {
                      execution_class: "read",
                      decision: "allow",
                      tools: ["*"],
                      environments: ["*"],
                    },
                  ],
                })
              }
            >
              Add rule
            </Button>
            <Button
              size="xs"
              variant="secondary"
              onClick={() => setDraft({ ...draft, enabled: !draft.enabled })}
            >
              {draft.enabled ? "Disable policy" : "Enable policy"}
            </Button>
            <div className="flex-1" />
            <Button size="xs" onClick={onSave} loading={isSaving} disabled={isSaving}>
              Save policy
            </Button>
            <Button
              size="xs"
              variant="light"
              onClick={() => setEditing(false)}
              disabled={isSaving}
            >
              Cancel
            </Button>
          </div>

          <Callout title="Rules are evaluated in order" color="gray" className="mt-3">
            The first rule whose class, tool and environment all match decides.
            Anything unmatched is denied — removing a rule makes the policy
            stricter, never looser.
          </Callout>
        </>
      ) : (
        <>
          {policy.description && (
            <Text className="text-sm mb-2">{policy.description}</Text>
          )}
          <ul>
            {policy.rules.map((rule, index) => (
              <ReadOnlyRule key={`${policy.id}-${index}`} rule={rule} />
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

export function AiPoliciesTab() {
  const { policies, isLoading, error } = useAiopsPolicies();

  if (error) return <ErrorState what="policies" />;
  if (isLoading || !policies) return <LoadingState what="policies" />;

  return (
    <div>
      <PageHeader
        title="Policies"
        description="What the agents are allowed to do. Rules are evaluated in order — tenant policies first, then global — and anything unmatched is denied."
      />

      {policies.length === 0 ? (
        <EmptyState message="No policies stored. The fail-closed default applies: everything is denied." />
      ) : (
        policies.map((policy) => <PolicyCard key={policy.id} policy={policy} />)
      )}
    </div>
  );
}
