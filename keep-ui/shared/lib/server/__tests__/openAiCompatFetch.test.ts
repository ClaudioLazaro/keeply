import { openAiCompatFetch, __testing } from "../openAiCompatFetch";

const rewriteRoles = (body: string) =>
  __testing.rewriteForCompatibility(body, new Set(__testing.ALL_DOWNGRADES));

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


describe("adapting to what a provider actually refuses", () => {
  const TOOL_CHOICE_400 =
    '{"error":{"message":"Thinking mode does not support this tool_choice"}}';
  const REASONING_400 =
    '{"error":{"message":"The `reasoning_content` in the thinking mode must be passed back to the API."}}';

  function post(body: unknown) {
    return {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "content-length": "1" },
    } as RequestInit;
  }

  const withToolChoice = {
    messages: [{ role: "user", content: "hi" }],
    tool_choice: "required",
  };


  /**
   * The shim reads exactly three things off a response: `ok`, `status`, and
   * the body via `clone().text()`. jsdom has no fetch primitives, and
   * pulling undici in breaks under the jsdom transform — a stub of only
   * what is used keeps the test honest about the surface it depends on.
   */
  class FakeResponse {
    constructor(
      readonly body: string,
      readonly status: number
    ) {}
    get ok() {
      return this.status >= 200 && this.status < 300;
    }
    clone() {
      return this;
    }
    async text() {
      return this.body;
    }
  }

  function respond(status: number, body: string) {
    return new FakeResponse(body, status) as unknown as Response;
  }

  function stubFetch(responses: Array<{ status: number; body: string }>) {
    const seen: string[] = [];
    const fetchMock = jest.fn(async (_url: unknown, init: RequestInit) => {
      seen.push(String(init.body));
      const next = responses.shift() ?? { status: 200, body: "{}" };
      return respond(next.status, next.body);
    });
    return { fetchMock: fetchMock as unknown as typeof fetch, seen };
  }

  it("sends the strong form first and does not weaken a working request", async () => {
    // The downgrade is a real loss of behaviour, so it must not be paid by
    // a provider that never asked for it.
    const { fetchMock, seen } = stubFetch([{ status: 200, body: "{}" }]);
    await openAiCompatFetch(fetchMock)("/v1/chat", post(withToolChoice));

    expect(seen).toHaveLength(1);
    expect(JSON.parse(seen[0]).tool_choice).toBe("required");
  });

  it("downgrades and retries when the provider names the reason", async () => {
    const { fetchMock, seen } = stubFetch([
      { status: 400, body: TOOL_CHOICE_400 },
      { status: 200, body: "{}" },
    ]);
    const response = await openAiCompatFetch(fetchMock)("/v1/chat", post(withToolChoice));

    expect(response.status).toBe(200);
    expect(JSON.parse(seen[0]).tool_choice).toBe("required");
    expect(JSON.parse(seen[1]).tool_choice).toBe("auto");
  });

  it("reports what it learned, with the provider's own words as the cause", async () => {
    const learned: Array<[string[], string]> = [];
    const { fetchMock } = stubFetch([
      { status: 400, body: TOOL_CHOICE_400 },
      { status: 200, body: "{}" },
    ]);
    await openAiCompatFetch(fetchMock, {
      onLearned: (d, evidence) => learned.push([d, evidence]),
    })("/v1/chat", post(withToolChoice));

    expect(learned[0][0]).toEqual(["tool_choice"]);
    expect(learned[0][1]).toContain("Thinking mode");
  });

  it("does not touch a 400 it does not recognise", async () => {
    // Silently weakening a request to make an unexplained error disappear
    // is how a system starts lying about what it did.
    const { fetchMock, seen } = stubFetch([
      { status: 400, body: '{"error":{"message":"context length exceeded"}}' },
    ]);
    const response = await openAiCompatFetch(fetchMock)("/v1/chat", post(withToolChoice));

    expect(seen).toHaveLength(1);
    expect(response.status).toBe(400);
  });

  it("learns one refusal at a time, exactly as this was found in production", async () => {
    const { fetchMock, seen } = stubFetch([
      { status: 400, body: TOOL_CHOICE_400 },
      { status: 400, body: REASONING_400 },
      { status: 200, body: "{}" },
    ]);
    const learned: string[][] = [];
    const response = await openAiCompatFetch(fetchMock, {
      onLearned: (d) => learned.push(d),
    })(
      "/v1/chat",
      post({
        messages: [
          { role: "assistant", content: null, tool_calls: [{ id: "c1" }] },
        ],
        tool_choice: "required",
      })
    );

    expect(response.status).toBe(200);
    expect(seen).toHaveLength(3);
    expect(learned[0]).toEqual(["tool_choice", "reasoning_content"]);
  });

  it("stops instead of looping when the same refusal repeats", async () => {
    const { fetchMock, seen } = stubFetch([
      { status: 400, body: TOOL_CHOICE_400 },
      { status: 400, body: TOOL_CHOICE_400 },
      { status: 400, body: TOOL_CHOICE_400 },
    ]);
    const response = await openAiCompatFetch(fetchMock)("/v1/chat", post(withToolChoice));

    expect(seen).toHaveLength(2); // the try, and one retry
    expect(response.status).toBe(400);
  });

  it("starts from what was already learned rather than failing again", async () => {
    const { fetchMock, seen } = stubFetch([{ status: 200, body: "{}" }]);
    await openAiCompatFetch(fetchMock, { known: ["tool_choice"] })(
      "/v1/chat",
      post(withToolChoice)
    );

    expect(seen).toHaveLength(1);
    expect(JSON.parse(seen[0]).tool_choice).toBe("auto");
  });

  it("thinking:off sends the strong form and never adapts", async () => {
    // The escape hatch for an operator who knows their model is fine.
    const { fetchMock, seen } = stubFetch([
      { status: 400, body: TOOL_CHOICE_400 },
    ]);
    const response = await openAiCompatFetch(fetchMock, { thinking: "off" })(
      "/v1/chat",
      post(withToolChoice)
    );

    expect(seen).toHaveLength(1);
    expect(JSON.parse(seen[0]).tool_choice).toBe("required");
    expect(response.status).toBe(400);
  });

  it("thinking:on applies everything up front, without a failed round trip", async () => {
    const { fetchMock, seen } = stubFetch([{ status: 200, body: "{}" }]);
    await openAiCompatFetch(fetchMock, { thinking: "on" })(
      "/v1/chat",
      post({
        messages: [
          { role: "developer", content: "sys" },
          { role: "assistant", content: null, tool_calls: [{ id: "c1" }] },
        ],
        tool_choice: "required",
      })
    );

    const sent = JSON.parse(seen[0]);
    expect(seen).toHaveLength(1);
    expect(sent.tool_choice).toBe("auto");
    expect(sent.messages[0].role).toBe("system");
    expect(sent.messages[1].reasoning_content).toBe("");
  });

  it("corrects Content-Length when the retry body grows", async () => {
    // Adding reasoning_content lengthens the body; a stale header stalls
    // the request with UND_ERR_REQ_CONTENT_LENGTH_MISMATCH.
    const sentLengths: number[] = [];
    let call = 0;
    const fetchWithRetry = (async (_u: unknown, init: RequestInit) => {
      sentLengths.push(
        Number(new Headers(init.headers as HeadersInit).get("content-length"))
      );
      call += 1;
      return respond(call === 1 ? 400 : 200, call === 1 ? REASONING_400 : "{}");
    }) as unknown as typeof fetch;

    await openAiCompatFetch(fetchWithRetry)(
      "/v1/chat",
      post({
        messages: [
          { role: "assistant", content: null, tool_calls: [{ id: "c1" }] },
        ],
      })
    );

    expect(sentLengths[1]).toBeGreaterThan(sentLengths[0]);
  });
});


describe("a workaround with a cost is paid for with evidence", () => {
  function seenBodies(status = 200) {
    const seen: string[] = [];
    const fetchMock = (async (_u: unknown, init: RequestInit) => {
      seen.push(String(init.body));
      return {
        ok: status < 300,
        status,
        clone: () => ({ text: async () => "{}" }),
      } as unknown as Response;
    }) as unknown as typeof fetch;
    return { fetchMock, seen };
  }

  const payload = {
    messages: [
      { role: "developer", content: "sys" },
      { role: "assistant", content: null, tool_calls: [{ id: "c1" }] },
    ],
    tool_choice: "required",
  };

  function post(body: unknown) {
    return { method: "POST", body: JSON.stringify(body) } as RequestInit;
  }

  it("translates the role up front, because that costs nothing", async () => {
    // `developer` exists only on OpenAI's own models and this shim runs
    // only when we are pointed elsewhere. Waiting for a 400 to learn it
    // would burn a guaranteed round trip on every first request.
    const { fetchMock, seen } = seenBodies();
    await openAiCompatFetch(fetchMock)("/v1/chat", post(payload));

    expect(JSON.parse(seen[0]).messages[0].role).toBe("system");
  });

  it("does not weaken tool_choice without a refusal", async () => {
    const { fetchMock, seen } = seenBodies();
    await openAiCompatFetch(fetchMock)("/v1/chat", post(payload));

    expect(JSON.parse(seen[0]).tool_choice).toBe("required");
  });

  it("does not add reasoning_content without a refusal", async () => {
    // A non-thinking model has every right to reject a field it never
    // asked for.
    const { fetchMock, seen } = seenBodies();
    await openAiCompatFetch(fetchMock)("/v1/chat", post(payload));

    expect(JSON.parse(seen[0]).messages[1]).not.toHaveProperty("reasoning_content");
  });

  it("still translates the role when thinking is switched off", async () => {
    // Turning off a thinking-mode workaround must not break a request that
    // was never about thinking mode.
    const { fetchMock, seen } = seenBodies();
    await openAiCompatFetch(fetchMock, { thinking: "off" })("/v1/chat", post(payload));

    expect(JSON.parse(seen[0]).messages[0].role).toBe("system");
    expect(JSON.parse(seen[0]).tool_choice).toBe("required");
  });
});
