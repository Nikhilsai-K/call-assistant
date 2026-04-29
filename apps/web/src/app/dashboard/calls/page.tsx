"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Call = {
  id: string;
  direction: string;
  from_e164: string | null;
  to_e164: string | null;
  started_at: string;
  duration_s: number | null;
  outcome: string | null;
  cost_cents: number | null;
};

export default function CallsPage() {
  const { data, isLoading } = useQuery<Call[]>({
    queryKey: ["calls"],
    queryFn: () => api<Call[]>("/v1/calls?limit=50"),
    refetchInterval: 5_000,
  });

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Calls</h1>
      {isLoading && <p className="text-muted-foreground">Loading…</p>}
      <div className="rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left">
            <tr>
              <th className="p-3">When</th>
              <th className="p-3">Direction</th>
              <th className="p-3">From</th>
              <th className="p-3">To</th>
              <th className="p-3">Outcome</th>
              <th className="p-3">Duration</th>
              <th className="p-3">Cost</th>
              <th className="p-3"></th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((c) => (
              <tr key={c.id} className="border-t">
                <td className="p-3">{new Date(c.started_at).toLocaleString()}</td>
                <td className="p-3">{c.direction}</td>
                <td className="p-3">{c.from_e164 ?? "—"}</td>
                <td className="p-3">{c.to_e164 ?? "—"}</td>
                <td className="p-3">{c.outcome ?? "—"}</td>
                <td className="p-3">{c.duration_s ? `${c.duration_s}s` : "—"}</td>
                <td className="p-3">
                  {c.cost_cents != null ? `$${(c.cost_cents / 100).toFixed(2)}` : "—"}
                </td>
                <td className="p-3">
                  <Link
                    href={`/dashboard/calls/${c.id}`}
                    className="text-primary hover:underline"
                  >
                    View
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
