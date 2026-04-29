"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Kb = { id: string; name: string; source_type: string; version: number };

export default function KbPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState("upload");

  const { data } = useQuery<Kb[]>({
    queryKey: ["kbs"],
    queryFn: () => api<Kb[]>("/v1/kb"),
  });

  const create = useMutation({
    mutationFn: () =>
      api<Kb>("/v1/kb", {
        method: "POST",
        body: JSON.stringify({ name, source_type: sourceType }),
      }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["kbs"] });
    },
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Knowledge bases</h1>
      <div className="rounded-lg border p-4 space-y-3 max-w-lg">
        <h2 className="font-medium">New knowledge base</h2>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Mike's HVAC pricing & FAQ"
          className="w-full rounded-md border px-3 py-2"
        />
        <select
          value={sourceType}
          onChange={(e) => setSourceType(e.target.value)}
          className="w-full rounded-md border px-3 py-2"
        >
          <option value="upload">Upload PDF / DOCX</option>
          <option value="url">Crawl a website</option>
          <option value="csv">Import FAQ CSV</option>
        </select>
        <button
          onClick={() => create.mutate()}
          disabled={!name || create.isPending}
          className="rounded-md bg-primary text-primary-foreground px-4 py-2 disabled:opacity-50"
        >
          {create.isPending ? "Creating…" : "Create"}
        </button>
      </div>
      <div className="rounded-lg border divide-y">
        {(data ?? []).map((k) => (
          <div key={k.id} className="p-3 flex justify-between">
            <span>{k.name}</span>
            <span className="text-xs text-muted-foreground">
              v{k.version} · {k.source_type}
            </span>
          </div>
        ))}
        {(data ?? []).length === 0 && (
          <div className="p-6 text-sm text-muted-foreground">No knowledge bases yet.</div>
        )}
      </div>
    </div>
  );
}
