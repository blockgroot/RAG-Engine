"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { homePathFor } from "@/lib/routing";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    api
      .me()
      .then((me) => router.replace(homePathFor(me)))
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <main className="page">
      <p className="muted">Loading…</p>
    </main>
  );
}
