"use client";

import { use } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TestCall } from "@/components/test-call";

type Agent = {
  id: string;
  name: string;
  system_prompt: string;
  voice_id: string;
  voice_provider: string;
  llm_model: string;
  tools_enabled: string[];
  emergency_keywords: string[];
  status: string;
};

export default function AgentDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: agent } = useQuery<Agent>({
    queryKey: ["agent", id],
    queryFn: () => api<Agent>(`/v1/agents/${id}`),
  });

  const test = useMutation({
    mutationFn: () =>
      api<{ room: string; livekit_url: string; token: string }>(
        `/v1/agents/${id}/test`,
        { method: "POST" },
      ),
  });

  if (!agent) return <div>Loading…</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{agent.name}</h1>
          <div className="text-xs text-muted-foreground">
            {agent.llm_model} · {agent.voice_provider}:{agent.voice_id} · {agent.status}
          </div>
        </div>
        <button
          onClick={() => test.mutate()}
          className="rounded-md bg-primary text-primary-foreground px-4 py-2"
        >
          Start test call
        </button>
      </div>
      <div>
        <h2 className="text-sm font-medium mb-1">System prompt</h2>
        <pre className="whitespace-pre-wrap text-sm bg-muted p-3 rounded-md">
          {agent.system_prompt}
        </pre>
      </div>
      <div>
        <h2 className="text-sm font-medium mb-1">Tools</h2>
        <div className="flex gap-2 flex-wrap">
          {agent.tools_enabled.map((t) => (
            <span key={t} className="text-xs rounded bg-muted px-2 py-1">
              {t}
            </span>
          ))}
        </div>
      </div>
      {test.data && (
        <TestCall
          livekitUrl={test.data.livekit_url}
          token={test.data.token}
          room={test.data.room}
        />
      )}
    </div>
  );
}
