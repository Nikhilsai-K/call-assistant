"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type CallAnalytics = {
  total: number;
  booked: number;
  abandoned: number;
  transferred: number;
  avg_duration_s: number | null;
  p95_quality: number | null;
};

type CostAnalytics = {
  total_cents: number;
  stt_cents: number;
  llm_cents: number;
  tts_cents: number;
  twilio_cents: number;
  per_call_cents_avg: number | null;
};

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border p-4">
      <div className="text-xs uppercase text-muted-foreground tracking-wide">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}

export default function DashboardOverview() {
  const { data: calls } = useQuery<CallAnalytics>({
    queryKey: ["analytics", "calls"],
    queryFn: () => api<CallAnalytics>("/v1/analytics/calls"),
  });
  const { data: cost } = useQuery<CostAnalytics>({
    queryKey: ["analytics", "cost"],
    queryFn: () => api<CostAnalytics>("/v1/analytics/cost"),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Overview</h1>
        <p className="text-muted-foreground">Real-time operations and cost.</p>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Calls" value={calls?.total ?? "—"} />
        <Stat label="Booked" value={calls?.booked ?? "—"} />
        <Stat label="Transferred" value={calls?.transferred ?? "—"} />
        <Stat
          label="Avg duration"
          value={calls?.avg_duration_s ? `${Math.round(calls.avg_duration_s)}s` : "—"}
        />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Stat
          label="Total COGS"
          value={cost ? `$${(cost.total_cents / 100).toFixed(2)}` : "—"}
        />
        <Stat label="STT" value={cost ? `$${(cost.stt_cents / 100).toFixed(2)}` : "—"} />
        <Stat label="LLM" value={cost ? `$${(cost.llm_cents / 100).toFixed(2)}` : "—"} />
        <Stat label="TTS" value={cost ? `$${(cost.tts_cents / 100).toFixed(2)}` : "—"} />
        <Stat
          label="Twilio"
          value={cost ? `$${(cost.twilio_cents / 100).toFixed(2)}` : "—"}
        />
      </div>
    </div>
  );
}
