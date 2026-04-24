"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Agent = {
  id: string;
  name: string;
  status: string;
  llm_model: string;
  voice_id: string;
};

export default function AgentsPage() {
  const { data } = useQuery<Agent[]>({
    queryKey: ["agents"],
    queryFn: () => api<Agent[]>("/v1/agents"),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agents</h1>
        <Link
          href="/dashboard/agents/new"
          className="rounded-md bg-primary text-primary-foreground px-4 py-2 text-sm"
        >
          New agent
        </Link>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(data ?? []).map((a) => (
          <Link
            key={a.id}
            href={`/dashboard/agents/${a.id}`}
            className="rounded-lg border p-4 hover:bg-muted"
          >
            <div className="flex items-center justify-between">
              <div className="font-medium">{a.name}</div>
              <span className="text-xs px-2 py-0.5 rounded bg-muted text-muted-foreground">
                {a.status}
              </span>
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              {a.llm_model} · voice {a.voice_id}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
