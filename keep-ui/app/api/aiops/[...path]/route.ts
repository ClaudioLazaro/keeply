import { NextRequest, NextResponse } from "next/server";

// Server-side proxy to the aiops-api service. The API key is read from server
// env only and is never exposed to the browser — client code calls
// /api/aiops/<path> and this handler attaches the credentials upstream.
//
// The client path INCLUDES the `/v1/` prefix (the keep-ui client code
// uses paths like `/api/aiops/v1/investigations`). We forward the path
// as-is to the upstream.
const AIOPS_API_URL = process.env.AIOPS_API_URL || "http://localhost:8081";
const AIOPS_API_KEY = process.env.AIOPS_API_KEY || "dev-key";

function buildUpstreamUrl(path: string[]): URL {
  return new URL(
    `${AIOPS_API_URL.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`
  );
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  const upstreamUrl = buildUpstreamUrl(path);
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

async function forwardWithBody(
  request: NextRequest,
  path: string[],
  method: "POST" | "PUT"
) {
  const upstreamUrl = buildUpstreamUrl(path);

  try {
    const upstreamResponse = await fetch(upstreamUrl.toString(), {
      method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-API-KEY": AIOPS_API_KEY,
      },
      body: await request.text(),
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

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return forwardWithBody(request, path, "POST");
}

// PUT is required by the agent-config API (PUT /v1/config).
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return forwardWithBody(request, path, "PUT");
}
