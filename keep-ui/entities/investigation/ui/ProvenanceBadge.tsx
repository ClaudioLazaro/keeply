"use client";

import { Badge, Callout } from "@tremor/react";
import { InvestigationEvidence } from "../model/types";

/**
 * Provenance rendering, shared by the incident panel and the AIOps console.
 *
 * A stub tool result is a canned demo payload. Once rendered it looks
 * exactly like production telemetry, so an RCA built on it reads just as
 * confidently — which invites an operator to act on fiction during an
 * incident. Everything here exists to make that impossible to miss.
 */

export type Provenance = "live" | "stub" | "gap" | "unknown";

const PROVENANCE: Record<Provenance, { color: string; label: string; title: string }> = {
  live: { color: "emerald", label: "live", title: "Read from a real system" },
  stub: {
    color: "amber",
    label: "stub",
    title: "Canned demo payload — not real data from your environment",
  },
  gap: { color: "red", label: "gap", title: "The tool call failed; no data collected" },
  unknown: {
    color: "gray",
    label: "unknown",
    title: "The tool did not report where this came from",
  },
};

export function provenanceOf(evidence: InvestigationEvidence): Provenance {
  return (evidence.backend as Provenance) ?? "unknown";
}

export function ProvenanceBadge({ value }: { value: Provenance }) {
  const badge = PROVENANCE[value] ?? PROVENANCE.unknown;
  return (
    <Badge color={badge.color} size="xs" tooltip={badge.title}>
      {badge.label}
    </Badge>
  );
}

export function tallyProvenance(
  evidence: InvestigationEvidence[]
): Record<Provenance, number> {
  const counts: Record<Provenance, number> = {
    live: 0,
    stub: 0,
    gap: 0,
    unknown: 0,
  };
  for (const item of evidence) {
    counts[provenanceOf(item)] += 1;
  }
  return counts;
}

/**
 * The banner an operator must see before reading any hypothesis.
 * Returns null only when every item is live — the one case with nothing
 * to warn about.
 */
export function ProvenanceSummary({
  evidence,
}: {
  evidence: InvestigationEvidence[];
}) {
  if (evidence.length === 0) return null;
  const counts = tallyProvenance(evidence);
  const nonLive = counts.stub + counts.unknown;
  if (nonLive === 0 && counts.gap === 0) return null;

  const parts = [`${counts.live} live`];
  if (counts.stub) parts.push(`${counts.stub} stub`);
  if (counts.gap) parts.push(`${counts.gap} gap`);
  if (counts.unknown) parts.push(`${counts.unknown} unknown`);
  const breakdown = parts.join(" · ");

  // No live evidence at all is the dangerous case: every hypothesis below
  // rests on demo data.
  if (counts.live === 0 && counts.stub > 0) {
    return (
      <Callout title="No live evidence" color="red" className="mb-3">
        {breakdown}. This analysis rests entirely on demo data and must not be
        used to make incident decisions.
      </Callout>
    );
  }

  return (
    <Callout title="Mixed evidence provenance" color="amber" className="mb-3">
      {breakdown}. Hypotheses supported only by stub evidence are marked
      unverified.
    </Callout>
  );
}
