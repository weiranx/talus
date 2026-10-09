import { cookies } from "next/headers";

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
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new EngineError(res.status, body.detail ?? `Engine returned ${res.status}`);
  }
  return res.json() as Promise<T>;
}
