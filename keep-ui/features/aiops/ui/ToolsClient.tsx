"use client";

import {
  Badge,
  Callout,
  Card,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  Text,
} from "@tremor/react";
import { useAiopsTools } from "@/entities/aiops/model/useAiops";
import { ProvenanceBadge } from "@/entities/investigation/ui/ProvenanceBadge";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  PolicyDecisionBadge,
} from "./shared";

export function ToolsClient() {
  const { catalog, isLoading, error } = useAiopsTools();

  if (error) return <ErrorState what="the MCP tool catalog" />;
  if (isLoading || !catalog) return <LoadingState what="the MCP tool catalog" />;

  if (!catalog.gateway_available) {
    return (
      <div>
        <PageHeader
          title="MCP Tools"
          description="Every tool the agents can reach, and the policy decision for each."
        />
        <Callout title="MCP gateway unreachable" color="red">
          {catalog.error ?? "The gateway did not answer."}
          <span className="block mt-1 text-xs">
            Gateway URL: <code>{catalog.gateway_url}</code>
          </span>
        </Callout>
      </div>
    );
  }

  const mutating = catalog.tools.filter(
    (tool) => tool.execution_class !== "read"
  );
  const stubbed = catalog.tools.filter((tool) => tool.mode !== "live");

  return (
    <div>
      <PageHeader
        title="MCP Tools"
        description="Every tool the agents can reach, and the policy decision for each. The gateway is the only path from an agent to a tool."
      />

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <Badge color="gray" size="xs">
          {catalog.tools.length} registered
        </Badge>
        <Badge color={mutating.length === 0 ? "emerald" : "red"} size="xs">
          {mutating.length === 0
            ? "all read-class"
            : `${mutating.length} non-read`}
        </Badge>
        <Badge color={stubbed.length === 0 ? "emerald" : "amber"} size="xs">
          {stubbed.length === 0
            ? "all live"
            : `${stubbed.length} on stub`}
        </Badge>
        <Text className="text-xs">
          gateway: <code>{catalog.gateway_url}</code>
        </Text>
      </div>

      {stubbed.length > 0 && (
        <Callout title="Some tools return demo data" color="amber" className="mb-3">
          {stubbed.length} of {catalog.tools.length} tools are in stub mode:
          they return canned payloads, not data from your environment.
          Evidence they produce is labelled, and hypotheses resting only on
          it are marked unverified — but treat any analysis involving them
          with care. Configure the matching credentials on the gateway to
          switch a tool to live.
        </Callout>
      )}

      {catalog.tools.length === 0 ? (
        <EmptyState message="The gateway answered with an empty catalog." />
      ) : (
        <Card className="!p-0 overflow-x-auto">
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Tool</TableHeaderCell>
                <TableHeaderCell>Data</TableHeaderCell>
                <TableHeaderCell>Class</TableHeaderCell>
                <TableHeaderCell>Decision</TableHeaderCell>
                <TableHeaderCell>Policy</TableHeaderCell>
                <TableHeaderCell>Description</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {catalog.tools.map((tool) => (
                <TableRow key={tool.name}>
                  <TableCell className="font-mono text-xs">
                    {tool.name}
                  </TableCell>
                  <TableCell>
                    <ProvenanceBadge
                      value={tool.mode === "live" ? "live" : tool.mode === "stub" ? "stub" : "unknown"}
                    />
                  </TableCell>
                  <TableCell>
                    <Badge
                      color={
                        tool.execution_class === "read" ? "emerald" : "red"
                      }
                      size="xs"
                    >
                      {tool.execution_class}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <PolicyDecisionBadge decision={tool.decision} />
                  </TableCell>
                  <TableCell className="text-xs font-mono">
                    {/* null policy_id = the fail-closed default decided. */}
                    {tool.policy_id ?? (
                      <span className="text-tremor-content">
                        fail-closed default
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">{tool.description}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <p className="text-xs text-tremor-content mt-3">
        This view is read-only. Tools cannot be invoked from the console —
        only an investigation can call them, and every call is policy-checked
        and audited at the gateway.
      </p>
    </div>
  );
}
