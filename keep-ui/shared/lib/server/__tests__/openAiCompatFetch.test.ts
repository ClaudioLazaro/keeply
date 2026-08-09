import { openAiCompatFetch, __testing } from "../openAiCompatFetch";

const { rewriteRoles } = __testing;

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
