/**
 * @jest-environment node
 */
import { NextRequest } from "next/server";
import { GET } from "../route";

describe("aiops proxy route", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("forwards GET to the aiops-api with path, query and API key header", async () => {
    const mockFetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: "inv-1" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    global.fetch = mockFetch;

    const response = await GET(
      new NextRequest("http://localhost/api/aiops/investigations?incident_id=inc-1"),
      { params: Promise.resolve({ path: ["investigations"] }) }
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([{ id: "inv-1" }]);

    const [url, init] = mockFetch.mock.calls[0];
    // defaults: AIOPS_API_URL=http://localhost:8081, AIOPS_API_KEY=dev-key
    expect(url).toBe(
      "http://localhost:8081/v1/investigations?incident_id=inc-1"
    );
    expect(init.headers["X-API-KEY"]).toBe("dev-key");
  });

  it("preserves the upstream status code", async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "investigation not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      })
    );

    const response = await GET(
      new NextRequest("http://localhost/api/aiops/investigations/inv-x/evidence"),
      { params: Promise.resolve({ path: ["investigations", "inv-x", "evidence"] }) }
    );

    expect(response.status).toBe(404);
    const [url] = (global.fetch as jest.Mock).mock.calls[0];
    expect(url).toBe("http://localhost:8081/v1/investigations/inv-x/evidence");
  });

  it("returns 502 when the upstream is unreachable", async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error("ECONNREFUSED"));

    const response = await GET(
      new NextRequest("http://localhost/api/aiops/investigations"),
      { params: Promise.resolve({ path: ["investigations"] }) }
    );

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      detail: "aiops-api is unreachable",
    });
  });
});
