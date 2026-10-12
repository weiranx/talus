import Link from "next/link";
import { notFound } from "next/navigation";
import AdminView from "@/components/AdminView";
import EngineDown from "@/components/EngineDown";
import { engine, engineFailed, EngineError } from "@/lib/api";
import type { AdminOverview } from "@/lib/types";

export const dynamic = "force-dynamic";

// Only the admin account gets data here; the engine answers 404 to everyone else, so this page
// doesn't exist for them either.
export default async function AdminPage() {
  let data: AdminOverview;
  try {
    data = await engine<AdminOverview>("/api/admin");
  } catch (e) {
    if (e instanceof EngineError && e.status === 404) notFound();
    return <EngineDown message={engineFailed(e)} />;
  }
  return (
    <>
      <header className="page-head">
        <h1 className="title">Admin</h1>
        <Link href="/status" style={{ paddingBottom: 6 }}>Status</Link>
      </header>
      <AdminView data={data} />
    </>
  );
}
