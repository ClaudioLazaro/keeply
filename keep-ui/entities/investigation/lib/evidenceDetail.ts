import { InvestigationEvidence } from "../model/types";

/**
 * Render what an evidence item actually found.
 *
 * Deliberately mirrors `evidence_detail` in
 * `keep-aiops/aiops_api/modules/rca/draft.py`, including its limits: the
 * operator and the model should be looking at the same thing. If the two
 * drift, an operator reviewing a hypothesis is checking it against different
 * evidence than the one that produced it.
 *
 * Bounded rather than dumped — 200 pods would bury the incident — and
 * truncation is marked, so nobody mistakes a slice for the whole.
 */

export const DETAIL_MAX_CHARS = 700;
export const DETAIL_MAX_LIST_ITEMS = 5;

/** Fields rendered as their own badge; repeating them here is noise. */
const RENDERED_ELSEWHERE = new Set(["backend", "cluster"]);

function compact(value: unknown, limit: number): string {
  const text = (typeof value === "string" ? value : JSON.stringify(value) ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

/**
 * The target that answered — the cluster, account or endpoint.
 *
 * A result that cannot say where it came from is not auditable evidence, so
 * this is surfaced next to the provenance badge rather than buried in the
 * payload where only a developer would find it.
 */
export function targetOf(evidence: InvestigationEvidence): string | null {
  const result = evidence.payload?.result;
  if (!result || typeof result !== "object") return null;
  const cluster = (result as Record<string, unknown>).cluster;
  return typeof cluster === "string" && cluster ? cluster : null;
}

export function evidenceDetail(
  evidence: InvestigationEvidence,
  maxChars: number = DETAIL_MAX_CHARS
): string {
  const payload = evidence.payload;
  if (!payload) return "";

  if (payload.error) return compact(`failed: ${payload.error}`, maxChars);

  const result = payload.result;
  if (result === null || result === undefined) return "";
  if (typeof result !== "object") return compact(result, maxChars);

  const parts: string[] = [];
  for (const [key, value] of Object.entries(result as Record<string, unknown>)) {
    if (RENDERED_ELSEWHERE.has(key)) continue;
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      const shown = value.slice(0, DETAIL_MAX_LIST_ITEMS).map((e) => compact(e, 160));
      const more = value.length - shown.length;
      parts.push(`${key}: ${shown.join(" | ")}${more > 0 ? ` (+${more} more)` : ""}`);
    } else if (value !== null && value !== undefined && value !== "") {
      parts.push(`${key}: ${compact(value, 160)}`);
    }
  }
  return compact(parts.join("; "), maxChars);
}

/** The arguments a tool was called with — the "what did we ask" half. */
export function evidenceArguments(evidence: InvestigationEvidence): string {
  const args = evidence.payload?.arguments;
  if (!args || Object.keys(args).length === 0) return "";
  return Object.entries(args)
    .map(([k, v]) => `${k}=${compact(v, 60)}`)
    .join(" ");
}
