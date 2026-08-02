import { getIncidentName } from "@/entities/incidents/lib/utils";
import { getIncidentWithErrorHandling } from "../getIncidentWithErrorHandling";
import { InvestigationPanel } from "@/features/investigation/ui/InvestigationPanel";

type PageProps = {
  params: Promise<{ id: string }>;
};

export default async function IncidentInvestigationPage(props: PageProps) {
  const { id } = await props.params;
  // Resolved server-side so a missing incident 404s here rather than
  // rendering an empty panel, matching the other incident tabs.
  await getIncidentWithErrorHandling(id);
  return <InvestigationPanel incidentId={id} />;
}

export async function generateMetadata(props: PageProps) {
  const params = await props.params;
  const incident = await getIncidentWithErrorHandling(params.id);
  const incidentName = getIncidentName(incident);
  return {
    title: `Keep — ${incidentName} — AI Investigation`,
    description: incident.user_summary || incident.generated_summary,
  };
}
