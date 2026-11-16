import { HbLogo } from "@/components/site/HbLogo";

/** The whole-page wait while a page reads the session and its data. */
export function PageLoader({ label = "Loading" }: { label?: string }) {
  return (
    <div className="page-loader" role="status" aria-live="polite">
      <HbLogo className="page-loader-logo" />
      <span className="page-loader-bar" aria-hidden />
      <span className="page-loader-label">{label}</span>
    </div>
  );
}
