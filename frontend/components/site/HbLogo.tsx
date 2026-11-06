/** Handbook's logo (public/handbook-logo.svg) at a badge's size; the class sets the size. */
export function HbLogo({ className = "hero-mark" }: { className?: string }) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img className={`hb-logo ${className}`} src="/handbook-logo.svg" alt="" aria-hidden />;
}
