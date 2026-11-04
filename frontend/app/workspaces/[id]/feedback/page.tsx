"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { GapTopics } from "@/components/GapTopics";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";
import { useMe } from "@/lib/useMe";

/**
 * The space's version of /admin/feedback: what its members could not get answered,
 * for its OWNER only (the API refuses anyone else). Reached from the bell,
 * which names the topic, like the company page is for admins.
 */
export default function SpaceFeedbackPage() {
  const { id: workspaceId } = useParams<{ id: string }>();
  const { me, loading } = useMe({ enforceSetupFlow: false });
  const [name, setName] = useState<string | null>(null);

  useEffect(() => {
    api.getWorkspace(workspaceId).then((w) => setName(w.name)).catch(() => setName(null));
  }, [workspaceId]);

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  return (
    <AppShell me={me} variant="app">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow={name ? `Space · ${name}` : "Space"}
          title="What we couldn’t answer"
          description="Topics this space's members asked about and couldn't get answered, or got an answer they marked wrong. Each is a document worth adding to the space."
          scene="spaces"
        />
        <GapTopics workspaceId={workspaceId} />
      </main>
    </AppShell>
  );
}
