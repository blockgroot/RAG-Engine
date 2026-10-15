"use client";

import { AppShell } from "@/components/AppShell";
import { GapTopics } from "@/components/GapTopics";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";

/**
 * What people asked company-wide and could not get answered, grouped by
 * meaning. Only what an admin can fix: a missing or outdated document. Chart
 * refusals and figure-reading failures are ours and are not shown here. A
 * space's topics go to that space's owner, on the space page.
 */
export default function FeedbackPage() {
  const { me, loading } = useMe({ requireAdmin: true });

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="What we couldn’t answer"
          description="Topics people asked about and couldn't get answered, or got an answer they marked wrong. Each is a document worth writing or a source worth connecting."
          scene="people"
        />
        <GapTopics />
      </main>
    </AppShell>
  );
}
