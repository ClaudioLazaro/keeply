import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InvestigationPanel } from "../InvestigationPanel";
import {
  INVESTIGATION_POLL_INTERVAL_MS,
  isInvestigationInFlight,
} from "@/entities/investigation/model/useInvestigation";
import {
  Investigation,
  InvestigationEvidence,
  InvestigationFeedback,
  InvestigationHypothesis,
} from "@/entities/investigation/model/types";

const mockUseSWR = jest.fn();
const mockMutate = jest.fn();

jest.mock("swr", () => ({
  __esModule: true,
  default: (key: string | null, fetcher: unknown, config: unknown) =>
    mockUseSWR(key, fetcher, config),
  useSWRConfig: () => ({ mutate: mockMutate }),
}));

const mockShowSuccessToast = jest.fn();
const mockShowErrorToast = jest.fn();

jest.mock("@/shared/ui", () => ({
  showSuccessToast: (...args: unknown[]) => mockShowSuccessToast(...args),
  showErrorToast: (...args: unknown[]) => mockShowErrorToast(...args),
}));

// react-markdown is ESM-only and not transformed by jest; the rendering of
// markdown itself is covered by the shared component's own tests.
jest.mock("@/shared/ui/MarkdownHTML/MarkdownHTML", () => ({
  MarkdownHTML: ({ children }: { children: string }) => (
    <div data-testid="rca-markdown">{children}</div>
  ),
}));

const INCIDENT_ID = "incident-1";

const rcaReadyInvestigation: Investigation = {
  id: "inv-1",
  tenant_id: "tenant-1",
  incident_id: INCIDENT_ID,
  status: "rca_ready",
  rca_draft: "## Root cause\nDatabase connection pool exhausted [E1]",
  error: null,
  created_at: "2026-07-29T10:00:00Z",
};

const gatheringInvestigation: Investigation = {
  ...rcaReadyInvestigation,
  status: "gathering",
  rca_draft: null,
};

const failedInvestigation: Investigation = {
  ...rcaReadyInvestigation,
  status: "failed",
  rca_draft: null,
  error: "mcp gateway unreachable",
};

const evidenceItems: InvestigationEvidence[] = [
  {
    id: "ev-1",
    investigation_id: "inv-1",
    tool: "k8s_get_pods",
    summary: "payment-api pods are CrashLoopBackOff",
    created_at: "2026-07-29T10:00:10Z",
  },
  {
    id: "ev-2",
    investigation_id: "inv-1",
    tool: "prom_alerts",
    summary: "HighErrorRate firing for payment-api",
    created_at: "2026-07-29T10:00:20Z",
  },
];

const hypothesisItems: InvestigationHypothesis[] = [
  {
    id: "hyp-1",
    investigation_id: "inv-1",
    title: "Database connection pool exhausted",
    confidence: 0.8,
    supporting_evidence: ["E1"],
    supporting_knowledge: ["K1"],
    created_at: "2026-07-29T10:01:00Z",
  },
];

const usefulFeedback: InvestigationFeedback = {
  id: "fb-1",
  investigation_id: "inv-1",
  tenant_id: "tenant-1",
  rating: "useful",
  comment: null,
  created_at: "2026-07-29T10:05:00Z",
  updated_at: "2026-07-29T10:05:00Z",
};

let byIncidentData: Investigation[] = [];
let byIncidentError: unknown = undefined;
let feedbackData: InvestigationFeedback | undefined = undefined;

function swrResultForKey(key: string | null) {
  if (!key) {
    return { data: undefined, error: undefined, isLoading: false };
  }
  if (key.includes("::by-incident::")) {
    return { data: byIncidentData, error: byIncidentError, isLoading: false };
  }
  if (key.includes("::evidence::")) {
    return { data: evidenceItems, error: undefined, isLoading: false };
  }
  if (key.includes("::hypotheses::")) {
    return { data: hypothesisItems, error: undefined, isLoading: false };
  }
  if (key.includes("::feedback::")) {
    return { data: feedbackData, error: undefined, isLoading: false };
  }
  throw new Error(`Unexpected SWR key: ${key}`);
}

function swrCallsFor(fragment: string) {
  return mockUseSWR.mock.calls.filter(
    ([key]) => typeof key === "string" && key.includes(fragment)
  );
}

/**
 * Make sure the panel body is visible.
 *
 * The panel opens by default whenever an investigation exists, so a blind
 * click would COLLAPSE it. Only toggle when it is actually closed —
 * headlessui reflects that in aria-expanded.
 */
function expandPanel() {
  const toggle = screen.getByTestId("investigation-panel-toggle");
  if (toggle.getAttribute("aria-expanded") !== "true") {
    fireEvent.click(toggle);
  }
}

describe("InvestigationPanel", () => {
  beforeEach(() => {
    mockUseSWR.mockReset();
    mockUseSWR.mockImplementation(swrResultForKey);
    byIncidentData = [rcaReadyInvestigation];
    byIncidentError = undefined;
    feedbackData = undefined;
  });

  it("is open by default when an investigation exists", () => {
    // Collapsed-by-default made the panel read as a bare heading and users
    // never found it. It now opens as soon as there is something to show.
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);

    expect(
      screen.getByText("payment-api pods are CrashLoopBackOff")
    ).toBeInTheDocument();
  });

  it("can still be collapsed", () => {
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);

    fireEvent.click(screen.getByTestId("investigation-panel-toggle"));

    expect(
      screen.queryByText("payment-api pods are CrashLoopBackOff")
    ).not.toBeInTheDocument();
  });

  it("starts collapsed when there is no investigation to show", () => {
    byIncidentData = [];
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);

    expect(
      screen.getByTestId("investigation-panel-toggle")
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("renders status, evidence, hypotheses and the RCA draft", () => {
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    expect(screen.getByText("RCA ready")).toBeInTheDocument();
    expect(screen.getByText("k8s_get_pods")).toBeInTheDocument();
    expect(screen.getByText("prom_alerts")).toBeInTheDocument();
    expect(
      screen.getByText("HighErrorRate firing for payment-api")
    ).toBeInTheDocument();
    expect(
      screen.getByText("Database connection pool exhausted")
    ).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByTestId("rca-markdown")).toHaveTextContent(
      "Database connection pool exhausted [E1]"
    );
  });

  it("shows the empty state when no investigation exists", () => {
    byIncidentData = [];
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    expect(
      screen.getByText(/No investigation found for this incident\./)
    ).toBeInTheDocument();
  });

  it("shows an error state when the investigation fetch fails", () => {
    byIncidentData = undefined as unknown as Investigation[];
    byIncidentError = new Error("aiops request failed with status 502");
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    expect(
      screen.getByText("Investigation data is unavailable.")
    ).toBeInTheDocument();
  });

  it("shows the failure callout when the investigation failed", () => {
    byIncidentData = [failedInvestigation];
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("mcp gateway unreachable")).toBeInTheDocument();
  });

  it("sets a polling interval while the investigation is in flight", () => {
    byIncidentData = [gatheringInvestigation];
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    for (const [, , config] of swrCallsFor("::evidence::")) {
      expect(config?.refreshInterval).toBe(INVESTIGATION_POLL_INTERVAL_MS);
    }
    for (const [, , config] of swrCallsFor("::hypotheses::")) {
      expect(config?.refreshInterval).toBe(INVESTIGATION_POLL_INTERVAL_MS);
    }

    // The investigation list itself polls via a functional refreshInterval.
    const [byIncidentCall] = swrCallsFor("::by-incident::");
    const refreshInterval = byIncidentCall[2]?.refreshInterval;
    expect(typeof refreshInterval).toBe("function");
    expect(refreshInterval([gatheringInvestigation])).toBe(
      INVESTIGATION_POLL_INTERVAL_MS
    );
    expect(refreshInterval([rcaReadyInvestigation])).toBe(0);
  });

  it("does not poll once the investigation is terminal", () => {
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    for (const [, , config] of swrCallsFor("::evidence::")) {
      expect(config?.refreshInterval).toBe(0);
    }
    for (const [, , config] of swrCallsFor("::hypotheses::")) {
      expect(config?.refreshInterval).toBe(0);
    }
  });
});

describe("InvestigationPanel feedback", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    mockUseSWR.mockReset();
    mockUseSWR.mockImplementation(swrResultForKey);
    mockMutate.mockReset();
    mockShowSuccessToast.mockReset();
    mockShowErrorToast.mockReset();
    byIncidentData = [rcaReadyInvestigation];
    byIncidentError = undefined;
    feedbackData = undefined;
    global.fetch = jest.fn();
  });

  afterAll(() => {
    global.fetch = originalFetch;
  });

  it("renders the rating buttons when the investigation is rca_ready", () => {
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    expect(
      screen.getByText("Was this investigation useful?")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Useful" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Not useful" })
    ).toBeInTheDocument();
  });

  it("posts the feedback when a rating button is clicked", async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => usefulFeedback,
    });
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    fireEvent.click(screen.getByRole("button", { name: "Useful" }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/aiops/investigations/inv-1/feedback",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ rating: "useful", comment: null }),
      })
    );
    await waitFor(() =>
      expect(mockMutate).toHaveBeenCalledWith(
        "investigations::feedback::inv-1",
        usefulFeedback,
        { revalidate: false }
      )
    );
    expect(mockShowSuccessToast).toHaveBeenCalled();
  });

  it("reflects the existing feedback as the selected rating", () => {
    feedbackData = usefulFeedback;
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    expect(screen.getByRole("button", { name: "Useful" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(
      screen.getByRole("button", { name: "Not useful" })
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("does not render the feedback section while the investigation is in flight", () => {
    byIncidentData = [gatheringInvestigation];
    render(<InvestigationPanel incidentId={INCIDENT_ID} />);
    expandPanel();

    expect(
      screen.queryByText("Was this investigation useful?")
    ).not.toBeInTheDocument();
  });
});

describe("isInvestigationInFlight", () => {
  it("is true for in-flight statuses only", () => {
    expect(isInvestigationInFlight(gatheringInvestigation)).toBe(true);
    expect(isInvestigationInFlight(rcaReadyInvestigation)).toBe(false);
    expect(isInvestigationInFlight(failedInvestigation)).toBe(false);
    expect(isInvestigationInFlight(undefined)).toBe(false);
  });
});
