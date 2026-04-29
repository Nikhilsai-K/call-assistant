"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Campaign = {
  id: string;
  name: string;
  status: string;
  quiet_hours_enforced: boolean;
};

export default function CampaignsPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState("");

  const { data } = useQuery<Campaign[]>({
    queryKey: ["campaigns"],
    queryFn: () => api<Campaign[]>("/v1/campaigns"),
  });

  const create = useMutation({
    mutationFn: () =>
      api<Campaign>("/v1/campaigns", {
        method: "POST",
        body: JSON.stringify({ name, agent_id: agentId, quiet_hours_enforced: true }),
      }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });

  const start = useMutation({
    mutationFn: (id: string) =>
      api(`/v1/campaigns/${id}/start`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Outbound campaigns</h1>
      <p className="text-sm text-muted-foreground">
        Every outbound call is gated by DNC, prior-consent, and state quiet-hours
        checks. Campaigns won't start until DNC has been imported.
      </p>
      <div className="rounded-lg border p-4 space-y-3 max-w-lg">
        <h2 className="font-medium">New campaign</h2>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="May reactivation"
          className="w-full rounded-md border px-3 py-2"
        />
        <input
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          placeholder="Agent UUID"
          className="w-full rounded-md border px-3 py-2"
        />
        <button
          onClick={() => create.mutate()}
          disabled={!name || !agentId || create.isPending}
          className="rounded-md bg-primary text-primary-foreground px-4 py-2 disabled:opacity-50"
        >
          {create.isPending ? "Creating…" : "Create"}
        </button>
      </div>
      <div className="rounded-lg border divide-y">
        {(data ?? []).map((c) => (
          <div key={c.id} className="p-3 flex justify-between items-center">
            <div>
              <div className="font-medium">{c.name}</div>
              <div className="text-xs text-muted-foreground">{c.status}</div>
            </div>
            <button
              onClick={() => start.mutate(c.id)}
              disabled={c.status !== "draft"}
              className="rounded-md border px-3 py-1 text-xs disabled:opacity-50"
            >
              Start
            </button>
          </div>
        ))}
        {(data ?? []).length === 0 && (
          <div className="p-6 text-sm text-muted-foreground">No campaigns yet.</div>
        )}
      </div>
    </div>
  );
}
