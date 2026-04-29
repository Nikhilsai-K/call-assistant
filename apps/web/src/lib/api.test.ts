import { describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("api client", () => {
  it("throws on non-2xx status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("bad", { status: 500, statusText: "Internal Server Error" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(api("/v1/agents")).rejects.toThrow(/500/);
  });

  it("sends X-Dev-Org header", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await api("/v1/health", {}, "org-xyz");
    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>)["X-Dev-Org"]).toBe("org-xyz");
  });
});
