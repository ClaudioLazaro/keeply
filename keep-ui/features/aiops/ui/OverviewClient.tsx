"use client";

import { Badge, Card, Metric, Text } from "@tremor/react";
import Link from "next/link";
import { useAiopsStats, useAiopsTools } from "@/entities/aiops/model/useAiops";
import {
  ErrorState,
  INVESTIGATION_STATUS_BADGE,
  LoadingState,
  PageHeader,
} from "./shared";
import { InvestigationStatus } from "@/entities/investigation/model/types";

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <Card className="!p-4">
      <Text className="text-xs uppercase tracking-wide">{label}</Text>
      <Metric className="mt-1 text-2xl">{value}</Metric>
      {hint && <Text className="text-xs mt-1">{hint}</Text>}
    </Card>
  );
}

export function OverviewClient() {
  const { stats, isLoading, error } = useAiopsStats();
  const { catalog } = useAiopsTools();

  if (error) return <ErrorState what="AIOps stats" />;
  if (isLoading || !stats) return <LoadingState what="AIOps stats" />;

  const statuses = Object.entries(stats.investigations_by_status) as [
    InvestigationStatus,
    number,
  ][];

  // Gap rate is the honest health signal: how much of the evidence the
  // specialists tried to collect actually came back.
  const gapRate =
    stats.evidence_total > 0
      ? Math.round((stats.evidence_gaps / stats.evidence_total) * 100)
      : 0;

  const rated = stats.feedback_useful + stats.feedback_not_useful;
  const usefulRate =
    rated > 0 ? Math.round((stats.feedback_useful / rated) * 100) : null;

  return (
    <div>
      <PageHeader
        title="AIOps Overview"
        description="State of the AI control plane: investigations, evidence, cost posture."
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <Stat
          label="Investigations"
          value={stats.investigations_total}
          hint={`${stats.investigations_last_24h} in the last 24h`}
        />
        <Stat
          label="Evidence collected"
          value={stats.evidence_total}
          hint={`${stats.evidence_gaps} gaps (${gapRate}%)`}
        />
        <Stat
          label="Rated useful"
          value={usefulRate === null ? "—" : `${usefulRate}%`}
          hint={rated > 0 ? `${rated} rated` : "no feedback yet"}
        />
        <Stat
          label="MCP tools"
          value={catalog?.gateway_available ? catalog.tools.length : "—"}
          hint={
            catalog?.gateway_available
              ? "all read-class"
              : "gateway unreachable"
          }
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Card>
          <Text className="font-medium mb-3">Investigations by status</Text>
          <ul className="space-y-2">
            {statuses.map(([status, count]) => (
              <li key={status} className="flex items-center justify-between">
                <Badge
                  color={INVESTIGATION_STATUS_BADGE[status]?.color ?? "gray"}
                  size="xs"
                >
                  {INVESTIGATION_STATUS_BADGE[status]?.label ?? status}
                </Badge>
                <span className="text-sm tabular-nums">{count}</span>
              </li>
            ))}
          </ul>
          <Link
            href="/aiops/investigations"
            className="text-sm text-orange-600 hover:underline mt-3 inline-block"
          >
            View all investigations →
          </Link>
        </Card>

        <Card>
          <Text className="font-medium mb-3">Execution posture</Text>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-tremor-content">Mode</dt>
              <dd>
                <Badge color="emerald" size="xs">
                  {stats.mode}-only
                </Badge>
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-tremor-content">RCA engine</dt>
              <dd>
                {stats.llm_enabled ? "LLM (LiteLLM)" : "deterministic fallback"}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-tremor-content">Max tool calls</dt>
              <dd className="tabular-nums">{stats.budget.max_tool_calls}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-tremor-content">Max wall time</dt>
              <dd className="tabular-nums">
                {stats.budget.max_wall_time_seconds}s
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-tremor-content">Max LLM tokens</dt>
              <dd className="tabular-nums">
                {stats.budget.max_llm_tokens.toLocaleString()}
              </dd>
            </div>
          </dl>
          <p className="text-xs text-tremor-content mt-3">
            Budgets are per investigation. A breach fails the investigation
            instead of letting it run away.
          </p>
        </Card>
      </div>
    </div>
  );
}
