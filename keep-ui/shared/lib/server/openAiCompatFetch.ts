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

/**
 * `required` and a forced `{type:"function"}` compel a tool call. DeepSeek's
 * reasoning models reject both outright:
 *
 *     400 Thinking mode does not support this tool_choice
 *
 * `auto` and `none` are accepted. So the request is downgraded from compelling
 * to *asking* — the model may still call the tool, and usually does, but is no
 * longer forced to.
 *
 * That is a real behavioural difference, not a transparent shim: a flow that
 * depends on a guaranteed tool call (CopilotKit's suggestions force one) can
 * come back empty instead of erroring. Empty is the better failure — the
 * builder chat stays usable, where before the whole request 400'd on load.
 */
function downgradeToolChoice(value: unknown): "auto" | null {
  if (value === "required") return "auto";
  if (value && typeof value === "object") return "auto";
  return null;
}

/**
 * In thinking mode an assistant message that carries `tool_calls` must also
 * carry `reasoning_content` when it is replayed. Omit it and the *next* turn
 * fails, not the current one:
 *
 *     400 The `reasoning_content` in the thinking mode must be passed back
 *
 * CopilotKit stores assistant messages by role, content and tool calls; the
 * reasoning stream has nowhere to live, so it is gone by the time the history
 * is replayed. Probed to find the exact rule rather than guessing at it:
 * plain assistant text replays fine, `""` is accepted, `null` is not, and only
 * messages with `tool_calls` are checked at all.
 *
 * So the empty string is what gets sent. Not a plausible-looking placeholder:
 * inventing reasoning the model never produced would put fabricated text into
 * its own context, which is the same lie as showing a stub payload as live
 * telemetry — smaller, and further from anyone's eyes, but the same kind.
 */
function repairReasoningContent(message: {
  tool_calls?: unknown;
  reasoning_content?: unknown;
}): boolean {
  if (!Array.isArray(message.tool_calls) || message.tool_calls.length === 0) {
    return false;
  }
  if (typeof message.reasoning_content === "string") return false;
  message.reasoning_content = "";
  return true;
}

function rewriteForCompatibility(body: string): string {
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
  const payload = parsed as {
    messages: Array<{
      role?: unknown;
      tool_calls?: unknown;
      reasoning_content?: unknown;
    }>;
    tool_choice?: unknown;
  };
  let changed = false;
  for (const message of payload.messages) {
    if (!message) continue;
    if (message.role === OPENAI_ONLY_ROLE) {
      message.role = UNIVERSAL_ROLE;
      changed = true;
    }
    if (message.role === "assistant" && repairReasoningContent(message)) {
      changed = true;
    }
  }
  if ("tool_choice" in payload) {
    const downgraded = downgradeToolChoice(payload.tool_choice);
    if (downgraded) {
      payload.tool_choice = downgraded;
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
    const body = rewriteForCompatibility(init.body);
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

export const __testing = { rewriteForCompatibility };
