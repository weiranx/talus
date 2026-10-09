"use client";

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
    <div className="side-note">
      <div style={{ overflowWrap: "anywhere" }}>{email}</div>
      <button className="link-btn" onClick={signOut}>Sign out</button>
    </div>
  );
}
