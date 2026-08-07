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

  it("passes non-POST requests straight through", async () => {
    const base = jest.fn(async () => ({}) as Response) as unknown as typeof fetch;
    await openAiCompatFetch(base)("https://api.deepseek.com/models", { method: "GET" });
    expect(base).toHaveBeenCalled();
  });
});
