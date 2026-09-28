import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { ACCESS_COOKIE, accessCode, accessToken, matches } from "@/lib/access";

/**
 * Shared-code gate for sharing the Control Center on a local network. It keeps out people who don't
 * have the code; it is not per-person sign-in — "Acting as" still picks the identity until OIDC (FR-27).
 */
export function proxy(request: NextRequest) {
  const code = accessCode();
  if (!code) return NextResponse.next();
  const cookie = request.cookies.get(ACCESS_COOKIE)?.value ?? "";
  if (matches(cookie, accessToken(code))) return NextResponse.next();
  if (request.nextUrl.pathname.startsWith("/v1/")) {
    return NextResponse.json({ code: "access_code_required", message: "Enter the workspace access code.",
      retryable: false, correlation_id: null, field_errors: {} }, { status: 401 });
  }
  const url = new URL("/access", request.url);
  url.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export const config = {
  // Everything except the access page itself and static assets.
  matcher: ["/((?!access|_next/static|_next/image|icon.svg|favicon.ico).*)"],
};
