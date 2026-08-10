"use client";

import { Badge, Callout, Card, Text } from "@tremor/react";
import Link from "next/link";
import { useAiopsStats } from "@/entities/aiops/model/useAiops";
import { ErrorState, LoadingState, PageHeader } from "./shared";
import { DailyBars, PROVENANCE_MEANING, ProvenanceBar, StatusBars } from "./charts";

/**
 * The AIOps dashboard.
 *
 * Ordered by the question an operator asks first, which is not "how many
 * investigations ran" but **"can I trust what this thing is telling me"**.
 * So evidence provenance leads and everything else is read against it: a
 * platform reporting a hundred investigations built on demo payloads is
 * worse than one reporting three built on real telemetry, and a dashboard
 * that opens with the count says the opposite.
 */

function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "warn";
}) {
  return (
    <Card className="!p-4">
      <Text className="text-xs uppercase tracking-wide">{label}</Text>
      <p
        className={`mt-1 text-2xl font-semibold tabular-nums ${
          tone === "warn"
            ? "text-amber-700 dark:text-amber-400"
            : "text-tremor-content-strong dark:text-dark-tremor-content-strong"
        }`}
      >
        {value}
      </p>
      {hint && <Text className="text-xs mt-1">{hint}</Text>}
    </Card>
  );
}

const STATUS_TONE: Record<string, "neutral" | "good" | "bad"> = {
  rca_ready: "good",
  failed: "bad",
  cancelled: "bad",
};

export function OverviewClient() {
  const { stats, isLoading, error } = useAiopsStats();

  if (error) return <ErrorState what="AIOps stats" />;
  if (isLoading || !stats) return <LoadingState what="AIOps stats" />;

  const provenance = stats.evidence_by_provenance ?? {};
  const live = provenance.live ?? 0;
  const totalEvidence = Object.values(provenance).reduce((a, b) => a + b, 0);
  const livePct = totalEvidence > 0 ? Math.round((live / totalEvidence) * 100) : 0;

  const rated = stats.feedback_useful + stats.feedback_not_useful;
  const usefulRate = rated > 0 ? Math.round((stats.feedback_useful / rated) * 100) : null;
  const spend = stats.llm_spend;

  return (
    <div>
      <PageHeader
        title="AIOps Overview"
        description="What the investigation agents have done, and how much of it rests on real data."
      />

      {/* Leads the page when it matters. A banner under the charts would be
          read after the numbers had already been believed. */}
      {totalEvidence > 0 && livePct < 50 && (
        <Callout
          title={live === 0 ? "No live evidence" : "Mostly demo data"}
          color={live === 0 ? "red" : "amber"}
          className="mb-4"
        >
          {live === 0
            ? "Nothing here was read from your systems. Hypotheses below are patterns matched against demo payloads — treat them as questions, not findings."
            : `Only ${livePct}% of collected evidence came from a real system. Install the providers your agents need under `}
          {live > 0 && (
            <Link href="/providers" className="underline">
              Providers
            </Link>
          )}
          {live > 0 && " to raise it."}
        </Callout>
      )}

      <Card className="mb-4">
        <Text className="font-medium mb-1">Evidence provenance</Text>
        <Text className="text-xs mb-4">
          Whether an analysis rests on your systems or on demo payloads. Every
          hypothesis is discounted in proportion to this.
        </Text>
        <ProvenanceBar counts={provenance} />
      </Card>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <Tile
          label="Investigations"
          value={stats.investigations_total}
          hint={`${stats.investigations_last_24h} in the last 24h`}
        />
        <Tile
          label="Evidence items"
          value={stats.evidence_total}
          hint={`${stats.evidence_gaps} gaps — calls that failed`}
        />
        <Tile
          label="LLM spend"
          value={`$${(spend?.usd ?? 0).toFixed(2)}`}
          // An unpriced completion contributes nothing to the total, so the
          // figure beside it is an underestimate — not a cheap month.
          tone={spend?.unpriced_completions ? "warn" : undefined}
          hint={
            spend?.unpriced_completions
              ? `${spend.unpriced_completions} completions with no known price — this is a floor`
              : `${spend?.priced_completions ?? 0} completions priced`
          }
        />
        <Tile
          label="Rated useful"
          value={usefulRate === null ? "—" : `${usefulRate}%`}
          hint={rated > 0 ? `${rated} rated` : "no feedback yet"}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-4">
        <Card>
          <Text className="font-medium mb-1">Activity</Text>
          <Text className="text-xs mb-3">
            Investigations started per day. A flat line is worth noticing.
          </Text>
          <DailyBars
            data={stats.investigations_daily ?? []}
            label="started in the last 14 days"
          />
        </Card>

        <Card>
          <Text className="font-medium mb-1">Investigations by status</Text>
          <Text className="text-xs mb-3">
            Anything stuck in gathering or hypothesizing after a restart is
            swept to failed, so these are live states.
          </Text>
          <StatusBars
            entries={Object.entries(stats.investigations_by_status).map(
              ([label, count]) => ({
                label: label.replace(/_/g, " "),
                count,
                tone: STATUS_TONE[label] ?? "neutral",
              })
            )}
          />
        </Card>
      </div>

      <Card className="mb-4">
        <Text className="font-medium mb-3">Execution posture</Text>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <span className="flex items-center gap-2">
            <Badge color="blue" size="xs">
              {stats.mode}-only
            </Badge>
            <Text className="text-xs">no action is ever taken automatically</Text>
          </span>
          <span className="flex items-center gap-2">
            <Badge color={stats.llm_enabled ? "emerald" : "gray"} size="xs">
              {stats.llm_enabled ? "LLM active" : "deterministic"}
            </Badge>
            <Text className="text-xs">
              {stats.llm_enabled
                ? "an LLM writes the RCA draft"
                : "rule-based writer — install an AI provider to enable the LLM path"}
            </Text>
          </span>
          <span className="flex items-center gap-2 text-xs">
            <Text className="text-xs">
              budget: {stats.budget.max_tool_calls} calls ·{" "}
              {stats.budget.max_wall_time_seconds}s ·{" "}
              {stats.budget.max_llm_tokens.toLocaleString()} tokens
            </Text>
          </span>
        </div>
      </Card>

      {/* The table the validator's contrast waiver is conditional on, and the
          screen-reader path: nothing on this page is available only as colour. */}
      <details className="text-sm">
        <summary className="cursor-pointer text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
          Evidence provenance as a table
        </summary>
        <table className="mt-2 w-full text-xs">
          <thead>
            <tr className="text-left text-tremor-content dark:text-dark-tremor-content">
              <th className="py-1 font-medium">State</th>
              <th className="py-1 font-medium">Items</th>
              <th className="py-1 font-medium">Share</th>
              <th className="py-1 font-medium">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {(["live", "stub", "gap", "unknown"] as const).map((key) => (
              <tr key={key} className="border-t border-tremor-border dark:border-dark-tremor-border">
                <td className="py-1">{key}</td>
                <td className="py-1 tabular-nums">{provenance[key] ?? 0}</td>
                <td className="py-1 tabular-nums">
                  {totalEvidence > 0
                    ? `${Math.round(((provenance[key] ?? 0) / totalEvidence) * 100)}%`
                    : "—"}
                </td>
                <td className="py-1 text-tremor-content dark:text-dark-tremor-content">
                  {PROVENANCE_MEANING[key]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
