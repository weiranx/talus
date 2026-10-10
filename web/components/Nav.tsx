"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import LinkPending from "@/components/LinkPending";

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
            {l.label}
            <LinkPending />
          </Link>
        );
      })}
    </nav>
  );
}
