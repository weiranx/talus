export default function Pill({ tone, children }: { tone: "good" | "warn" | "bad" | "neutral"; children: React.ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function statePill(state: string) {
  return state === "overreached" ? "bad" : state === "loaded" ? "warn" : "good";
}
