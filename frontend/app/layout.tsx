import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Policy Portal",
  description: "Ask questions grounded in your company's policy documents.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
