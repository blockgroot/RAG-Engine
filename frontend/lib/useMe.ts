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
    a.has_connection === b.has_connection &&
    a.has_documents === b.has_documents &&
    a.sync_in_progress === b.sync_in_progress &&
    a.ready_to_ask === b.ready_to_ask &&
    a.latest_job_status === b.latest_job_status &&
    a.latest_doc_count === b.latest_doc_count
  );
}

/** Loads the signed-in session and applies production route guards. */
export function useMe(options: Options = {}) {
  const { requireAdmin = false, enforceSetupFlow = true } = options;
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const user = await api.me();
    setMe((prev) => (sameMe(prev, user) ? prev : user));
    return user;
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((user) => {
        if (cancelled) return;

        if (requireAdmin && user.role !== "admin") {
          router.replace("/chat");
          return;
        }

        if (enforceSetupFlow) {
          const onOnboarding = pathname.startsWith("/onboarding");
          const onAdmin = pathname.startsWith("/admin");

          if (user.role === "admin" && !isSetupComplete(user) && !onOnboarding) {
            router.replace("/onboarding");
            return;
          }

          if (user.role === "member" && onOnboarding) {
            router.replace("/chat");
            return;
          }

          if (onAdmin && !canAccessAdminPortal(user) && user.role === "admin") {
            router.replace("/onboarding");
            return;
          }
        }

        setMe(user);
      })
      .catch(() => {
        if (!cancelled) router.replace("/login");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router, pathname, requireAdmin, enforceSetupFlow]);

  return { me, loading, refresh, homePathFor };
}
