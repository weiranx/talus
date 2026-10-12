"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

export default function AccountMenu() {
  const path = usePathname();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/status", { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => setEmail(s.account?.email ?? null))
      .catch(() => setEmail(null));
  }, [path]);

  if (!email || path === "/login") return null;
  async function signOut() {
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => null);
    window.location.replace("/login");
  }
  return (
    <div className="side-note account-menu">
      <div className="account-email" style={{ overflowWrap: "anywhere" }}>{email}</div>
      <div className="account-links">
        <Link href="/status" className="link-btn">Status</Link>
        <button className="link-btn" onClick={signOut}>Sign out</button>
      </div>
    </div>
  );
}
