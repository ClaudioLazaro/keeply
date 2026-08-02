"use client";

import { IoIosGitNetwork } from "react-icons/io";
import { Workflows } from "components/icons";
import { useParams, usePathname } from "next/navigation";
import { TabLinkNavigation, TabNavigationLink } from "@/shared/ui";
import { BellAlertIcon, BoltIcon } from "@heroicons/react/24/outline";
import { CiViewTimeline } from "react-icons/ci";
import { RiRobot2Line } from "react-icons/ri";
import { Badge } from "@tremor/react";
import { IncidentDto } from "@/entities/incidents/model";
import { useIncident, useIncidentAlerts } from "@/utils/hooks/useIncidents";
import { useInvestigationByIncident } from "@/entities/investigation/model/useInvestigation";
import { INVESTIGATION_STATUS_BADGE } from "@/entities/investigation/ui/InvestigationStatusBadge";

export const tabs = [
  { icon: BellAlertIcon, label: "Alerts", path: "alerts" },
  { icon: BoltIcon, label: "Activity", path: "activity", prefetch: true },
  { icon: CiViewTimeline, label: "Timeline", path: "timeline" },
  {
    icon: IoIosGitNetwork,
    label: "Topology",
    path: "topology",
  },
  { icon: Workflows, label: "Workflows", path: "workflows" },
  { icon: RiRobot2Line, label: "AI Investigation", path: "investigation" },
];

export function IncidentTabsNavigation() {
  // Using type assertion because this component only renders on the /incidents/[id] routes
  const { id } = useParams<{ id: string }>() as { id: string };
  const pathname = usePathname();
  const { data: alerts } = useIncidentAlerts(id);
  const { investigation } = useInvestigationByIncident(id);

  return (
    <TabLinkNavigation className="sticky xl:-top-10 -top-4 bg-tremor-background-muted">
      {tabs.map((tab) => (
        <TabNavigationLink
          key={tab.path}
          icon={tab.icon}
          isActive={pathname?.endsWith(tab.path)}
          href={`/incidents/${id}/${tab.path}`}
          prefetch={!!tab.prefetch}
          count={tab.path === "alerts" ? alerts?.count : undefined}
        >
          {tab.label}
          {/* Investigation status on the tab itself: whether an RCA is
              ready is the reason to open it. */}
          {tab.path === "investigation" && investigation && (
            <Badge
              className="ml-2"
              color={
                INVESTIGATION_STATUS_BADGE[investigation.status]?.color ?? "gray"
              }
              size="xs"
            >
              {INVESTIGATION_STATUS_BADGE[investigation.status]?.label ??
                investigation.status}
            </Badge>
          )}
        </TabNavigationLink>
      ))}
    </TabLinkNavigation>
  );
}
