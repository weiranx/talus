import Link from "next/link";
import type { Onboarding } from "@/lib/types";
import { unfinished } from "./steps";

// What Today still needs, each a link back to its setup step. `empty`: Today has nothing to
// show yet, so this fills the page; otherwise it's a card above the numbers.
export default function SetupList({ ob, empty }: { ob: Onboarding; empty?: boolean }) {
  const todo = unfinished(ob);
  const importing = ob.import?.state === "running";
  if (!todo.length && !importing) return null;
  const body = (
    <>
      {importing ? (
        <div className="muted">
          Importing your history: {ob.import!.total == null ? "getting the list" : `${ob.import!.done} of ${ob.import!.total}`}.{" "}
          <Link href="/welcome?step=import">See progress</Link>
        </div>
      ) : null}
      {todo.length ? (
        <ol className="method-list" style={{ margin: 0 }}>
          {todo.map((s) => <li key={s.key}><Link href={`/welcome?step=${s.key}`}>{s.todo}</Link></li>)}
        </ol>
      ) : null}
    </>
  );
  if (empty) {
    return (
      <div className="empty" style={{ textAlign: "left", maxWidth: 560 }}>
        <h1 className="title" style={{ fontSize: 22 }}>Finish setting up</h1>
        <p>Today needs your history and heart rate thresholds before it can make a call.</p>
        {body}
      </div>
    );
  }
  return (
    <section className="card" style={{ gap: 8 }}>
      <h2 className="label">Finish setting up</h2>
      {body}
    </section>
  );
}
