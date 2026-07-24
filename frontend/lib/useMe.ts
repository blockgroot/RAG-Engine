"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, Me } from "./api";

/** Loads the signed-in session, redirecting to /login if there isn't one. */
export function useMe() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch(() => router.replace("/login"))
      .finally(() => setLoading(false));
  }, [router]);

  return { me, loading };
}
