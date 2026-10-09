import type { Metadata } from "next";
import { DM_Mono, Figtree } from "next/font/google";
import Nav from "@/components/Nav";
import "./globals.css";

const sans = Figtree({ subsets: ["latin"], variable: "--font-sans", weight: ["400", "500", "600", "700"] });
const mono = DM_Mono({ subsets: ["latin"], variable: "--font-mono", weight: ["400", "500"] });

export const metadata: Metadata = { title: "Talus", description: "Mountain training load" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <div className="shell">
          <aside className="sidebar">
            <div className="brand">Talus</div>
            <Nav />
            <div className="side-note">
              <div>Data from intervals.icu</div>
            </div>
          </aside>
          <main className="main">
            <div className="page">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
