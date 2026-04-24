import Link from "next/link";

const NAV = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/calls", label: "Calls" },
  { href: "/dashboard/agents", label: "Agents" },
  { href: "/dashboard/kb", label: "Knowledge" },
  { href: "/dashboard/integrations", label: "Integrations" },
  { href: "/dashboard/campaigns", label: "Campaigns" },
  { href: "/dashboard/analytics", label: "Analytics" },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[240px_1fr] min-h-screen">
      <aside className="border-r p-5 space-y-1">
        <div className="font-semibold text-lg mb-6">VocalFlow</div>
        <nav className="flex flex-col gap-1">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className="px-3 py-2 rounded-md hover:bg-muted text-sm"
            >
              {n.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="p-8 overflow-auto">{children}</main>
    </div>
  );
}
