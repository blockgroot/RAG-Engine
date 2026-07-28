import type { Me } from "./api";

/** Admin org is ready only after a finished sync (not mid-ingest). */
export function isSetupComplete(me: Me): boolean {
  return me.has_connection && me.ready_to_ask;
}

/** Where a signed-in user should land after login / visiting `/`. */
export function homePathFor(me: Me): string {
  if (me.role === "admin" && !isSetupComplete(me)) {
    return "/onboarding";
  }
  return "/chat";
}

/** True when this user may use the full admin portal (not just onboarding). */
export function canAccessAdminPortal(me: Me): boolean {
  return me.role === "admin" && isSetupComplete(me);
}
