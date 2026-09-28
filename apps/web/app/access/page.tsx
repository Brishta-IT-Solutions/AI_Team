"use client";

import { useState } from "react";

export default function AccessPage() {
  const [code, setCode] = useState("");
  const [wrong, setWrong] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const res = await fetch("/access/check", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ code }) });
    if (res.ok) {
      const next = new URLSearchParams(window.location.search).get("next") ?? "/";
      window.location.assign(next.startsWith("/") && !next.startsWith("//") ? next : "/");
      return;
    }
    setWrong(true);
    setBusy(false);
  }

  return (
    <form className="card pad stack" style={{ maxWidth: 380, margin: "10vh auto 0" }} onSubmit={submit}>
      <div>
        <p className="eyebrow">AI Software Team</p>
        <h1>Control Center</h1>
      </div>
      <label className="field">Access code
        <input type="password" autoFocus required value={code} autoComplete="current-password"
          onChange={(e) => { setCode(e.target.value); setWrong(false); }} />
      </label>
      {wrong && <p className="notice small" role="alert" style={{ margin: 0 }}>That code isn’t right.</p>}
      <button className="btn btn-primary" disabled={busy}>Continue</button>
      <p className="small muted" style={{ margin: 0 }}>Ask the person who runs the Control Center for the code.</p>
    </form>
  );
}
