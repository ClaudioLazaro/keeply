export const investigationKeys = {
  all: "investigations",
  byIncident: (incidentId: string) =>
    [investigationKeys.all, "by-incident", incidentId]
      .filter(Boolean)
      .join("::"),
  evidence: (investigationId: string) =>
    [investigationKeys.all, "evidence", investigationId].join("::"),
  hypotheses: (investigationId: string) =>
    [investigationKeys.all, "hypotheses", investigationId].join("::"),
  getByIncidentMatcher: () => (key: unknown) =>
    typeof key === "string" &&
    key.startsWith([investigationKeys.all, "by-incident"].join("::")),
};
