"use client";

import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { ConnectionCard } from "@/components/ConnectionCard";
import { useMe } from "@/lib/useMe";
import { api, ConnectionRecord } from "@/lib/api";

const PROVIDERS: ("notion" | "google" | "github")[] = ["notion", "google", "github"];

export default function ConnectionsPage() {
  const { me, loading } = useMe();
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (me) api.listConnections().then(setConnections);
  }, [me]);

  async function handleIngest(connectionId: string) {
    setMessage(null);
    try {
      const { job_id } = await api.triggerIngest(connectionId);
      setMessage(`Ingestion started — job ${job_id}. Track progress on the Jobs page.`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not start ingestion.");
    }
  }

  if (loading) return null;

  return (
    <>
      <Nav me={me} />
      <main className="page-wide stack">
        <h1>Connections</h1>
        <p className="muted">Connect a source so the portal can read your company&rsquo;s policy documents.</p>
        {message && <div className="card">{message}</div>}
        <div className="stack">
          {PROVIDERS.map((provider) => (
            <ConnectionCard
              key={provider}
              provider={provider}
              connection={connections.find((c) => c.provider === provider)}
              onIngest={handleIngest}
            />
          ))}
        </div>
      </main>
    </>
  );
}
