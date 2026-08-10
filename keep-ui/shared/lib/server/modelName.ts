/**
 * Model-name translation between the AI plane and the providers.
 *
 * Kept in its own module with no imports: `getAiProvider` reaches for
 * next-auth, and a pure string function should not need an auth stack to
 * be testable.
 */

/**
 * Drop a LiteLLM-style `provider/` prefix from a model name.
 *
 * The two sides of this boundary name models differently and both are
 * right. The AI plane routes through LiteLLM, which needs the provider
 * prefix to know where to send the call (`deepseek/deepseek-v4-flash`).
 * The frontend talks to the provider's own endpoint through the OpenAI
 * SDK, where the host already answers that question and the prefix is
 * simply not a model:
 *
 *     400 The supported API model names are deepseek-v4-pro or
 *     deepseek-v4-flash, but you passed deepseek/deepseek-v4-flash
 *
 * Stripped only when the prefix is exactly this provider's own type.
 * Model names legitimately contain slashes — an OpenRouter deployment
 * routes to `meta-llama/llama-3` — and cutting at the first slash would
 * quietly rewrite those into something that does not exist.
 */
export function stripProviderPrefix(
  model: string | undefined,
  providerType: string | undefined
): string | undefined {
  if (!model || !providerType) return model;
  const prefix = `${providerType}/`;
  return model.toLowerCase().startsWith(prefix.toLowerCase())
    ? model.slice(prefix.length)
    : model;
}
