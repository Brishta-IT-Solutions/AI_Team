"""Run a child process with a deadline, cancellation and a line callback."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

GRACE_SECONDS = 30


@dataclass
class ProcResult:
    exit_code: int
    output: str
    timed_out: bool = False
    cancelled: bool = False


def run(argv: list[str], *, cwd: str, env: dict[str, str], timeout: float,
        cancel: threading.Event | None = None, on_line: Callable[[str], None] | None = None,
        stdin: str | None = None) -> ProcResult:
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            text=True, start_new_session=True, bufsize=1)
    lines: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            if on_line:
                on_line(line.rstrip("\n"))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    if stdin is not None:
        assert proc.stdin is not None
        proc.stdin.write(stdin)
        proc.stdin.close()
    deadline = time.monotonic() + timeout
    timed_out = cancelled = False
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        if time.monotonic() > deadline:
            timed_out = True
            break
        time.sleep(0.2)
    if proc.poll() is None:  # interrupt the whole process tree, then force after a grace period
        _signal(proc, signal.SIGTERM)
        try:
            proc.wait(GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal(proc, signal.SIGKILL)
            proc.wait()
    reader.join(timeout=5)
    return ProcResult(proc.returncode, "".join(lines), timed_out, cancelled)


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass
