// How a coach reply is labeled. The coach text is always marked as generated, and rule-based
// text says why the coach isn't showing.
export function coachNote(source: "coach" | "rules", reason: string | null, what: string): string {
  if (source === "coach") return `Written by the AI coach from your engine numbers. Every number was checked against the data.`;
  if (reason === "no_key") return `Rule-based ${what}. Add an Anthropic API key to the engine's .env to turn on the AI coach.`;
  if (reason === "off") return `Rule-based ${what}. The AI coach is turned off in Settings.`;
  if (reason === "rejected") return `Rule-based ${what}. The coach quoted numbers that aren't in your data, so its reply was dropped.`;
  return `Rule-based ${what}. The AI coach could not answer this time.`;
}
