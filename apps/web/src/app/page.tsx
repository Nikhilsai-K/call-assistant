import Link from "next/link";

export default function Home() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-8 p-8">
      <h1 className="text-4xl font-bold">VocalFlow</h1>
      <p className="text-muted-foreground max-w-xl text-center">
        Voice AI agents for service businesses. Sub-500ms latency, native
        integrations, shadow-mode onboarding.
      </p>
      <div className="flex gap-4">
        <Link
          href="/dashboard"
          className="rounded-md bg-primary text-primary-foreground px-5 py-2"
        >
          Open dashboard
        </Link>
        <Link
          href="/dashboard/agents/new"
          className="rounded-md border px-5 py-2"
        >
          Create an agent
        </Link>
      </div>
    </main>
  );
}
