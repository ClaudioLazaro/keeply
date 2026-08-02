"use client";

import Link from "next/link";
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
import { useAiopsTools, useIntegrations } from "@/entities/aiops/model/useAiops";
import { ProvenanceBadge } from "@/entities/investigation/ui/ProvenanceBadge";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  PolicyDecisionBadge,
} from "./shared";

/**
 * Which AIOps integration (and Keep provider) each tool belongs to.
 *
 * The catalog groups tools by their mode setting prefix (`dd_` -> datadog);
 * the integrations API tells which installed Keep provider supplies each
 * group. A tool with no provider behind it points to the install flow —
 * one management surface (Providers), one operational view (this page).
 */
const TOOL_PREFIX_TO_INTEGRATION: [RegExp, string][] = [
  [/^dd_/, "datadog"],
  [/^eks_/, "eks"],
  [/^rds_/, "rds"],
  [/^argocd_/, "argocd"],
  [/^jira_/, "jira"],
  [/^slack_/, "slack"],
  [/^bb_/, "bitbucket"],
  [/^backstage_/, "backstage"],
  [/^prom_/, "prometheus"],
];

function integrationForTool(toolName: string): string {
  for (const [pattern, name] of TOOL_PREFIX_TO_INTEGRATION) {
    if (pattern.test(toolName)) return name;
  }
  return "k8s";
}

export function ToolsClient() {
  const { catalog, isLoading, error } = useAiopsTools();
  const { integrations } = useIntegrations();

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

  const byName = new Map((integrations ?? []).map((item) => [item.name, item]));

  const mutating = catalog.tools.filter(
    (tool) => tool.execution_class !== "read"
  );
  const stubbed = catalog.tools.filter((tool) => tool.mode !== "live");
  const withoutProvider = new Set(
    catalog.tools
      .map((tool) => integrationForTool(tool.name))
      .filter((name) => {
        const integration = byName.get(name);
        return (
          integration && !integration.provider && !integration.ambient_credentials
        );
      })
  );

  return (
    <div>
      <PageHeader
        title="MCP Tools"
        description="Every tool the agents can reach, its data source, and the policy decision for each. Credentials are installed under Providers."
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
          with care.{" "}
          <Link href="/providers" className="underline">
            Install the providers you actually run
          </Link>{" "}
          to switch them to live.
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
                <TableHeaderCell>Provider</TableHeaderCell>
                <TableHeaderCell>Class</TableHeaderCell>
                <TableHeaderCell>Decision</TableHeaderCell>
                <TableHeaderCell>Policy</TableHeaderCell>
                <TableHeaderCell>Description</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {catalog.tools.map((tool) => {
                const integration = byName.get(integrationForTool(tool.name));
                return (
                  <TableRow key={tool.name}>
                    <TableCell className="font-mono text-xs">
                      {tool.name}
                    </TableCell>
                    <TableCell>
                      <ProvenanceBadge
                        value={tool.mode === "live" ? "live" : tool.mode === "stub" ? "stub" : "unknown"}
                      />
                    </TableCell>
                    <TableCell className="text-xs">
                      {integration?.provider ? (
                        <Link
                          href="/providers"
                          className="text-orange-600 hover:underline"
                        >
                          {integration.provider.display_name}
                        </Link>
                      ) : integration?.ambient_credentials ? (
                        <span className="text-tremor-content">ambient</span>
                      ) : integration ? (
                        <Link
                          href="/providers"
                          className="text-orange-600 hover:underline"
                        >
                          install {integration.provider_types[0]} →
                        </Link>
                      ) : (
                        "—"
                      )}
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
                );
              })}
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
