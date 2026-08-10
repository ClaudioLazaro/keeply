/**
 * How a provider install failure should be described to the operator.
 *
 * Its own module, with no imports, so it can be tested without loading the
 * form and everything the form draws in.
 */

/** Heading for a failure we have nothing more specific to say about. */
export const DEFAULT_ERROR_TITLE = "Connection Problem";

/**
 * The heading carries as much meaning as the text.
 *
 * A duplicate name reported under "Connection Problem" reads as a rejected
 * credential, and an operator who believes the credential failed retries
 * the whole install — including one that already succeeded, since scope
 * validation runs before the insert and a 409 means everything worked
 * except the name.
 *
 * Returns null for anything it cannot describe precisely. Inventing a
 * confident heading for an unknown failure would be the same mistake in
 * the other direction.
 */
export function describeInstallError(
  status: number | undefined,
  providerName: string
): { title: string; message: string } | null {
  if (status === 409) {
    return {
      title: "Name already in use",
      message:
        `A provider named "${providerName}" is already installed. Its ` +
        "credentials were accepted — only the name is taken. Choose a " +
        "different name, or open the existing provider to edit it.",
    };
  }
  return null;
}
