import type { Me } from "./api";

/** Admin org is ready when a source is connected and at least one doc ingested. */
export function isSetupComplete(me: Me): boolean {
  return me.has_connection && me.has_documents;
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
