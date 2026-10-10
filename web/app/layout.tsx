import type { Metadata, Viewport } from "next";
import { DM_Mono, Figtree } from "next/font/google";
import AccountMenu from "@/components/AccountMenu";
import Nav from "@/components/Nav";
import { THEME_SCRIPT } from "@/lib/theme";
import "./globals.css";

const sans = Figtree({ subsets: ["latin"], variable: "--font-sans", weight: ["400", "500", "600", "700"] });
const mono = DM_Mono({ subsets: ["latin"], variable: "--font-mono", weight: ["400", "500"] });

export const metadata: Metadata = { title: "Talus", description: "Mountain training load" };
export const viewport: Viewport = {
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
      </head>
      <body>
        <div className="shell">
          <aside className="sidebar">
            <div className="brand">Talus</div>
            <Nav />
            <div className="side-note">
              <div>Data from intervals.icu</div>
            </div>
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
