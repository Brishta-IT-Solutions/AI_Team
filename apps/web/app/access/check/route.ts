import { NextResponse } from "next/server";
import { ACCESS_COOKIE, accessCode, accessToken, matches } from "@/lib/access";

export async function POST(request: Request) {
  const expected = accessCode();
  const { code } = (await request.json().catch(() => ({}))) as { code?: string };
  if (!expected || !code || !matches(accessToken(code.trim()), accessToken(expected))) {
    await new Promise((r) => setTimeout(r, 750)); // slow down guessing
    return NextResponse.json({ ok: false }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(ACCESS_COOKIE, accessToken(expected), {
    httpOnly: true, sameSite: "lax", path: "/", maxAge: 60 * 60 * 24 * 30,
  });
  return res;
}
