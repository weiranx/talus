import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import AccountMenu from "@/components/AccountMenu";
import Nav from "@/components/Nav";
import { STALE_RELOAD_SCRIPT } from "@/lib/stale";
import { THEME_SCRIPT } from "@/lib/theme";
import "./globals.css";
import "./workspace.css";

// Fonts ship with the app so builds never fetch from Google Fonts (a failed fetch broke CI).
const sans = localFont({ src: "./fonts/figtree-latin-wght-normal.woff2", variable: "--font-sans", weight: "300 900" });
const mono = localFont({
  src: [
    { path: "./fonts/dm-mono-latin-400-normal.woff2", weight: "400" },
    { path: "./fonts/dm-mono-latin-500-normal.woff2", weight: "500" },
  ],
  variable: "--font-mono",
});

export const metadata: Metadata = { title: "Talus", description: "Mountain training load" };
export const viewport: Viewport = {
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f2f4f3" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1513" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <script dangerouslySetInnerHTML={{ __html: STALE_RELOAD_SCRIPT }} />
      </head>
      <body className="talus-workspace">
        <div className="shell">
          <aside className="sidebar">
            <div className="brand">
              <svg className="brand-mark" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" aria-hidden><path d="m5 11 9-7M4 22l19-15M15 24l9-7" /></svg>
              Talus
            </div>
            <Nav />
            <AccountMenu />
          </aside>
          <main className="main">
            <div className="page">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
