"use client";

import { Badge } from "@tremor/react";
import { InvestigationStatus } from "../model/types";

/**
 * Single source of truth for how an investigation status looks.
 *
 * It lives in the entity layer because both the incident panel
 * (features/investigation) and the AIOps console (features/aiops) render
 * it — a status must never appear in two different colours in two views.
 * The map is a total `Record`, so adding a status to the FSM is a compile
 * error here until it gets a label.
 */
export const INVESTIGATION_STATUS_BADGE: Record<
  InvestigationStatus,
  { color: string; label: string }
> = {
  queued: { color: "gray", label: "Queued" },
  gathering: { color: "blue", label: "Gathering evidence" },
  hypothesizing: { color: "violet", label: "Generating hypotheses" },
  rca_ready: { color: "emerald", label: "RCA ready" },
  failed: { color: "red", label: "Failed" },
  cancelled: { color: "gray", label: "Cancelled" },
};

export function InvestigationStatusBadge({
  status,
}: {
  status: InvestigationStatus;
}) {
  const badge = INVESTIGATION_STATUS_BADGE[status] ?? {
    color: "gray",
    label: status,
  };
  return (
    <Badge color={badge.color} size="xs">
      {badge.label}
    </Badge>
  );
}
