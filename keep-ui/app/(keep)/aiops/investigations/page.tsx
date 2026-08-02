import { InvestigationsClient } from "@/features/aiops/ui/InvestigationsClient";

export default function Page() {
  return <InvestigationsClient />;
}

export const metadata = {
  title: "Keep - AI Investigations",
  description: "Every AI investigation and its status",
};
