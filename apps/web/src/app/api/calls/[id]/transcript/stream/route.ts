/**
 * Server-side proxy for the live transcript SSE.
 * EventSource can't send custom headers, so the dashboard hits this Next.js
 * route which forwards a Bearer token (Clerk) to the FastAPI SSE endpoint.
 */
import { auth } from "@clerk/nextjs/server";
import { NextRequest } from "next/server";

const API_BASE = process.env.API_BASE_URL ?? "http://api:8000";

export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const { getToken } = await auth();
  const token = await getToken({ template: "default" }).catch(() => null);

  const headers: Record<string, string> = {
    Accept: "text/event-stream",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  } else if (process.env.NEXT_PUBLIC_DEV_ORG) {
    headers["X-Dev-Org"] = process.env.NEXT_PUBLIC_DEV_ORG;
  }

  const upstream = await fetch(`${API_BASE}/v1/calls/${id}/transcript/stream`, {
    headers,
    cache: "no-store",
    signal: req.signal,
  });

  if (!upstream.ok || !upstream.body) {
    return new Response("upstream error", { status: upstream.status });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
