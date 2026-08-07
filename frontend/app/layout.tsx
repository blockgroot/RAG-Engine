import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

/* Friendly SaaS pairing from ui-ux-pro-max — modern alternative to Inter. */
const plusJakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-brand",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: {
    default: "Handbook",
    template: "%s · Handbook",
  },
  description: "Ask questions grounded in your own documents.",
  applicationName: "Handbook",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light" className={plusJakarta.variable}>
      <body className={plusJakarta.className}>{children}</body>
    </html>
  );
}
