"""A tiny, dependency-free demo repository so the team has something real to work on."""

from __future__ import annotations

import os
import subprocess

FILES = {
    "README.md": "# Employee Portal (demo)\n\nA small demo codebase the AI team works on. "
                 "Run the tests with `npm test`.\n",
    "package.json": '{\n  "name": "employee-portal-demo",\n  "version": "0.1.0",\n  "private": true,\n'
                    '  "type": "module",\n  "scripts": {\n    "test": "node --test"\n  }\n}\n',
    "src/identity.js": """// Identity checks for the employee portal.

/** Returns true when the identity document has not expired on the given date. */
export function isIdentityValid(identity, today = new Date()) {
  if (!identity || !identity.expiresOn) return false;
  return new Date(identity.expiresOn) >= today;
}
""",
    "test/identity.test.js": """import { test } from "node:test";
import assert from "node:assert/strict";
import { isIdentityValid } from "../src/identity.js";

test("valid identity is accepted", () => {
  assert.equal(isIdentityValid({ expiresOn: "2999-01-01" }), true);
});

test("missing identity is refused", () => {
  assert.equal(isIdentityValid(null), false);
});
""",
}


def create(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    for rel, content in FILES.items():
        full = os.path.join(path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    ident = ["-c", "user.name=AI Team Control Center", "-c", "user.email=control-center@localhost"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "Initial demo project"]):
        subprocess.run(["git", *ident, *args], cwd=path, check=True, capture_output=True)
