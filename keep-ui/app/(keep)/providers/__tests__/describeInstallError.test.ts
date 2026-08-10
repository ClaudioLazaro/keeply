import { describeInstallError } from "../install-errors";

/**
 * A Datadog install was retried because the failure looked like a rejected
 * credential. It was not: the scopes had all validated and only the name
 * was taken. The heading was doing the misleading.
 */
describe("describeInstallError", () => {
  it("names a duplicate name as such, not as a connection problem", () => {
    const described = describeInstallError(409, "Acerto");

    expect(described?.title).toBe("Name already in use");
    expect(described?.title).not.toMatch(/connection/i);
  });

  it("says the credentials were fine, so the operator does not re-enter them", () => {
    // This sentence is the whole point: validation runs before the insert,
    // so a 409 means everything worked except the name.
    expect(describeInstallError(409, "Acerto")?.message).toMatch(
      /credentials were accepted/
    );
  });

  it("names the provider that is in the way", () => {
    expect(describeInstallError(409, "Acerto")?.message).toContain("Acerto");
  });

  it("offers both ways out — rename, or edit what exists", () => {
    const message = describeInstallError(409, "Acerto")?.message ?? "";

    expect(message).toMatch(/different name/);
    expect(message).toMatch(/existing provider/);
  });

  it("stays silent about statuses it has nothing specific to say about", () => {
    // Falling through to the generic handler is correct; inventing a
    // confident heading for an unknown failure is not.
    expect(describeInstallError(500, "Acerto")).toBeNull();
    expect(describeInstallError(undefined, "Acerto")).toBeNull();
  });
});
