/** Recognizable app marks — the real logos, not stand-in emoji (no 🐙). */

export type BrandName = "notion" | "drive" | "github" | "slack" | "gmail" | "workspace";

export function BrandGlyph({
  name,
  size = 28,
}: {
  name: BrandName;
  size?: number;
}) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    "aria-hidden": true as const,
  };

  if (name === "notion") {
    return (
      <svg {...common}>
        <rect width="24" height="24" rx="5" fill="#191919" />
        <path
          d="M8.2 6.4h2.1l5.5 8.6V6.4H18v11.2h-2.1L10.4 9v8.6H8.2V6.4Z"
          fill="#fff"
        />
      </svg>
    );
  }
  if (name === "drive") {
    return (
      <svg {...common}>
        <path fill="#4285F4" d="M8.1 3.6 2.4 13.5l3.2 5.5 5.7-9.9z" />
        <path fill="#EA4335" d="M15.9 3.6H8.1l5.7 9.9h7.8z" />
        <path fill="#34A853" d="M5.6 19 2.4 13.5h11.4L17.1 19z" />
        <path fill="#FBBC04" d="M15.9 3.6 21.6 13.5l-3.2 5.5-5.7-9.9z" />
      </svg>
    );
  }
  if (name === "github") {
    return (
      <svg {...common}>
        <rect width="24" height="24" rx="6" fill="#181717" />
        <path
          fill="#fff"
          d="M12 4.4c-4.2 0-7.6 3.4-7.6 7.6 0 3.4 2.2 6.2 5.2 7.2.4.1.5-.2.5-.4v-1.4c-2.1.5-2.6-1-2.6-1-.3-.9-.8-1.1-.8-1.1-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.2 1.8.8 2.2.6.1-.5.3-.8.5-1-1.7-.2-3.5-.9-3.5-3.8 0-.8.3-1.5.8-2.1-.1-.2-.4-1 .1-2.1 0 0 .7-.2 2.2.8a7.6 7.6 0 0 1 4 0c1.5-1 2.2-.8 2.2-.8.5 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.1 0 2.9-1.8 3.6-3.5 3.8.3.3.5.7.5 1.5v2.2c0 .2.1.5.5.4 3-.1 5.2-3.8 5.2-7.2 0-4.2-3.4-7.6-7.6-7.6Z"
        />
      </svg>
    );
  }
  if (name === "slack") {
    return (
      <svg {...common}>
        <path fill="#E01E5A" d="M6.4 14.2a1.8 1.8 0 1 1-1.8-1.8h1.8v1.8Zm.9 0a1.8 1.8 0 1 1 3.6 0v1.8H7.3v-1.8Z" />
        <path fill="#36C5F0" d="M9.8 6.4a1.8 1.8 0 1 1 1.8-1.8v1.8H9.8Zm0 .9a1.8 1.8 0 1 1 0 3.6H8v-3.6h1.8Z" />
        <path fill="#2EB67D" d="M17.6 9.8a1.8 1.8 0 1 1 1.8 1.8h-1.8V9.8Zm-.9 0a1.8 1.8 0 1 1-3.6 0V8h3.6v1.8Z" />
        <path fill="#ECB22E" d="M14.2 17.6a1.8 1.8 0 1 1-1.8 1.8v-1.8h1.8Zm0-.9a1.8 1.8 0 1 1 0-3.6h1.8v3.6H14.2Z" />
      </svg>
    );
  }
  if (name === "gmail") {
    return (
      <svg {...common}>
        <rect width="24" height="24" rx="5" fill="#fff" />
        <path d="M4 7.2 12 13l8-5.8V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7.2Z" fill="#E8EAED" />
        <path d="M4 7.2 12 13 4 18.2V7.2Z" fill="#EA4335" />
        <path d="M20 7.2 12 13l8 5.2V7.2Z" fill="#FBBC05" />
        <path d="M4 7.2 12 4.5 20 7.2 12 13 4 7.2Z" fill="#C5221F" />
        <path d="M4 7.2 12 13l8-5.8" stroke="#fff" strokeWidth="0.6" fill="none" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <rect width="24" height="24" rx="6" fill="#0F766E" />
      <path d="M5.5 9.2h5.1l1.3-1.5h6.6v9.6H5.5V9.2Z" fill="#99F6E4" />
      <path d="M5.5 10.4h13v8H5.5v-8Z" fill="#fff" />
    </svg>
  );
}
