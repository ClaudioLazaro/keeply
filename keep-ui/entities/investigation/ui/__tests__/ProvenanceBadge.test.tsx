import React from "react";
import { render, screen } from "@testing-library/react";
import {
  ProvenanceSummary,
  provenanceOf,
  tallyProvenance,
} from "../ProvenanceBadge";
import { InvestigationEvidence } from "../../model/types";

function ev(
  id: string,
  backend?: InvestigationEvidence["backend"]
): InvestigationEvidence {
  return {
    id,
    investigation_id: "inv-1",
    tool: "get_pods",
    summary: "s",
    backend,
    created_at: "2026-08-01T00:00:00Z",
  };
}

describe("provenanceOf", () => {
  it("reads the backend field", () => {
    expect(provenanceOf(ev("e1", "live"))).toBe("live");
    expect(provenanceOf(ev("e1", "stub"))).toBe("stub");
  });

  it("treats a missing backend as unknown, never live", () => {
    expect(provenanceOf(ev("e1", undefined))).toBe("unknown");
  });
});

describe("tallyProvenance", () => {
  it("counts every bucket", () => {
    const counts = tallyProvenance([
      ev("1", "live"),
      ev("2", "stub"),
      ev("3", "stub"),
      ev("4", "gap"),
      ev("5", undefined),
    ]);
    expect(counts).toEqual({ live: 1, stub: 2, gap: 1, unknown: 1 });
  });
});

describe("ProvenanceSummary", () => {
  it("says nothing when all evidence is live", () => {
    const { container } = render(
      <ProvenanceSummary evidence={[ev("1", "live"), ev("2", "live")]} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("warns in red when nothing is live", () => {
    render(<ProvenanceSummary evidence={[ev("1", "stub"), ev("2", "stub")]} />);

    expect(screen.getByText("No live evidence")).toBeInTheDocument();
    expect(
      screen.getByText(/must not be used to make incident decisions/)
    ).toBeInTheDocument();
  });

  it("warns about a mixed set without the hard stop", () => {
    render(<ProvenanceSummary evidence={[ev("1", "live"), ev("2", "stub")]} />);

    expect(screen.getByText("Mixed evidence provenance")).toBeInTheDocument();
    expect(screen.queryByText("No live evidence")).not.toBeInTheDocument();
    expect(screen.getByText(/1 live · 1 stub/)).toBeInTheDocument();
  });

  it("renders nothing for an empty evidence list", () => {
    const { container } = render(<ProvenanceSummary evidence={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
