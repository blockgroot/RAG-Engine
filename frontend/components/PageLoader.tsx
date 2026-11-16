"use client";

import { HbLogo } from "@/components/site/HbLogo";
import { useConnecting } from "@/lib/serverWake";

/** The whole-page wait while a page reads the session and its data. Says
 *  "Connecting" while the server is still starting (lib/serverWake.ts). */
export function PageLoader({ label = "Loading" }: { label?: string }) {
  const connecting = useConnecting();
  return (
    <div className="page-loader" role="status" aria-live="polite">
      <HbLogo className="page-loader-logo" />
      <span className="page-loader-bar" aria-hidden />
      <span className="page-loader-label">{connecting ? "Connecting…" : label}</span>
      {connecting && (
        <span className="page-loader-note">This is taking longer than usual. The page will open as soon as the connection completes.</span>
      )}
    </div>
  );
}
