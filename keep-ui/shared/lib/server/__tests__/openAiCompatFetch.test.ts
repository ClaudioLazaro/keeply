import { openAiCompatFetch, __testing } from "../openAiCompatFetch";

const { rewriteForCompatibility: rewriteRoles } = __testing;

/**
 * The failure this prevents is silent: the AI Summary button produced
 * nothing at all, because the provider rejected the request before any
 * token was generated.
 */
describe("rewriteRoles", () => {
  it("downgrades the developer role that non-OpenAI providers reject", () => {
    const body = JSON.stringify({
      messages: [
        { role: "developer", content: "you are an assistant" },
        { role: "user", content: "summarise" },
      ],
    });
    const out = JSON.parse(rewriteRoles(body));
    expect(out.messages[0].role).toBe("system");
    expect(out.messages[1].role).toBe("user");
  });

  it("leaves a body without the role byte-identical", () => {
    const body = JSON.stringify({ messages: [{ role: "user", content: "hi" }] });
    expect(rewriteRoles(body)).toBe(body);
  });

  it("forwards unparseable bodies untouched rather than corrupting them", () => {
    expect(rewriteRoles("not json")).toBe("not json");
  });

  it("ignores payloads that are not chat completions", () => {
    const body = JSON.stringify({ input: "embed me" });
    expect(rewriteRoles(body)).toBe(body);
  });
});

describe("tool_choice compatibility", () => {
  it("downgrades a compelled tool call to an offered one", () => {
    // DeepSeek reasoning models answer 400 "Thinking mode does not support
    // this tool_choice" for `required` and for a forced function. The whole
    // request failed on load, so the workflow builder chat never opened.
    for (const choice of ["required", { type: "function", function: { name: "x" } }]) {
      const out = JSON.parse(
        rewriteRoles(JSON.stringify({ messages: [], tool_choice: choice }))
      );
      expect(out.tool_choice).toBe("auto");
    }
  });

  it("leaves the values the provider accepts alone", () => {
    for (const choice of ["auto", "none"]) {
      const body = JSON.stringify({ messages: [], tool_choice: choice });
      expect(rewriteRoles(body)).toBe(body);
    }
  });

  it("does not invent a tool_choice where the caller sent none", () => {
    const body = JSON.stringify({ messages: [{ role: "user", content: "hi" }] });
    expect(rewriteRoles(body)).toBe(body);
  });
});

describe("openAiCompatFetch", () => {
  it("rewrites the body of an outgoing POST", async () => {
    const seen: string[] = [];
    const base = jest.fn(async (_url: unknown, init?: RequestInit) => {
      seen.push(String(init?.body));
      return {} as Response;
    }) as unknown as typeof fetch;

    await openAiCompatFetch(base)("https://api.deepseek.com/chat/completions", {
      method: "POST",
      body: JSON.stringify({ messages: [{ role: "developer", content: "x" }] }),
    });
    expect(JSON.parse(seen[0]).messages[0].role).toBe("system");
  });

  it("corrects Content-Length after shortening the body", async () => {
    // "developer" -> "system" shrinks the body, and the OpenAI SDK sets
    // Content-Length explicitly. A stale value aborts the request with
    // UND_ERR_REQ_CONTENT_LENGTH_MISMATCH — the same silent failure, one
    // layer deeper.
    let sent: RequestInit | undefined;
    const base = jest.fn(async (_u: unknown, init?: RequestInit) => {
      sent = init;
      return {} as Response;
    }) as unknown as typeof fetch;

    const original = JSON.stringify({
      messages: [{ role: "developer", content: "x" }],
    });
    await openAiCompatFetch(base)("https://api.deepseek.com/chat/completions", {
      method: "POST",
      body: original,
      headers: { "content-length": String(original.length) },
    });

    const headers = new Headers(sent!.headers);
    expect(headers.get("content-length")).toBe(
      String(new TextEncoder().encode(String(sent!.body)).length)
    );
    expect(Number(headers.get("content-length"))).toBeLessThan(original.length);
  });

  it("counts bytes, not characters, for non-ASCII content", async () => {
    let sent: RequestInit | undefined;
    const base = jest.fn(async (_u: unknown, init?: RequestInit) => {
      sent = init;
      return {} as Response;
    }) as unknown as typeof fetch;

    const body = JSON.stringify({
      messages: [{ role: "developer", content: "acentuação — ç" }],
    });
    await openAiCompatFetch(base)("https://api.deepseek.com/chat/completions", {
      method: "POST",
      body,
      headers: { "content-length": String(body.length) },
    });

    const declared = Number(new Headers(sent!.headers).get("content-length"));
    expect(declared).toBe(new TextEncoder().encode(String(sent!.body)).length);
    expect(declared).toBeGreaterThan(String(sent!.body).length);
  });

  it("leaves headers untouched when nothing was rewritten", async () => {
    let sent: RequestInit | undefined;
    const base = jest.fn(async (_u: unknown, init?: RequestInit) => {
      sent = init;
      return {} as Response;
    }) as unknown as typeof fetch;

    const body = JSON.stringify({ messages: [{ role: "user", content: "hi" }] });
    await openAiCompatFetch(base)("https://api.deepseek.com/chat/completions", {
      method: "POST",
      body,
      headers: { "content-length": "999" },
    });
    expect(new Headers(sent!.headers).get("content-length")).toBe("999");
  });

  it("passes non-POST requests straight through", async () => {
    const base = jest.fn(async () => ({}) as Response) as unknown as typeof fetch;
    await openAiCompatFetch(base)("https://api.deepseek.com/models", { method: "GET" });
    expect(base).toHaveBeenCalled();
  });
});

describe("reasoning_content on replayed tool calls", () => {
  const toolCall = {
    id: "c1",
    type: "function",
    function: { name: "createStep", arguments: "{}" },
  };

  it("supplies the field the API demands when a tool call is replayed", () => {
    // The turn that errored was never the one that called the tool — it was
    // the one after, replaying it.
    const out = JSON.parse(
      rewriteRoles(
        JSON.stringify({
          messages: [
            { role: "user", content: "add a step" },
            { role: "assistant", content: null, tool_calls: [toolCall] },
            { role: "tool", tool_call_id: "c1", content: "ok" },
          ],
        })
      )
    );
    expect(out.messages[1].reasoning_content).toBe("");
  });

  it("sends an empty string, never invented reasoning", () => {
    const out = JSON.parse(
      rewriteRoles(
        JSON.stringify({
          messages: [{ role: "assistant", content: null, tool_calls: [toolCall] }],
        })
      )
    );
    // Anything else would be fabricated model reasoning fed back as if real.
    expect(out.messages[0].reasoning_content).toBe("");
  });

  it("keeps real reasoning when the client did retain it", () => {
    const out = JSON.parse(
      rewriteRoles(
        JSON.stringify({
          messages: [
            {
              role: "assistant",
              content: null,
              reasoning_content: "the workflow needs a step",
              tool_calls: [toolCall],
            },
          ],
        })
      )
    );
    expect(out.messages[0].reasoning_content).toBe("the workflow needs a step");
  });

  it("leaves plain assistant messages alone", () => {
    // Probing showed these replay fine, so touching them would be an
    // unnecessary edit to a payload that already works.
    const body = JSON.stringify({
      messages: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "hello" },
      ],
    });
    expect(rewriteRoles(body)).toBe(body);
  });

  it("leaves an assistant message with an empty tool_calls array alone", () => {
    const body = JSON.stringify({
      messages: [{ role: "assistant", content: "hello", tool_calls: [] }],
    });
    expect(rewriteRoles(body)).toBe(body);
  });

  it("does not add the field to tool or user messages", () => {
    const out = JSON.parse(
      rewriteRoles(
        JSON.stringify({
          messages: [
            { role: "user", content: "hi", tool_calls: [toolCall] },
            { role: "tool", tool_call_id: "c1", content: "ok" },
          ],
        })
      )
    );
    expect(out.messages[0]).not.toHaveProperty("reasoning_content");
    expect(out.messages[1]).not.toHaveProperty("reasoning_content");
  });
});
