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

function Card({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border p-4">
      <div className="text-xs uppercase text-muted-foreground tracking-wide">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}

export default function AnalyticsPage() {
  const { data: c } = useQuery<CallAnalytics>({
    queryKey: ["analytics", "calls"],
    queryFn: () => api<CallAnalytics>("/v1/analytics/calls"),
  });
  const { data: cost } = useQuery<CostAnalytics>({
    queryKey: ["analytics", "cost"],
    queryFn: () => api<CostAnalytics>("/v1/analytics/cost"),
  });

  const margin =
    c && cost && c.total > 0
      ? ((c.total * 20 - cost.total_cents / 100) / (c.total * 20)) * 100
      : null;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Analytics</h1>
      <p className="text-muted-foreground">
        Volume, outcomes, and per-leg cost. Margin assumes $0.20/min retail.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card label="Calls" value={c?.total ?? "—"} />
        <Card label="Booked" value={c?.booked ?? "—"} />
        <Card label="Transferred" value={c?.transferred ?? "—"} />
        <Card label="Abandoned" value={c?.abandoned ?? "—"} />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Card
          label="Total COGS"
          value={cost ? `$${(cost.total_cents / 100).toFixed(2)}` : "—"}
        />
        <Card label="STT" value={cost ? `$${(cost.stt_cents / 100).toFixed(2)}` : "—"} />
        <Card label="LLM" value={cost ? `$${(cost.llm_cents / 100).toFixed(2)}` : "—"} />
        <Card label="TTS" value={cost ? `$${(cost.tts_cents / 100).toFixed(2)}` : "—"} />
        <Card
          label="Twilio"
          value={cost ? `$${(cost.twilio_cents / 100).toFixed(2)}` : "—"}
        />
      </div>
      {margin !== null && (
        <Card label="Estimated margin" value={`${margin.toFixed(1)}%`} />
      )}
    </div>
  );
}
