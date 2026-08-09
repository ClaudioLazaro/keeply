/**
 * Compatibility shim for OpenAI-compatible providers that are not OpenAI.
 *
 * CopilotKit's `OpenAIAdapter` rewrites every `system` message to the
 * `developer` role — the variant OpenAI introduced for its reasoning models.
 * The conversion is unconditional and the adapter exposes no way to opt out
 * (its `GroqAdapter` sibling passes `keepSystemRole: true`; the OpenAI one
 * hardcodes false).
 *
 * Providers that merely speak the OpenAI wire format have no such role.
 * DeepSeek answers:
 *
 *     400 Failed to deserialize the JSON body into the target type:
 *     messages[0].role: unknown variant `developer`, expected one of
 *     `system`, `user`, `assistant`, `tool`
 *
 * which surfaces in the product as an AI Summary button that does nothing.
 *
 * Applied only when a custom `baseURL` is configured — i.e. when the caller
 * is deliberately pointing at something other than api.openai.com. Against
 * real OpenAI the rewrite is skipped, because there `developer` is correct
 * and some reasoning models reject `system`.
 */

const OPENAI_ONLY_ROLE = "developer";
const UNIVERSAL_ROLE = "system";

function rewriteRoles(body: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    // Not JSON we understand — forward untouched rather than corrupt it.
    return body;
  }
  if (
    !parsed ||
    typeof parsed !== "object" ||
    !Array.isArray((parsed as { messages?: unknown }).messages)
  ) {
    return body;
  }
  const payload = parsed as { messages: Array<{ role?: unknown }> };
  let changed = false;
  for (const message of payload.messages) {
    if (message?.role === OPENAI_ONLY_ROLE) {
      message.role = UNIVERSAL_ROLE;
      changed = true;
    }
  }
  return changed ? JSON.stringify(payload) : body;
}

/**
 * A `fetch` for the OpenAI SDK that downgrades the `developer` role.
 *
 * Everything else is passed through untouched — this is a translation layer,
 * not a proxy, and it must stay boring.
 */
export function openAiCompatFetch(
  baseFetch: typeof fetch = fetch
): typeof fetch {
  return async (input, init) => {
    if (init?.method?.toUpperCase() !== "POST" || typeof init.body !== "string") {
      return baseFetch(input, init);
    }
    const body = rewriteRoles(init.body);
    if (body === init.body) return baseFetch(input, init);

    // The rewrite shortens the body ("developer" -> "system"), and the OpenAI
    // SDK sets Content-Length explicitly. Leaving the old value stranded the
    // request with UND_ERR_REQ_CONTENT_LENGTH_MISMATCH — the button called the
    // endpoint and still produced nothing, just one layer further in.
    // Byte length, not character count: content may be non-ASCII.
    const headers = new Headers(init.headers as HeadersInit | undefined);
    headers.set("content-length", String(new TextEncoder().encode(body).length));
    return baseFetch(input, { ...init, body, headers });
  };
}

export const __testing = { rewriteRoles };
