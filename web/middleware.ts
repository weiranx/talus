import { NextResponse, type NextRequest } from "next/server";

// Pages need a signed-in account. The engine checks the session cookie; anyone without a valid
// one goes to /login. Browser calls to /api/* are checked by the engine itself.
const ENGINE = process.env.ENGINE_URL ?? "http://127.0.0.1:8000";

export async function middleware(req: NextRequest) {
  const cookie = req.headers.get("cookie");
  let signedIn = false;
  if (cookie?.includes("talus_session=")) {
    try {
      const res = await fetch(`${ENGINE}/api/auth/status`, { headers: { cookie }, cache: "no-store" });
      signedIn = res.ok && Boolean((await res.json()).account);
    } catch {
      return NextResponse.next(); // engine down: let the page say so
    }
  }
  if (signedIn) return NextResponse.next();
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = req.nextUrl.pathname === "/" ? "" : `?next=${encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search)}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!api/|_next/|login|favicon.ico).*)"],
};
