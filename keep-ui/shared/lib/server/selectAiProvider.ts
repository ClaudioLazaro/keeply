/**
 * Choosing which installed provider an AI feature uses.
 *
 * Its own module, with no imports, for the same reason the model-name rule
 * is: `getAiProvider` reaches for next-auth, and a decision this important
 * should not need an auth stack to be tested. It previously had no test at
 * all, which is how it shipped picking whichever provider the API happened
 * to return first.
 */

export interface InstalledProvider {
  id?: string;
  type?: string;
  details?: { authentication?: Record<string, string> };
}

export interface ProviderPreference {
  /** Provider type, e.g. `deepseek`. */
  provider?: string;
  /** A specific installation, when several of one type exist. */
  providerId?: string;
}

/** The usable credential on an installed provider, or "" if it has none. */
export function credentialOf(provider: InstalledProvider | undefined): string {
  const auth = provider?.details?.authentication ?? {};
  return auth.api_key || auth.token || auth.access_token || "";
}

/**
 * The provider to use, most specific preference first.
 *
 * Two rules earn their place here:
 *
 * **Only providers that can authenticate are candidates.** Choosing one and
 * *then* noticing it has no credential used to return null and lose the
 * assistant entirely, with a working provider sitting next in the list.
 * That is not hypothetical — a row whose secret file had gone missing was
 * found in production doing exactly this.
 *
 * **The id beats the type.** Two installations of the same vendor is a real
 * configuration: a cheap key and an expensive one both appear as
 * `deepseek`, and a type alone cannot tell them apart.
 */
export function selectAiProvider(
  installed: InstalledProvider[],
  aiTypes: readonly string[],
  preference: ProviderPreference = {}
): InstalledProvider | undefined {
  const candidates = installed.filter(
    (item) => item?.type && aiTypes.includes(item.type) && credentialOf(item)
  );
  return (
    (preference.providerId &&
      candidates.find((item) => item?.id === preference.providerId)) ||
    (preference.provider &&
      candidates.find((item) => item?.type === preference.provider)) ||
    candidates[0]
  );
}
