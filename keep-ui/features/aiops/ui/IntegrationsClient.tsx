"use client";

import Link from "next/link";
import {
  Badge,
  Button,
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
import { useIntegrations } from "@/entities/aiops/model/useAiops";
import { Integration } from "@/entities/aiops/model/types";
import { ProvenanceBadge } from "@/entities/investigation/ui/ProvenanceBadge";
import { ErrorState, LoadingState, PageHeader } from "./shared";

/**
 * Read-only view of which Keep provider feeds which AI specialist.
 *
 * Credentials are installed and rotated under Providers — Keep's own
 * system of record. Offering a second form here would mean two secret
 * stores and two rotation paths for the same backend.
 */
function IntegrationRow({ integration }: { integration: Integration }) {
  const isLive = integration.mode === "live";

  return (
    <TableRow>
      <TableCell>
        <div className="flex items-center gap-2">
          <Text className="font-medium">{integration.label}</Text>
          <ProvenanceBadge value={isLive ? "live" : "stub"} />
        </div>
      </TableCell>
      <TableCell>
        {integration.provider ? (
          <Link
            href="/providers"
            className="text-xs text-orange-600 hover:underline"
          >
            {integration.provider.display_name}
          </Link>
        ) : integration.ambient_credentials ? (
          <Text className="text-xs">ambient credentials</Text>
        ) : (
          <Link href="/providers" className="text-xs text-orange-600 hover:underline">
            install {integration.provider_types.join(" or ")} →
          </Link>
        )}
      </TableCell>
      <TableCell className="text-xs font-mono">
        {integration.tools.join(", ")}
      </TableCell>
      <TableCell className="text-xs">{integration.notes}</TableCell>
    </TableRow>
  );
}

export function IntegrationsClient() {
  const { integrations, isLoading, error } = useIntegrations();

  if (error) return <ErrorState what="integrations" />;
  if (isLoading || !integrations) return <LoadingState what="integrations" />;

  const stubbed = integrations.filter((item) => item.mode !== "live");
  const installable = stubbed.filter(
    (item) => !item.provider && !item.ambient_credentials
  );

  return (
    <div>
      <PageHeader
        title="Integrations"
        description="Where the agents read evidence from. Each one is backed by a Keep provider — install it under Providers and the matching specialist goes live."
      />

      {stubbed.length > 0 && (
        <Callout title="Demo data in play" color="amber" className="mb-3">
          {stubbed.length} of {integrations.length} integrations are on stub and
          return canned payloads. Evidence they produce is labelled, and
          hypotheses resting only on it are marked unverified — but an analysis
          built on demo data cannot support a real decision.
          {installable.length > 0 && (
            <span className="block mt-2">
              <Link href="/providers" className="underline">
                Install the providers you actually run
              </Link>{" "}
              to switch them over.
            </span>
          )}
        </Callout>
      )}

      <Card className="!p-0 overflow-x-auto">
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Integration</TableHeaderCell>
              <TableHeaderCell>Keep provider</TableHeaderCell>
              <TableHeaderCell>Tools</TableHeaderCell>
              <TableHeaderCell>Notes</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {integrations.map((integration) => (
              <IntegrationRow key={integration.name} integration={integration} />
            ))}
          </TableBody>
        </Table>
      </Card>

      <div className="flex items-center gap-2 mt-3">
        <Link href="/providers">
          <Button size="xs" variant="secondary">
            Go to Providers
          </Button>
        </Link>
        <Text className="text-xs">
          Credentials are managed there, with the rest of your integrations.
          Changes reach the agents within ~30s.
        </Text>
      </div>
    </div>
  );
}
