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

/*
 * Session cache, shared across every page and every `useMe()` caller.
 *
 * Why this exists: `useMe` runs on essentially every page, its effect depends on
 * `pathname`, and the state started at `null` with `loading = true`. So each
 * navigation re-fetched `/me` AND blocked the whole page behind that round trip
 * — the user saw "Loading…" on every single page transition, even though the
 * session had not changed. Two components both calling `useMe()` also issued two
 * identical requests on the same render.
 *
 * `cachedMe` lets a subsequent page render immediately from the session we
 * already know, while `inflight` collapses concurrent callers onto one request.
 * The value is still revalidated in the background on every mount, so
 * `ready_to_ask` / `sync_in_progress` / role changes are picked up exactly as
 * before — this is stale-while-revalidate, not a stale-forever cache.
 */
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

/** Drop the cached session. Call on sign-out so the next load re-authenticates. */
export function clearMeCache(): void {
  cachedMe = null;
  inflight = null;
}

/** Loads the signed-in session and applies production route guards. */
export function useMe(options: Options = {}) {
  const { requireAdmin = false, enforceSetupFlow = true } = options;
  const router = useRouter();
  const pathname = usePathname();
  // Seed from the shared cache so a navigation renders without a network wait.
  const [me, setMe] = useState<Me | null>(cachedMe);
  const [loading, setLoading] = useState(cachedMe === null);

  /** Returns true if a redirect was issued (caller should stop). */
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

    // Guards must still hold for THIS route even when rendering from cache —
    // the cached session is the same user, so the decision is the same, and it
    // is re-checked below against the revalidated response anyway.
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
