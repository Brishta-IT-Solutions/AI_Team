import { createHash, timingSafeEqual } from "node:crypto";

export const ACCESS_COOKIE = "aitc_access";

/** The shared access code for this workspace; empty means no gate (e.g. one person on localhost). */
export function accessCode(): string {
  return (process.env.AITC_ACCESS_CODE ?? "").trim();
}

/** What the browser keeps: a digest of the code, never the code itself. */
export function accessToken(code: string): string {
  return createHash("sha256").update(`aitc-access:${code}`).digest("hex");
}

export function matches(given: string, expected: string): boolean {
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}
