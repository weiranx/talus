"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import LinkPending from "@/components/LinkPending";

// 20px line icons, drawn in currentColor so they follow the theme and the active state.
const icon = (d: ReactNode) => (
  <svg className="nav-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden>{d}</svg>
);
const ICONS: Record<string, ReactNode> = {
  "/": icon(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>),
  "/load": icon(<path d="M3 20h18M5 16l4-6 4 3 6-8" />),
  "/plan": icon(<><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></>),
  "/activities": icon(<path d="M3 19l6-11 4 6 3-4 5 9z" />),
  "/settings": icon(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>),
  "/admin": icon(<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />),
};

const LINKS = [
  { href: "/", label: "Today" },
  { href: "/load", label: "Load" },
  { href: "/plan", label: "Plan" },
  { href: "/activities", label: "Activities" },
  { href: "/settings", label: "Settings" },
];

export default function Nav() {
  const path = usePathname();
  const [admin, setAdmin] = useState(false);
  const signedIn = path !== "/login";

  // Only the admin account gets the Admin link; the engine refuses everyone else anyway.
  useEffect(() => {
    if (!signedIn) return setAdmin(false);
    fetch("/api/auth/status", { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => setAdmin(Boolean(s.account?.is_admin)))
      .catch(() => setAdmin(false));
  }, [signedIn]);

  if (!signedIn) return null;
  const links = admin ? [...LINKS, { href: "/admin", label: "Admin" }] : LINKS;
  return (
    <nav className="nav" aria-label="Main">
      {links.map((l) => {
        const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
        return (
          <Link key={l.href} href={l.href} aria-current={active ? "page" : undefined}>
            {ICONS[l.href]}
            <span className="nav-label">{l.label}</span>
            <LinkPending />
          </Link>
        );
      })}
    </nav>
  );
}
