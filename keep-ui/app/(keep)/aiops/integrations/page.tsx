import { IntegrationsClient } from "@/features/aiops/ui/IntegrationsClient";

export default function Page() {
  return <IntegrationsClient />;
}

export const metadata = {
  title: "Keep - AIOps Integrations",
  description: "Configure where the AI agents read evidence from",
};
