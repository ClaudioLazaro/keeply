"use client";

import { useRules } from "utils/hooks/useRules";
import { CorrelationPlaceholder } from "./CorrelationPlaceholder";
import { CorrelationTable } from "./CorrelationTable";
import Loading from "@/app/(keep)/loading";
import { SuggestedRules } from "./SuggestedRules";

export const Client = () => {
  const { data: rules = [], isLoading } = useRules();

  if (isLoading) {
    return <Loading />;
  }

  // Suggestions render above both states: with no rules yet, a proposal is
  // exactly what an operator needs to see.
  return (
    <>
      <SuggestedRules />
      {rules.length === 0 ? (
        <CorrelationPlaceholder />
      ) : (
        <CorrelationTable rules={rules} />
      )}
    </>
  );
};
