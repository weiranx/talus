import Link from "next/link";
import { unstable_rethrow } from "next/navigation";
import RouteMatches from "@/components/RouteMatches";
import { engine } from "@/lib/api";
import type { Call, RouteMatches as Matches } from "@/lib/types";

export default async function RouteRecommendations({ call }: { call: Call["call"] }) {
  try {
    const data = await engine<Matches>(`/api/routes/today?call=${call}`);
    return <RouteMatches key={JSON.stringify(data)} initial={data} call={call} />;
  } catch (e) {
    unstable_rethrow(e);
    return (
      <section className="card">
        <h2 className="label">Find a route for today</h2>
        <p className="muted">Route matches are unavailable right now. Follow today&apos;s call and browse your past routes.</p>
        <Link href="/activities">Your activities</Link>
      </section>
    );
  }
}
