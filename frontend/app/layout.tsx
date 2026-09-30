import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { Providers } from "@/components/Providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "WardWatch",
  description: "Ward deterioration and sepsis early warning",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:rounded focus:bg-white focus:p-2"
        >
          Skip to main content
        </a>
        <Providers>
          <header className="border-b border-slate-200 bg-white">
            <nav aria-label="Main" className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
              <span className="font-semibold">WardWatch</span>
              <Link href="/" className="hover:underline">
                Ward
              </Link>
              <Link href="/alerts" className="hover:underline">
                Alerts
              </Link>
              <ConnectionIndicator />
            </nav>
          </header>
          <main id="main" className="mx-auto max-w-7xl px-6 py-6">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
