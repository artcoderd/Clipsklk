import { NextResponse, type NextRequest } from "next/server";

// Visitors without a session see the public landing page at "/" (good for SEO);
// signed-in users get the app. The cookie is only checked for presence here,
// the API validates it.
export function proxy(request: NextRequest) {
  if (!request.cookies.has(process.env.SESSION_COOKIE_NAME ?? "clipeo_session")) {
    return NextResponse.rewrite(new URL("/bienvenida", request.url));
  }
  return NextResponse.next();
}

export const config = { matcher: "/" };
