"use client";

import { Disclosure } from "@headlessui/react";
import { Badge, Callout, Card } from "@tremor/react";
import {
  IoChevronDown,
  IoThumbsDown,
  IoThumbsDownOutline,
  IoThumbsUp,
  IoThumbsUpOutline,
} from "react-icons/io5";
import clsx from "clsx";
import { MarkdownHTML } from "@/shared/ui/MarkdownHTML/MarkdownHTML";
import {
  INVESTIGATION_POLL_INTERVAL_MS,
  useInvestigationByIncident,
  useInvestigationEvidence,
  useInvestigationHypotheses,
} from "@/entities/investigation/model/useInvestigation";
import {
  Investigation,
  InvestigationEvidence,
  InvestigationFeedbackRating,
  InvestigationHypothesis,
} from "@/entities/investigation/model/types";
import { InvestigationStatusBadge } from "@/entities/investigation/ui/InvestigationStatusBadge";
import {
  ProvenanceBadge,
  ProvenanceSummary,
  provenanceOf,
} from "@/entities/investigation/ui/ProvenanceBadge";
import { useInvestigationFeedback } from "@/entities/investigation/model/useInvestigationFeedback";
import { useInvestigationFeedbackActions } from "@/entities/investigation/model/useInvestigationActions";

export interface InvestigationPanelProps {
  incidentId: string;
}

function formatConfidence(confidence: number): string {
  const percent =
    confidence <= 1 ? Math.round(confidence * 100) : Math.round(confidence);
  return `${percent}%`;
}

function EvidenceList({ evidence }: { evidence: InvestigationEvidence[] }) {
  if (evidence.length === 0) {
    return (
      <p className="text-tremor-content text-sm">No evidence collected yet.</p>
    );
  }
  return (
    <ul className="space-y-1">
      {evidence.map((item) => (
        <li key={item.id} className="text-sm flex items-start gap-2">
          <ProvenanceBadge value={provenanceOf(item)} />
          <span className="font-mono text-xs bg-tremor-background-muted px-1 py-0.5 rounded">
            {item.tool}
          </span>
          <span className="text-tremor-content-emphasis">{item.summary}</span>
        </li>
      ))}
    </ul>
  );
}

function HypothesisList({
  hypotheses,
}: {
  hypotheses: InvestigationHypothesis[];
}) {
  if (hypotheses.length === 0) {
    return (
      <p className="text-tremor-content text-sm">No hypotheses generated yet.</p>
    );
  }
  return (
    <ul className="space-y-1">
      {hypotheses.map((hypothesis) => (
        <li key={hypothesis.id} className="text-sm flex items-center gap-2 flex-wrap">
          <span className="text-tremor-content-emphasis">
            {hypothesis.title}
          </span>
          <Badge
            color={hypothesis.corroborated === false ? "gray" : "orange"}
            size="xs"
          >
            {formatConfidence(hypothesis.confidence)}
          </Badge>
          {/* Already discounted server-side; the label says why. */}
          {hypothesis.corroborated === false && (
            <Badge color="amber" size="xs" tooltip={hypothesis.caveat}>
              unverified
            </Badge>
          )}
        </li>
      ))}
    </ul>
  );
}

function FeedbackRatingButton({
  rating,
  selected,
  disabled,
  onSelect,
}: {
  rating: InvestigationFeedbackRating;
  selected: boolean;
  disabled: boolean;
  onSelect: (rating: InvestigationFeedbackRating) => void;
}) {
  const Icon =
    rating === "useful"
      ? selected
        ? IoThumbsUp
        : IoThumbsUpOutline
      : selected
        ? IoThumbsDown
        : IoThumbsDownOutline;
  return (
    <button
      type="button"
      aria-label={rating === "useful" ? "Useful" : "Not useful"}
      aria-pressed={selected}
      disabled={disabled}
      onClick={() => onSelect(rating)}
      className={clsx(
        "p-1.5 rounded-tremor-default border transition-colors disabled:opacity-50",
        selected
          ? rating === "useful"
            ? "text-emerald-600 border-emerald-600 bg-emerald-50"
            : "text-red-600 border-red-600 bg-red-50"
          : "text-tremor-content border-tremor-border hover:text-tremor-content-emphasis"
      )}
    >
      <Icon className="size-5" />
    </button>
  );
}

function InvestigationFeedbackSection({
  investigation,
}: {
  investigation: Investigation;
}) {
  const { feedback } = useInvestigationFeedback(investigation.id);
  const { submitFeedback, isSubmitting } = useInvestigationFeedbackActions();

  return (
    <section>
      <h5 className="text-tremor-content text-sm font-medium mb-1">
        Was this investigation useful?
      </h5>
      <div className="flex items-center gap-2">
        <FeedbackRatingButton
          rating="useful"
          selected={feedback?.rating === "useful"}
          disabled={isSubmitting}
          onSelect={(rating) => submitFeedback(investigation.id, rating)}
        />
        <FeedbackRatingButton
          rating="not_useful"
          selected={feedback?.rating === "not_useful"}
          disabled={isSubmitting}
          onSelect={(rating) => submitFeedback(investigation.id, rating)}
        />
      </div>
    </section>
  );
}

function InvestigationPanelBody({
  investigation,
}: {
  investigation: Investigation;
}) {
  const isInFlight =
    investigation.status === "queued" ||
    investigation.status === "gathering" ||
    investigation.status === "hypothesizing";
  const pollConfig = {
    refreshInterval: isInFlight ? INVESTIGATION_POLL_INTERVAL_MS : 0,
  };

  const { evidence, error: evidenceError } = useInvestigationEvidence(
    investigation.id,
    pollConfig
  );
  const { hypotheses, error: hypothesesError } = useInvestigationHypotheses(
    investigation.id,
    pollConfig
  );

  return (
    <div className="space-y-4 pt-3">
      {investigation.status === "failed" && investigation.error && (
        <Callout title="Investigation failed" color="red">
          {investigation.error}
        </Callout>
      )}

      {/* Provenance first: it changes how everything below should be read. */}
      {!evidenceError && <ProvenanceSummary evidence={evidence ?? []} />}

      <section>
        <h5 className="text-tremor-content text-sm font-medium mb-1">
          Evidence
        </h5>
        {evidenceError ? (
          <p className="text-tremor-content text-sm">
            Evidence is unavailable.
          </p>
        ) : (
          <EvidenceList evidence={evidence ?? []} />
        )}
      </section>

      <section>
        <h5 className="text-tremor-content text-sm font-medium mb-1">
          Hypotheses
        </h5>
        {hypothesesError ? (
          <p className="text-tremor-content text-sm">
            Hypotheses are unavailable.
          </p>
        ) : (
          <HypothesisList hypotheses={hypotheses ?? []} />
        )}
      </section>

      {investigation.rca_draft && (
        <section>
          <h5 className="text-tremor-content text-sm font-medium mb-1">
            Root Cause Analysis (draft)
          </h5>
          <div className="prose prose-slate max-w-none text-sm [&>p]:!my-1 [&>ul]:!my-1 [&>ol]:!my-1">
            <MarkdownHTML>{investigation.rca_draft}</MarkdownHTML>
          </div>
        </section>
      )}

      {investigation.status === "rca_ready" && (
        <InvestigationFeedbackSection investigation={investigation} />
      )}
    </div>
  );
}

export function InvestigationPanel({ incidentId }: InvestigationPanelProps) {
  const { investigation, error, isLoading } =
    useInvestigationByIncident(incidentId);

  return (
    <Card className="mt-2 !p-4">
      {/* Open by default once there is something to show. Collapsed-by-default
          made the panel invisible in practice — it read as a bare heading. */}
      <Disclosure as="div" defaultOpen={!!investigation}>
        <Disclosure.Button
          className="flex w-full items-center justify-between gap-2"
          data-testid="investigation-panel-toggle"
        >
          {({ open }) => (
            <>
              <span className="flex items-center gap-2">
                <h4 className="text-tremor-content-strong font-medium">
                  AI Investigation
                </h4>
                {investigation && (
                  <InvestigationStatusBadge status={investigation.status} />
                )}
              </span>
              <IoChevronDown
                className={clsx({ "rotate-180": open }, "text-slate-400")}
              />
            </>
          )}
        </Disclosure.Button>

        <Disclosure.Panel as="div" className="relative">
          {error ? (
            <p className="text-tremor-content text-sm pt-3">
              Investigation data is unavailable.
            </p>
          ) : isLoading ? (
            <p className="text-tremor-content text-sm pt-3">
              Loading investigation…
            </p>
          ) : investigation ? (
            <>
              <InvestigationPanelBody investigation={investigation} />
              <a
                href="/aiops/investigations"
                className="text-xs text-orange-600 hover:underline mt-3 inline-block"
              >
                All investigations →
              </a>
            </>
          ) : (
            <p className="text-tremor-content text-sm pt-3">
              No investigation found for this incident. One is created
              automatically for critical and high-severity incidents.
            </p>
          )}
        </Disclosure.Panel>
      </Disclosure>
    </Card>
  );
}
