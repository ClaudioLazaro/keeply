import {
  DETAIL_MAX_CHARS,
  evidenceArguments,
  evidenceDetail,
  targetOf,
} from "../evidenceDetail";
import { InvestigationEvidence } from "../../model/types";

/**
 * The operator and the model must see the same evidence.
 *
 * These mirror `tests/test_evidence_detail.py` in keep-aiops. If the two
 * renderers drift, an operator reviewing a hypothesis is checking it against
 * different evidence than the one that produced it — which is worse than
 * showing nothing, because it looks like verification.
 */

function evidence(
  overrides: Partial<InvestigationEvidence> = {}
): InvestigationEvidence {
  return {
    id: "e1",
    investigation_id: "i1",
    tool: "get_events",
    summary: "get_events: 3 events returned",
    backend: "live",
    created_at: "2026-08-06T12:00:00Z",
    ...overrides,
  };
}

const EVENTS = evidence({
  payload: {
    arguments: { cluster: "prod-eu", namespace: "payments" },
    result: {
      backend: "live",
      cluster: "prod-eu",
      events: [
        { reason: "OOMKilled", message: "container exceeded memory" },
        { reason: "BackOff", message: "back-off restarting" },
      ],
    },
  },
});

describe("evidenceDetail", () => {
  it("surfaces the finding, not just its count", () => {
    const detail = evidenceDetail(EVENTS);
    expect(detail).toContain("OOMKilled");
    expect(detail).toContain("back-off restarting");
  });

  it("omits fields that are rendered as their own badge", () => {
    const detail = evidenceDetail(EVENTS);
    expect(detail).not.toContain("backend");
    expect(detail).not.toContain("cluster");
  });

  it("marks truncation so a slice is not mistaken for the whole", () => {
    const pods = Array.from({ length: 40 }, (_, i) => ({ name: `p-${i}` }));
    const detail = evidenceDetail(evidence({ payload: { result: { pods } } }));
    expect(detail).toContain("more)");
    expect(detail.length).toBeLessThanOrEqual(DETAIL_MAX_CHARS);
  });

  it("says why a call failed", () => {
    const detail = evidenceDetail(
      evidence({
        backend: "gap",
        payload: { error: "403 Forbidden: cannot get resource pods/log" },
      })
    );
    expect(detail).toContain("cannot get resource pods/log");
  });

  it("renders nothing when the API returned no payload", () => {
    expect(evidenceDetail(evidence())).toBe("");
  });
});

describe("targetOf", () => {
  it("exposes which target answered, so provenance is checkable", () => {
    expect(targetOf(EVENTS)).toBe("prod-eu");
  });

  it("returns null rather than guessing when the result names none", () => {
    expect(targetOf(evidence({ payload: { result: { pods: [] } } }))).toBeNull();
    expect(targetOf(evidence())).toBeNull();
  });
});

describe("evidenceArguments", () => {
  it("shows what the tool was asked", () => {
    expect(evidenceArguments(EVENTS)).toBe(
      "cluster=prod-eu namespace=payments"
    );
  });

  it("is empty when nothing was passed", () => {
    expect(evidenceArguments(evidence({ payload: { arguments: {} } }))).toBe("");
  });
});
