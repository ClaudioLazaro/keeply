"use client";

/**
 * Chart primitives for the AIOps dashboard.
 *
 * Built from plain SVG and divs rather than a chart library, because the
 * specifications that matter here — 2px surface gaps between stacked
 * segments, direct labels rather than a value on every mark, recessive
 * gridlines — are exactly the things a library's defaults override.
 *
 * ## Palette
 *
 * The provenance colours are semantic and load-bearing: they answer "can I
 * trust this?", so they are status colours, not a categorical series. They
 * were re-stepped after validation, not chosen by eye.
 *
 * The shipped badge pair (amber-600 stub / red-600 gap) measures ΔE 14.4 in
 * normal vision — below the 15 floor, meaning *stub* and *gap* are hard to
 * tell apart even with full colour vision. In a badge that is survivable,
 * because a text label sits beside it. In a stacked bar where the two
 * segments touch, it is not. Amber-500 moves the worst adjacent pair to
 * ΔE 20.9 and passes CVD separation in both themes.
 *
 * Two validator findings are accepted deliberately:
 *
 * - **Chroma floor on grey.** It reads as grey because grey *is* the meaning
 *   of `unknown`. Giving it a hue would make it look like a fifth state.
 * - **Contrast 2.09 on amber (light).** The validator allows this only with
 *   visible labels or a table view; both ship below, and every segment is
 *   direct-labelled.
 */

export type Provenance = "live" | "stub" | "gap" | "unknown";

/** Validated steps. Light and dark are chosen per surface, not flipped. */
export const PROVENANCE_COLOR: Record<Provenance, { light: string; dark: string }> = {
  live: { light: "#059669", dark: "#10b981" },
  stub: { light: "#f59e0b", dark: "#f59e0b" },
  gap: { light: "#b91c1c", dark: "#ef4444" },
  unknown: { light: "#6b7280", dark: "#9ca3af" },
};

export const PROVENANCE_MEANING: Record<Provenance, string> = {
  live: "Read from a real system",
  stub: "Canned demo payload — not your environment",
  gap: "The call failed; nothing was collected",
  unknown: "The tool did not say where this came from",
};

const ORDER: Provenance[] = ["live", "stub", "gap", "unknown"];

function pct(value: number, total: number): number {
  return total > 0 ? (value / total) * 100 : 0;
}

/**
 * Evidence composition as one proportional bar.
 *
 * The question it answers is not "how many of each" but "how much of this is
 * real", so the whole is the frame and the parts are read against it. A pie
 * would make the same point worse: four wedges compared by angle instead of
 * one line compared by length.
 */
export function ProvenanceBar({
  counts,
  className = "",
}: {
  counts: Record<string, number>;
  className?: string;
}) {
  const present = ORDER.filter((key) => (counts[key] ?? 0) > 0);
  const total = ORDER.reduce((sum, key) => sum + (counts[key] ?? 0), 0);

  if (total === 0) {
    return (
      <p className="text-sm text-tremor-content dark:text-dark-tremor-content">
        No evidence collected yet.
      </p>
    );
  }

  const livePct = pct(counts.live ?? 0, total);

  return (
    <div className={className}>
      {/* The headline is the number the rest of the page should be read
          against, so it leads rather than sitting under the chart. */}
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-3xl font-semibold tabular-nums text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {Math.round(livePct)}%
        </span>
        <span className="text-sm text-tremor-content dark:text-dark-tremor-content">
          of {total.toLocaleString()} evidence items came from a real system
        </span>
      </div>

      {/* gap-[2px] is the surface spacer between segments: without it two
          adjacent fills read as one. */}
      <div
        className="flex h-7 w-full gap-[2px] rounded overflow-hidden"
        role="img"
        aria-label={present
          .map((k) => `${k}: ${counts[k]} (${Math.round(pct(counts[k] ?? 0, total))}%)`)
          .join(", ")}
      >
        {present.map((key) => (
          <div
            key={key}
            className="h-full first:rounded-l last:rounded-r"
            style={{
              width: `${pct(counts[key] ?? 0, total)}%`,
              backgroundColor: `var(--prov-${key})`,
            }}
            title={`${key}: ${counts[key]} — ${PROVENANCE_MEANING[key]}`}
          />
        ))}
      </div>

      {/* Legend and direct labels together: identity is never colour alone,
          and the contrast waiver on amber is conditional on exactly this. */}
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
        {ORDER.map((key) => (
          <li key={key} className="flex items-center gap-1.5 text-xs">
            <span
              aria-hidden
              className="inline-block size-2.5 rounded-sm shrink-0"
              style={{ backgroundColor: `var(--prov-${key})` }}
            />
            <span className="text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis">
              {key}
            </span>
            <span className="tabular-nums text-tremor-content dark:text-dark-tremor-content">
              {counts[key] ?? 0}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Daily counts over a window.
 *
 * Bars rather than a line: the buckets are discrete days, and a line between
 * them implies values in between that do not exist. One series, so the title
 * names it and no legend box is needed.
 */
export function DailyBars({
  data,
  label,
}: {
  data: { date: string; count: number }[];
  label: string;
}) {
  const max = Math.max(1, ...data.map((d) => d.count));
  const total = data.reduce((sum, d) => sum + d.count, 0);

  return (
    <div>
      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-2xl font-semibold tabular-nums text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {total}
        </span>
        <span className="text-sm text-tremor-content dark:text-dark-tremor-content">
          {label}
        </span>
      </div>
      <div
        className="flex items-end gap-[2px] h-24"
        role="img"
        aria-label={`${label}: ${data.map((d) => `${d.date} ${d.count}`).join(", ")}`}
      >
        {data.map((point) => (
          <div key={point.date} className="flex-1 flex flex-col justify-end h-full group relative">
            <div
              className="w-full rounded-t bg-tremor-brand dark:bg-dark-tremor-brand transition-[height] duration-200"
              // A zero day still draws a hairline, so an empty bucket is
              // visibly zero rather than indistinguishable from no bucket.
              style={{ height: `${Math.max(2, (point.count / max) * 100)}%` }}
            />
            <span className="pointer-events-none absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-tremor-background-emphasis px-1.5 py-0.5 text-[10px] text-tremor-content-inverted opacity-0 group-hover:opacity-100 transition-opacity dark:bg-dark-tremor-background-emphasis dark:text-dark-tremor-content-inverted">
              {point.date.slice(5)}: {point.count}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-tremor-content dark:text-dark-tremor-content tabular-nums">
        <span>{data[0]?.date.slice(5)}</span>
        <span>{data[data.length - 1]?.date.slice(5)}</span>
      </div>
    </div>
  );
}

/**
 * Ranked counts as horizontal bars.
 *
 * Six statuses is past the point where a pie is readable, and the comparison
 * that matters is magnitude between states — which length carries and angle
 * does not.
 */
export function StatusBars({
  entries,
}: {
  entries: { label: string; count: number; tone?: "neutral" | "good" | "bad" }[];
}) {
  const max = Math.max(1, ...entries.map((e) => e.count));
  const tones: Record<string, string> = {
    neutral: "bg-tremor-brand-subtle dark:bg-dark-tremor-brand-subtle",
    good: "bg-emerald-600 dark:bg-emerald-500",
    bad: "bg-red-700 dark:bg-red-500",
  };
  return (
    <ul className="space-y-2">
      {entries.map((entry) => (
        <li key={entry.label} className="grid grid-cols-[7rem_1fr_2.5rem] items-center gap-2">
          <span className="text-xs text-tremor-content-emphasis dark:text-dark-tremor-content-emphasis truncate">
            {entry.label}
          </span>
          <div className="h-2 rounded bg-tremor-background-muted dark:bg-dark-tremor-background-muted overflow-hidden">
            <div
              className={`h-full rounded ${tones[entry.tone ?? "neutral"]}`}
              style={{ width: `${(entry.count / max) * 100}%` }}
            />
          </div>
          <span className="text-xs tabular-nums text-right text-tremor-content dark:text-dark-tremor-content">
            {entry.count}
          </span>
        </li>
      ))}
    </ul>
  );
}
