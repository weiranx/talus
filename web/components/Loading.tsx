import type { CSSProperties, ReactNode } from "react";

// Building blocks for loading states. Skeletons copy the size of what they stand in for, so nothing jumps when data lands.

export function Skel({ w = "100%", h = 14, r, style }: { w?: number | string; h?: number | string; r?: number; style?: CSSProperties }) {
  return <span className="skel" aria-hidden style={{ width: w, height: h, borderRadius: r, ...style }} />;
}

// Lines of text: the last one shorter, like a paragraph.
export function SkelLines({ n = 3, h = 13, gap = 8, last = "60%" }: { n?: number; h?: number; gap?: number; last?: string }) {
  return (
    <span aria-hidden style={{ display: "flex", flexDirection: "column", gap }}>
      {Array.from({ length: n }, (_, i) => <Skel key={i} h={h} w={i === n - 1 && n > 1 ? last : "100%"} />)}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return <span className="spinner" role={label ? "status" : undefined} aria-label={label} aria-hidden={label ? undefined : true} />;
}

export function Dots() {
  return <span className="dots" aria-hidden><span /><span /><span /></span>;
}

// A muted line with a spinner, for "the coach is reading" style waits.
export function BusyLine({ children, dark }: { children: ReactNode; dark?: boolean }) {
  return (
    <div className="busy-line muted" role="status" style={dark ? { color: "var(--feature-muted)" } : undefined}>
      <Spinner />
      <span>{children}</span>
    </div>
  );
}

// The whole page while its data loads: announced once, then hidden from screen readers.
export function SkelPage({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="skel-page" style={{ display: "flex", flexDirection: "column", gap: 16 }} aria-busy="true">
      <span className="visually-hidden" role="status">{label}</span>
      <div aria-hidden style={{ display: "contents" }}>{children}</div>
    </div>
  );
}

export function SkelHead({ eyebrow, title = 160, right }: { eyebrow?: boolean; title?: number; right?: number }) {
  return (
    <header className="page-head">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {eyebrow ? <Skel w={140} h={13} /> : null}
        <Skel w={title} h={32} r={8} />
      </div>
      {right ? <Skel w={right} h={14} style={{ marginBottom: 8 }} /> : null}
    </header>
  );
}

export function SkelTable({ rows = 5, cols = 5, minWidth }: { rows?: number; cols?: number; minWidth?: number }) {
  return (
    <div className="table-wrap">
      <table style={{ minWidth }}>
        <thead>
          <tr>{Array.from({ length: cols }, (_, i) => <th key={i}><Skel w={i === 1 ? 80 : 48} h={10} style={{ marginLeft: i > 1 ? "auto" : 0 }} /></th>)}</tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              {Array.from({ length: cols }, (_, i) => (
                <td key={i}><Skel w={i === 1 ? `${55 + ((r * 17) % 35)}%` : i === 0 ? 52 : 40} h={13} style={{ marginLeft: i > 1 ? "auto" : 0 }} /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// A card with a label and a few lines, or custom contents.
export function SkelCard({ label = 110, lines = 3, style, children }: { label?: number; lines?: number; style?: CSSProperties; children?: ReactNode }) {
  return (
    <section className="card" style={style}>
      <Skel w={label} h={12} />
      {children ?? <SkelLines n={lines} />}
    </section>
  );
}
