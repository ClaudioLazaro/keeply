"use client";

import { useState } from "react";
import { Badge, Button, Callout, Card, Text, Title } from "@tremor/react";
import { SparklesIcon } from "@heroicons/react/24/outline";
import {
  useRuleSuggestions,
  useRuleSuggestionActions,
} from "@/entities/aiops/model/useRuleSuggestions";

/**
 * Correlation rules proposed by the AIOps analysis, shown alongside the
 * rules an operator wrote.
 *
 * They live here rather than in a separate AIOps page because the decision
 * they need — should this rule exist? — belongs next to the rules that
 * already do. Accepting one creates an ordinary Keep rule; from that point
 * the engine owns it and this section forgets about it.
 */
function SuggestionCard({
  suggestion,
  onAccept,
  onDismiss,
  busy,
}: {
  suggestion: ReturnType<typeof useRuleSuggestions>["suggestions"] extends
    | (infer T)[]
    | undefined
    ? T
    : never;
  onAccept: () => void;
  onDismiss: () => void;
  busy: boolean;
}) {
  return (
    <Card className="mb-2 !p-4">
      <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
        <div>
          <div className="flex items-center gap-2">
            <Text className="font-medium">{suggestion.name}</Text>
            <Badge color="orange" size="xs">
              seen {suggestion.occurrences}×
            </Badge>
            <Badge color="gray" size="xs">
              {suggestion.alerts_covered} alerts
            </Badge>
          </div>
          <code className="text-xs block mt-1">{suggestion.cel}</code>
        </div>
        <div className="flex items-center gap-2">
          <Button size="xs" onClick={onAccept} loading={busy} disabled={busy}>
            Create rule
          </Button>
          <Button size="xs" variant="light" onClick={onDismiss} disabled={busy}>
            Dismiss
          </Button>
        </div>
      </div>

      <Text className="text-xs">{suggestion.rationale}</Text>
      <Text className="text-xs mt-1">
        Groups by <code>{suggestion.grouping_criteria.join(", ") || "—"}</code>{" "}
        over {Math.round(suggestion.timeframe_seconds / 60)} min.
      </Text>
    </Card>
  );
}

export function SuggestedRules() {
  const { suggestions, error } = useRuleSuggestions("pending");
  const { accept, dismiss, pendingId } = useRuleSuggestionActions();
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(
    null
  );

  // No control plane, or nothing proposed: this section simply is not there.
  // The rules page must work identically without AIOps installed.
  if (error || !suggestions || suggestions.length === 0) {
    return null;
  }

  async function handle(id: string, action: "accept" | "dismiss") {
    const result = action === "accept" ? await accept(id) : await dismiss(id);
    setFeedback(
      result.ok
        ? {
            ok: true,
            message:
              action === "accept"
                ? "Rule created. It needs approval before its incidents count."
                : "Suggestion dismissed.",
          }
        : { ok: false, message: result.error ?? "Something went wrong." }
    );
  }

  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-1">
        <SparklesIcon className="size-5 text-orange-500" />
        <Title>Suggested correlations</Title>
      </div>
      <Text className="mb-3">
        Patterns that kept recurring in your alert history. Creating a rule
        hands it to Keep&apos;s correlation engine — it starts gated behind
        approval, so its first incidents are candidates you confirm.
      </Text>

      {feedback && (
        <Callout
          title={feedback.ok ? "Done" : "Failed"}
          color={feedback.ok ? "emerald" : "red"}
          className="mb-3"
        >
          {feedback.message}
        </Callout>
      )}

      {suggestions.map((suggestion) => (
        <SuggestionCard
          key={suggestion.id}
          suggestion={suggestion}
          busy={pendingId === suggestion.id}
          onAccept={() => handle(suggestion.id, "accept")}
          onDismiss={() => handle(suggestion.id, "dismiss")}
        />
      ))}
    </div>
  );
}
