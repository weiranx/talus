import { dayLabel, mi, n0 } from "@/lib/format";
import { KIND_COLOR, minutesText } from "@/lib/plan";
import type { PlanSession } from "@/lib/types";

// The next seven days as cards. Only these days have sessions; later weeks are targets.
export default function DayCards({ sessions, today }: { sessions: PlanSession[]; today: string }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 10 }}>
      {sessions.map((s) => {
        const objective = s.kind === "objective";
        const off = s.kind === "rest" || s.kind === "done";
        return (
          <div key={s.day} style={{
            border: "1px solid var(--line)", borderTop: `4px solid ${KIND_COLOR[s.kind]}`, borderRadius: 12,
            padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4, minWidth: 0,
            background: objective ? "var(--ink)" : "#fff", color: objective ? "#fff" : undefined,
          }}>
            <div className="muted" style={{ color: objective ? "#b9c6c0" : undefined, fontWeight: s.day === today ? 600 : 400 }}>
              {s.day === today ? "Today" : dayLabel(s.day, { weekday: "short", month: "short", day: "numeric" })}
            </div>
            <div style={{ fontWeight: 600 }}>{s.title}</div>
            {off ? null : (
              <div className="mono" style={{ fontSize: 13 }}>
                {mi(s.miles)} mi · {n0(s.gain_ft)} ft up · {n0(s.loss_ft)} ft down
              </div>
            )}
            {off ? null : <div className="muted" style={{ color: objective ? "#b9c6c0" : undefined }}>{minutesText(s.minutes)}</div>}
            <div style={{ fontSize: 12, color: objective ? "#e4eae7" : "var(--muted)" }}>{s.how}</div>
          </div>
        );
      })}
    </div>
  );
}
