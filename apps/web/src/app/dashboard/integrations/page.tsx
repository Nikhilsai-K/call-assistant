"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Integration = { provider: string; status: string };

const CATALOG = [
  { id: "google_calendar", label: "Google Calendar", category: "Calendar" },
  { id: "microsoft365", label: "Microsoft 365", category: "Calendar" },
  { id: "calendly", label: "Calendly", category: "Calendar" },
  { id: "acuity", label: "Acuity", category: "Calendar" },
  { id: "cal_com", label: "Cal.com", category: "Calendar" },
  { id: "hubspot", label: "HubSpot", category: "CRM" },
  { id: "gohighlevel", label: "GoHighLevel", category: "CRM" },
  { id: "pipedrive", label: "Pipedrive", category: "CRM" },
  { id: "salesforce", label: "Salesforce", category: "CRM" },
  { id: "jobber", label: "Jobber", category: "Field service" },
  { id: "housecall_pro", label: "Housecall Pro", category: "Field service" },
  { id: "servicetitan", label: "ServiceTitan", category: "Field service" },
  { id: "nexhealth", label: "NexHealth", category: "Healthcare" },
  { id: "drchrono", label: "DrChrono", category: "Healthcare" },
  { id: "slack", label: "Slack", category: "Comms" },
  { id: "teams", label: "Microsoft Teams", category: "Comms" },
  { id: "stripe", label: "Stripe", category: "Payments" },
  { id: "twilio", label: "Twilio", category: "Telephony" },
  { id: "postmark", label: "Postmark", category: "Email" },
  { id: "resend", label: "Resend", category: "Email" },
];

export default function IntegrationsPage() {
  const { data } = useQuery<Integration[]>({
    queryKey: ["integrations"],
    queryFn: () => api<Integration[]>("/v1/integrations"),
  });
  const status = new Map((data ?? []).map((i) => [i.provider, i.status]));

  const start = async (provider: string) => {
    const r = await api<{ authorize_url: string }>(`/v1/integrations/${provider}/oauth/start`, {
      method: "POST",
      body: JSON.stringify({ return_url: window.location.href }),
    });
    window.location.href = r.authorize_url;
  };

  const grouped = CATALOG.reduce<Record<string, typeof CATALOG>>((acc, item) => {
    (acc[item.category] ||= []).push(item);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Integrations</h1>
      {Object.entries(grouped).map(([cat, items]) => (
        <section key={cat} className="space-y-2">
          <h2 className="text-sm uppercase text-muted-foreground tracking-wide">{cat}</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {items.map((p) => {
              const s = status.get(p.id);
              return (
                <div key={p.id} className="rounded-lg border p-3 flex items-center justify-between">
                  <div>
                    <div className="font-medium">{p.label}</div>
                    <div className="text-xs text-muted-foreground">
                      {s ? s : "not connected"}
                    </div>
                  </div>
                  <button
                    onClick={() => start(p.id)}
                    className="rounded-md border px-3 py-1 text-xs hover:bg-muted"
                  >
                    {s === "active" ? "Reconnect" : "Connect"}
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
