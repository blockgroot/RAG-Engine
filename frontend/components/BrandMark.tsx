/**
 * Handbook brand mark — HD book + chat logo (transparent PNG).
 * Keep in sync with app/icon.png + app/apple-icon.png.
 */
export function BrandMark({ className = "brand-mark" }: { className?: string }) {
  return (
    <span className={className} aria-hidden>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/brand/handbook-mark.png?v=3"
        alt=""
        className="brand-mark-glyph"
        width={1024}
        height={1024}
        decoding="async"
        fetchPriority="high"
      />
    </span>
  );
}
