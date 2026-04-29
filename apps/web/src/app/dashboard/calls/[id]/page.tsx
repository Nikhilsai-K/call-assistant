"use client";

import { use, useEffect, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Turn = {
  speaker: string;
  text: string;
  start_ms: number;
  end_ms: number;
  is_redacted: boolean;
};

export default function CallDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [liveTurns, setLiveTurns] = useState<Turn[]>([]);
  const [whisperText, setWhisperText] = useState("");

  const { data: initial } = useQuery<Turn[]>({
    queryKey: ["transcript", id],
    queryFn: () => api<Turn[]>(`/v1/calls/${id}/transcript`),
  });

  // Live SSE stream — goes through our Next.js proxy which injects the
  // Clerk Bearer token (EventSource cannot send custom headers).
  useEffect(() => {
    const ev = new EventSource(`/api/calls/${id}/transcript/stream`);
    ev.addEventListener("turn", (e) => {
      try {
        const t = JSON.parse((e as MessageEvent).data) as Turn;
        setLiveTurns((prev) => [...prev, t]);
      } catch {}
    });
    return () => ev.close();
  }, [id]);

  const whisper = useMutation({
    mutationFn: (text: string) =>
      api(`/v1/calls/${id}/whisper`, {
        method: "POST",
        body: JSON.stringify({ text }),
      }),
  });
  const takeover = useMutation({
    mutationFn: () =>
      api(`/v1/calls/${id}/takeover`, {
        method: "POST",
        body: JSON.stringify({ human_name: "You", include_brief: true }),
      }),
  });

  const turns = [...(initial ?? []), ...liveTurns];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Call {id.slice(0, 8)}…</h1>
        <p className="text-muted-foreground">Live transcript and supervisor controls.</p>
      </div>

      <div className="flex gap-3">
        <input
          value={whisperText}
          onChange={(e) => setWhisperText(e.target.value)}
          placeholder="Whisper to agent…"
          className="flex-1 rounded-md border px-3 py-2"
        />
        <button
          onClick={() => {
            whisper.mutate(whisperText);
            setWhisperText("");
          }}
          className="rounded-md bg-primary text-primary-foreground px-4 py-2"
        >
          Send whisper
        </button>
        <button
          onClick={() => takeover.mutate()}
          className="rounded-md border px-4 py-2"
        >
          Take over
        </button>
      </div>

      <div className="rounded-lg border divide-y">
        {turns.map((t, i) => (
          <div key={i} className="p-3 flex gap-3">
            <span
              className={`text-xs font-medium px-2 py-0.5 rounded self-start ${
                t.speaker === "customer"
                  ? "bg-blue-100 text-blue-900"
                  : t.speaker === "agent"
                  ? "bg-green-100 text-green-900"
                  : "bg-amber-100 text-amber-900"
              }`}
            >
              {t.speaker}
            </span>
            <div className="flex-1">
              <div className="text-sm">{t.text}</div>
              <div className="text-xs text-muted-foreground">
                {Math.floor(t.start_ms / 1000)}s {t.is_redacted && "· redacted"}
              </div>
            </div>
          </div>
        ))}
        {turns.length === 0 && (
          <div className="p-6 text-muted-foreground text-sm">Waiting for turns…</div>
        )}
      </div>
    </div>
  );
}
