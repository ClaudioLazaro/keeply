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

/** The compatibility downgrades this shim knows how to apply. */
export type Downgrade = "developer_role" | "tool_choice" | "reasoning_content";

export const ALL_DOWNGRADES: Downgrade[] = [
  "developer_role",
  "tool_choice",
  "reasoning_content",
];

/**
 * Downgrades that cost nothing, and so need no evidence to justify.
 *
 * `developer` -> `system` is a pure translation: the two mean the same
 * thing, and `developer` exists only on OpenAI's own reasoning models. This
 * shim runs only when a custom baseURL says we are not talking to OpenAI,
 * so the rewrite is always right and applying it up front saves every first
 * request a guaranteed round trip into a 400.
 *
 * The other two are not free and are therefore not here. `tool_choice`
 * genuinely weakens the request — compelling a tool call becomes merely
 * offering one — so it is applied only against a provider that has actually
 * refused. `reasoning_content` adds a field the provider never asked for,
 * which a non-thinking model has every right to reject.
 *
 * The distinction is the point: a workaround with a cost must be paid for
 * with evidence, never applied speculatively.
 */
const LOSSLESS: Downgrade[] = ["developer_role"];

/**
 * Which provider refusal means which downgrade.
 *
 * Matched on the provider's own words. Deliberately narrow: a broad match
 * would swallow an unrelated 400 and "fix" it by silently weakening the
 * request, which is a worse outcome than the error the operator can see.
 * An unrecognised 400 is returned untouched, on purpose.
 */
const REFUSAL_SIGNATURES: Array<{ downgrade: Downgrade; test: RegExp }> = [
  { downgrade: "developer_role", test: /unknown variant `?developer`?/i },
  { downgrade: "tool_choice", test: /does not support this tool_choice/i },
  {
    downgrade: "reasoning_content",
    test: /reasoning_content.*must be passed back/i,
  },
];

/** Which downgrade, if any, a provider error is asking for. */
export function downgradeForError(message: string): Downgrade | null {
  for (const { downgrade, test } of REFUSAL_SIGNATURES) {
    if (test.test(message)) return downgrade;
  }
  return null;
}

function rewriteForCompatibility(
  body: string,
  apply: ReadonlySet<Downgrade>
): string {
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
    if (apply.has("developer_role") && message.role === OPENAI_ONLY_ROLE) {
      message.role = UNIVERSAL_ROLE;
      changed = true;
    }
    if (
      apply.has("reasoning_content") &&
      message.role === "assistant" &&
      repairReasoningContent(message)
    ) {
      changed = true;
    }
  }
  if (apply.has("tool_choice") && "tool_choice" in payload) {
    const downgraded = downgradeToolChoice(payload.tool_choice);
    if (downgraded) {
      payload.tool_choice = downgraded;
      changed = true;
    }
  }
  return changed ? JSON.stringify(payload) : body;
}

export interface CompatOptions {
  /**
   * Downgrades already known to be needed for this model. Applied on the
   * first attempt so a model whose constraints were learned last week does
   * not have to fail again to prove it.
   */
  known?: readonly Downgrade[];
  /**
   * `off` sends the strong form and never adapts; `on` applies everything
   * up front; `auto` (the default) starts from `known` and discovers the
   * rest from real refusals.
   */
  thinking?: "auto" | "on" | "off";
  /** Called when a refusal taught us something new, so it can be persisted. */
  onLearned?: (downgrades: Downgrade[], evidence: string) => void;
}

/** Re-encode a body and fix the SDK's explicit Content-Length. */
function withBody(init: RequestInit, body: string): RequestInit {
  // The rewrite changes the body's length in both directions ("developer"
  // -> "system" shortens it; adding reasoning_content lengthens it) and the
  // OpenAI SDK sets Content-Length explicitly. Leaving the old value
  // stranded the request with UND_ERR_REQ_CONTENT_LENGTH_MISMATCH — the
  // button called the endpoint and still produced nothing, one layer
  // deeper. Byte length, not character count: content may be non-ASCII.
  const headers = new Headers(init.headers as HeadersInit | undefined);
  headers.set("content-length", String(new TextEncoder().encode(body).length));
  return { ...init, body, headers };
}

/**
 * A `fetch` for the OpenAI SDK that adapts to what a provider will accept.
 *
 * Three refusals were needed to get one reasoning model talking, each found
 * in production after the previous fix shipped. Hardcoding the resulting
 * workarounds would fix exactly that model; vendors add reasoning modes to
 * existing model names, so the next refusal is a matter of time.
 *
 * So the strong form is tried first, and a *recognised* refusal — matched on
 * the provider's own words — is answered by applying that one downgrade and
 * retrying once. An unrecognised 400 is returned untouched: silently
 * weakening a request to make an unexplained error go away is how a system
 * starts lying about what it did.
 */
export function openAiCompatFetch(
  baseFetch: typeof fetch = fetch,
  options: CompatOptions = {}
): typeof fetch {
  const thinking = options.thinking ?? "auto";

  return async (input, init) => {
    if (init?.method?.toUpperCase() !== "POST" || typeof init.body !== "string") {
      return baseFetch(input, init);
    }

    const original = init.body;
    const applied = new Set<Downgrade>(
      thinking === "on"
        ? ALL_DOWNGRADES
        : thinking === "off"
          ? // `off` still translates the role: that is not a thinking-mode
            // workaround, it is the OpenAI-only role this provider does not
            // have. Turning thinking off should not break the request.
            LOSSLESS
          : [...LOSSLESS, ...(options.known ?? [])]
    );

    let attempt = rewriteForCompatibility(original, applied);
    let response = await baseFetch(
      input,
      attempt === original ? init : withBody(init, attempt)
    );

    if (thinking !== "auto" || response.ok) return response;

    // Retry only while each refusal teaches something new. Bounded by the
    // number of known downgrades, so a provider that keeps refusing for a
    // reason we do not understand ends the loop instead of hammering it.
    const learned: Downgrade[] = [];
    let evidence = "";

    for (let round = 0; round < ALL_DOWNGRADES.length; round++) {
      if (response.status !== 400) break;
      const text = await response.clone().text();
      const needed = downgradeForError(text);
      if (!needed || applied.has(needed)) break;

      applied.add(needed);
      learned.push(needed);
      evidence = text.slice(0, 1000);

      attempt = rewriteForCompatibility(original, applied);
      response = await baseFetch(input, withBody(init, attempt));
      if (response.ok) break;
    }

    if (learned.length > 0) {
      // Reported whether or not the retry ultimately succeeded: the refusal
      // is a fact about the model either way, and the next request should
      // not have to rediscover it.
      options.onLearned?.(learned, evidence);
    }
    return response;
  };
}

export const __testing = { rewriteForCompatibility, ALL_DOWNGRADES };
