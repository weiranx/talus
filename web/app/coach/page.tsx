import Link from "next/link";
import EngineDown from "@/components/EngineDown";
import Chat from "@/components/plan/Chat";
import { engine, engineFailed } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";

export const dynamic = "force-dynamic";

const SUGGESTIONS = [
  "How have my miles and vert trended lately?",
  "What should I focus on this week?",
  "Am I doing enough easy running?",
  "How did my last long run go?",
];

// A chat with the coach about anything in your training, not only the plan. It shares one
// conversation with the chat on the Plan page.
export default async function CoachPage() {
  let chat: { messages: ChatMessage[] };
  try {
    chat = await engine<{ messages: ChatMessage[] }>("/api/coach/chat");
  } catch (e) {
    return <EngineDown message={engineFailed(e)} />;
  }
  return (
    <>
      <header className="page-head">
        <div>
          <div className="eyebrow">Reads your weeks, runs, readiness and plan</div>
          <h1 className="title">Coach</h1>
        </div>
        <div className="muted" style={{ paddingBottom: 6 }}>To change your plan, use <Link href="/plan">Plan</Link>.</div>
      </header>
      <Chat initial={chat.messages} full suggestions={SUGGESTIONS} />
    </>
  );
}
