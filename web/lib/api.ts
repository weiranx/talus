import { cookies, headers } from "next/headers";
import { redirect, unstable_rethrow } from "next/navigation";

// Server-side reads from the engine API. Every number comes from the engine. The browser's
// session cookie is passed along, so the engine answers for the signed-in account only.
const ENGINE = process.env.ENGINE_URL ?? "http://127.0.0.1:8000";

export class EngineError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function engine<T>(path: string): Promise<T> {
  const cookie = (await cookies()).toString();
  let res: Response;
  try {
    res = await fetch(`${ENGINE}${path}`, { cache: "no-store", headers: cookie ? { cookie } : {} });
  } catch {
    throw new EngineError(503, "The Talus engine is not running. Start it with: cd engine && uv run talus serve");
  }
  if (res.status === 401) {
    // Signed out or the session ended. The middleware only checks that a session cookie is there,
    // so this is where a stale one is caught: back to sign-in, then here again.
    const path = (await headers()).get("x-talus-path") ?? "/";
    redirect(path === "/" ? "/login" : `/login?next=${encodeURIComponent(path)}`);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // FastAPI's validation errors put a list in detail; only show it when it is a sentence.
    throw new EngineError(res.status, typeof body.detail === "string" ? body.detail : `Engine returned ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// For a page's catch: a sign-in redirect from engine() passes through; anything else becomes the
// message EngineDown shows.
export function engineFailed(e: unknown): string {
  unstable_rethrow(e);
  return e instanceof EngineError ? e.message : "Could not reach the engine.";
}
