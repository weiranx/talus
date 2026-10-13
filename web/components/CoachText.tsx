import { Fragment, type ReactNode } from "react";

// Coach text, rendered for reading: paragraphs, bulleted and numbered lists, and **bold** for the
// numbers that matter. Only that small subset of markdown, built as React elements (never HTML),
// so a stray "*" or "<" in a reply shows as itself. Used for every reply the coach writes.

type Block = { kind: "p"; lines: string[] } | { kind: "ul" | "ol"; items: string[] };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d{1,2}[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,4}\s+(.*)$/;

export function blocks(text: string): Block[] {
  const out: Block[] = [];
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    const last = out[out.length - 1];
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (!line.trim()) {
      out.push({ kind: "p", lines: [] }); // a blank line ends the block
    } else if (bullet || numbered) {
      const kind = bullet ? "ul" : "ol";
      const item = (bullet ?? numbered)![1];
      if (last?.kind === kind) last.items.push(item);
      else out.push({ kind, items: [item] });
    } else {
      const heading = HEADING.exec(line);
      const text = heading ? `**${heading[1].replace(/\*\*/g, "")}**` : line.trim();
      // An indented line right after a list item continues that item.
      if ((last?.kind === "ul" || last?.kind === "ol") && /^\s+/.test(raw)) last.items[last.items.length - 1] += ` ${text}`;
      else if (last?.kind === "p" && last.lines.length) last.lines.push(text);
      else out.push({ kind: "p", lines: [text] });
    }
  }
  return out.filter((b) => (b.kind === "p" ? b.lines.length > 0 : b.items.length > 0));
}

export function inline(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+?\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") && p.length > 4
      ? <strong key={i}>{p.slice(2, -2)}</strong>
      : <Fragment key={i}>{p}</Fragment>);
}

export default function CoachText({ text, className, style }: { text: string | null | undefined; className?: string; style?: React.CSSProperties }) {
  if (!text) return null;
  const bs = blocks(text);
  return (
    <div className={`coach-text${className ? ` ${className}` : ""}`} style={style}>
      {bs.map((b, i) => b.kind === "p"
        ? <p key={i}>{b.lines.map((l, j) => <Fragment key={j}>{j ? <br /> : null}{inline(l)}</Fragment>)}</p>
        : b.kind === "ul"
          ? <ul key={i}>{b.items.map((t, j) => <li key={j}>{inline(t)}</li>)}</ul>
          : <ol key={i}>{b.items.map((t, j) => <li key={j}>{inline(t)}</li>)}</ol>)}
    </div>
  );
}
