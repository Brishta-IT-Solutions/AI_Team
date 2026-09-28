"use client";

import { useEffect, useState } from "react";
import { getSubject, setSubject } from "@/lib/api";

const SUBJECTS = [
  ["admin", "Administrator"],
  ["product", "Product Lead"],
  ["eng", "Engineering Lead"],
  ["qa", "QA Reviewer"],
  ["observer", "Observer"],
] as const;

/** Development identity switcher. Replaced by OIDC sign-in (FR-27). */
export function Identity() {
  const [subject, set] = useState("product");
  useEffect(() => set(getSubject()), []);
  return (
    <label className="identity">
      <span className="small muted">Acting as</span>
      <select
        value={subject}
        onChange={(e) => {
          set(e.target.value);
          setSubject(e.target.value);
        }}
      >
        {SUBJECTS.map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>
      <span className="badge tone-warn" title="Development sign-in; OIDC replaces this">dev</span>
    </label>
  );
}
