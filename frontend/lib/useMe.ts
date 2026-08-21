"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, Me } from "./api";
import { canAccessAdminPortal, homePathFor, isSetupComplete } from "./routing";

type Options = {
  requireAdmin?: boolean;
  enforceSetupFlow?: boolean;
};

function sameMe(a: Me | null, b: Me): boolean {
  if (!a) return false;
  return (
    a.user_id === b.user_id &&
    a.org_id === b.org_id &&
    a.role === b.role &&
    a.org_name === b.org_name &&
    a.email === b.email &&
    a.has_connection === b.has_connection &&
    a.has_documents === b.has_documents &&
    a.sync_in_progress === b.sync_in_progress &&
    a.ready_to_ask === b.ready_to_ask &&
    a.latest_job_status === b.latest_job_status &&
    a.latest_doc_count === b.latest_doc_count
  );
}

let cachedMe: Me | null = null;
let inflight: Promise<Me> | null = null;

function loadMe(force = false): Promise<Me> {
  if (!force && inflight) return inflight;
  const request = api.me().then(
    (user) => {
      cachedMe = user;
      if (inflight === request) inflight = null;
      return user;
    },
    (err) => {
      if (inflight === request) inflight = null;
      throw err;
    }
  );
  inflight = request;
  return request;
}

export function clearMeCache(): void {
  cachedMe = null;
  inflight = null;
}

export function useMe(options: Options = {}) {
  const { requireAdmin = false, enforceSetupFlow = true } = options;
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(cachedMe);
  const [loading, setLoading] = useState(cachedMe === null);

  const applyGuards = useCallback(
    (user: Me): boolean => {
      if (requireAdmin && user.role !== "admin") {
        router.replace("/chat");
        return true;
      }
      if (enforceSetupFlow) {
        const onOnboarding = pathname.startsWith("/onboarding");
        const onAdmin = pathname.startsWith("/admin");

        if (user.role === "admin" && !isSetupComplete(user) && !onOnboarding) {
          router.replace("/onboarding");
          return true;
        }
        if (user.role === "member" && onOnboarding) {
          router.replace("/chat");
          return true;
        }
        if (onAdmin && !canAccessAdminPortal(user) && user.role === "admin") {
          router.replace("/onboarding");
          return true;
        }
      }
      return false;
    },
    [router, pathname, requireAdmin, enforceSetupFlow]
  );

  const refresh = useCallback(async () => {
    const user = await loadMe(true);
    setMe((prev) => (sameMe(prev, user) ? prev : user));
    return user;
  }, []);

  useEffect(() => {
    let cancelled = false;

    if (cachedMe && applyGuards(cachedMe)) return;

    loadMe()
      .then((user) => {
        if (cancelled) return;
        if (applyGuards(user)) return;
        setMe((prev) => (sameMe(prev, user) ? prev : user));
      })
      .catch(() => {
        if (!cancelled) {
          clearMeCache();
          router.replace("/login");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [router, applyGuards]);

  return { me, loading, refresh, homePathFor };
}
