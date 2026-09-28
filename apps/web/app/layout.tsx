import type { Metadata } from "next";
import Link from "next/link";
import { Identity } from "@/components/Identity";
import "./globals.css";

export const metadata: Metadata = {
  title: "Control Center",
  description: "From approved specification to approved merge, with every decision on record.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="sr-only">Skip to content</a>
        <div className="shell">
          <header className="topbar">
            <Link href="/" className="wordmark">
              <strong>Control Center</strong>
              <span>AI Software Team</span>
            </Link>
            <Identity />
          </header>
          <main id="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
