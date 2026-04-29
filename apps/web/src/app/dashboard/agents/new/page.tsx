"use client";

import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

export default function NewAgentPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [plain, setPlain] = useState(
    "You're an HVAC receptionist for Mike's Heating. Book service jobs into Jobber, emergencies page Mike directly. Keep it friendly.",
  );

  const create = useMutation({
    mutationFn: () =>
      api<{ id: string }>("/v1/agents", {
        method: "POST",
        body: JSON.stringify({ name, plain_instructions: plain }),
      }),
    onSuccess: (a) => router.push(`/dashboard/agents/${a.id}`),
  });

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">New agent</h1>
        <p className="text-muted-foreground">
          Write instructions in plain English — we compile to a structured
          system prompt + tool config.
        </p>
      </div>
      <label className="block">
        <span className="text-sm font-medium">Name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mt-1 w-full rounded-md border px-3 py-2"
          placeholder="Mike's HVAC Receptionist"
        />
      </label>
      <label className="block">
        <span className="text-sm font-medium">Instructions</span>
        <textarea
          value={plain}
          onChange={(e) => setPlain(e.target.value)}
          rows={6}
          className="mt-1 w-full rounded-md border px-3 py-2"
        />
      </label>
      <button
        onClick={() => create.mutate()}
        disabled={!name || !plain || create.isPending}
        className="rounded-md bg-primary text-primary-foreground px-4 py-2 disabled:opacity-50"
      >
        {create.isPending ? "Creating…" : "Create & test"}
      </button>
    </div>
  );
}
