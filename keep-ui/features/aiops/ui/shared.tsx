"use client";

import { Badge, Card, Subtitle, Title } from "@tremor/react";
import { PolicyDecision } from "@/entities/aiops/model/types";

// Status rendering lives in the entity layer — the incident panel renders
// the same badge, and it must never diverge between the two views.
export {
  INVESTIGATION_STATUS_BADGE,
  InvestigationStatusBadge,
} from "@/entities/investigation/ui/InvestigationStatusBadge";

const DECISION_COLOR: Record<PolicyDecision, string> = {
  allow: "emerald",
  deny: "red",
  approval_required: "amber",
};

export function PolicyDecisionBadge({
  decision,
}: {
  decision: PolicyDecision;
}) {
  return (
    <Badge color={DECISION_COLOR[decision] ?? "gray"} size="xs">
      {decision.replace("_", " ")}
    </Badge>
  );
}

export function PageHeader({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="mb-4">
      <Title>{title}</Title>
      <Subtitle>{description}</Subtitle>
    </div>
  );
}

/**
 * Single place that decides what "we could not reach the control plane"
 * looks like. Every console page uses it so a dead aiops-api reads the
 * same way everywhere instead of as an empty table.
 */
export function ErrorState({ what }: { what: string }) {
  return (
    <Card>
      <p className="text-tremor-content text-sm">
        Could not load {what}. The AIOps control plane may be unreachable —
        check that <code>aiops-api</code> is running.
      </p>
    </Card>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <Card>
      <p className="text-tremor-content text-sm">{message}</p>
    </Card>
  );
}

export function LoadingState({ what }: { what: string }) {
  return (
    <Card>
      <p className="text-tremor-content text-sm">Loading {what}…</p>
    </Card>
  );
}

export function formatDateTime(value: string | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

/** Compact relative age ("3m", "2h", "4d") for list rows. */
export function formatAge(value: string | undefined): string {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}
