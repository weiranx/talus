import { NextResponse, type NextRequest } from "next/server";

// Pages need a signed-in account. Without a session cookie, go to /login straight away. With one,
// the page goes ahead and the engine checks it on the page's own first call (a stale session
// gets a 401 there and engine() sends it to /login), which saves a round trip to the engine on
// every page. Browser calls to /api/* are checked by the engine itself.
export function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname + req.nextUrl.search;
  if (req.cookies.get("talus_session")?.value) {
    const headers = new Headers(req.headers);
    headers.set("x-talus-path", path);
    return NextResponse.next({ request: { headers } });
  }
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = req.nextUrl.pathname === "/" ? "" : `?next=${encodeURIComponent(path)}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!api/|_next/|login|favicon.ico).*)"],
};
