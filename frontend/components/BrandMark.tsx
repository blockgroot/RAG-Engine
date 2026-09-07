export function BrandMark({ className = "brand-mark" }: { className?: string }) {
  return (
    <span className={className} aria-hidden>
      <span className="brand-monogram">
        <span className="brand-monogram-h">H</span>
        <span className="brand-monogram-b">B</span>
      </span>
    </span>
  );
}
