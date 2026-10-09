import Link from "next/link";

export default function EngineDown({ message }: { message: string }) {
  return (
    <div className="empty">
      <h1 className="title" style={{ fontSize: 22 }}>Nothing to show yet</h1>
      <p>{message}</p>
      {message.includes("Sign in") ? (
        <p><Link href="/login">Sign in</Link></p>
      ) : null}
      {message.includes("thresholds") ? (
        <p><Link href="/settings">Set your heart rate thresholds in Settings</Link></p>
      ) : null}
      {message.includes("not running") ? (<p className="muted">
        First run: <code>cd engine</code>, <code>uv run talus thresholds …</code>, <code>uv run talus sync</code>, then{" "}
        <code>uv run talus serve</code>.
      </p>) : null}
    </div>
  );
}
