/**
 * Handbook wordmark tile — open book on the teal gradient.
 * Keep in sync with app/icon.tsx + app/apple-icon.tsx.
 */
export function BrandMark({ className = "brand-mark" }: { className?: string }) {
  return (
    <span className={className} aria-hidden>
      <svg viewBox="0 0 24 24" className="brand-mark-glyph" focusable="false">
        <path
          fill="rgba(255,252,247,0.95)"
          d="M4.2 6.4c0-.9.6-1.4 1.4-1.4H11v14H5.6c-.8 0-1.4-.5-1.4-1.4V6.4Z"
        />
        <path
          fill="rgba(255,252,247,0.88)"
          d="M13 5h5.4c.8 0 1.4.5 1.4 1.4v11.2c0 .9-.6 1.4-1.4 1.4H13V5Z"
        />
        <path
          fill="rgba(15,118,110,0.22)"
          d="M11 5h2v14h-2V5Z"
        />
        <path
          stroke="rgba(15,118,110,0.28)"
          strokeWidth="1"
          strokeLinecap="round"
          d="M6.4 9.2h3.2M6.4 11.6h3.2M6.4 14h2.2M14.4 9.2h3.2M14.4 11.6h3.2M14.4 14h2.2"
        />
      </svg>
    </span>
  );
}
