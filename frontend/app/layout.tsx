import type { Metadata } from "next";
import { IBM_Plex_Mono, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

/* Friendly SaaS pairing from ui-ux-pro-max — modern alternative to Inter. */
const plusJakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-brand",
  weight: ["400", "500", "600", "700"],
});

/* Small uppercase labels on the marketing page (app/site.css). */
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  variable: "--font-label",
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: {
    default: "Handbook",
    template: "%s · Handbook",
  },
  description:
    "Ask your company’s documents, conversations, and code. Handbook answers only from the Notion, Drive, Slack, and GitHub you connected — or says so honestly when it can’t.",
  applicationName: "Handbook",
  icons: {
    icon: [
      { url: "/icon.svg?v=4", type: "image/svg+xml" },
      { url: "/icon.svg?v=4", sizes: "any", type: "image/svg+xml" },
    ],
    shortcut: "/icon.svg?v=4",
    apple: "/apple-icon.svg?v=4",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light" className={`${plusJakarta.variable} ${plexMono.variable}`}>
      <body className={plusJakarta.className}>{children}</body>
    </html>
  );
}
