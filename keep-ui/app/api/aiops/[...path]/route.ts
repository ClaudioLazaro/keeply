import { NextRequest, NextResponse } from "next/server";

// Server-side proxy to the aiops-api service. The API key is read from server
// env only and is never exposed to the browser — client code calls
// /api/aiops/<path> and this handler attaches the credentials upstream.
const AIOPS_API_URL = process.env.AIOPS_API_URL || "http://localhost:8081";
const AIOPS_API_KEY = process.env.AIOPS_API_KEY || "dev-key";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;

  const upstreamUrl = new URL(
    `${AIOPS_API_URL.replace(/\/$/, "")}/v1/${path.map(encodeURIComponent).join("/")}`
  );
  upstreamUrl.search = request.nextUrl.search;

  try {
    const upstreamResponse = await fetch(upstreamUrl.toString(), {
      method: "GET",
      headers: {
        Accept: "application/json",
        "X-API-KEY": AIOPS_API_KEY,
      },
      cache: "no-store",
    });

    const body = await upstreamResponse.text();
    return new NextResponse(body, {
      status: upstreamResponse.status,
      headers: {
        "Content-Type":
          upstreamResponse.headers.get("Content-Type") ?? "application/json",
      },
    });
  } catch (error) {
    console.error("aiops proxy: upstream request failed", error);
    return NextResponse.json(
      { detail: "aiops-api is unreachable" },
      { status: 502 }
    );
  }
}
