"""Worker loop: announce capabilities, claim runs, execute them with heartbeats, report results."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import replace
from typing import Any

from aitc_worker.adapters import ba_gemini, developer_claude, junior_ollama, qa_codex
from aitc_worker.adapters.base import Outcome, Reporter, RunContext
from aitc_worker.client import ApiError, Client
from aitc_worker.config import Config

VERSION = "0.1.0"
HEARTBEAT_SECONDS = 15
HELLO_SECONDS = 30
ADAPTERS = {"BA": ba_gemini, "DEVELOPER": developer_claude, "QA": qa_codex}
log = logging.getLogger("aitc.worker")


def capabilities(config: Config) -> dict[str, dict[str, Any]]:
    return {"BA": ba_gemini.availability(config), "DEVELOPER": developer_claude.availability(config),
            "QA": qa_codex.availability(config), "JUNIOR": junior_ollama.availability(config)}


def execute(config: Config, client: Client, run: dict[str, Any]) -> dict[str, Any]:
    reporter, cancel, done = Reporter(), threading.Event(), threading.Event()

    def beat() -> None:
        while not done.wait(HEARTBEAT_SECONDS):
            try:
                if client.heartbeat(run["run_id"], run["claim_token"], reporter.milestone,
                                    reporter.drain()).get("cancel"):
                    cancel.set()
            except ApiError as exc:
                if exc.body.get("code") == "claim_fenced":
                    cancel.set()  # someone else owns this work now; stop touching it
            except Exception:  # noqa: BLE001 - a missed beat is retried on the next tick
                log.warning("heartbeat failed", exc_info=True)

    beater = threading.Thread(target=beat, daemon=True)
    beater.start()
    ctx = RunContext(config, client, run, cancel, reporter)
    try:
        outcome = ADAPTERS[run["role"]].run(ctx)
    except Exception as exc:  # noqa: BLE001 - report every crash as a failed run, never hang
        log.error("run crashed\n%s", traceback.format_exc())
        outcome = Outcome.failed("worker_crash", f"{type(exc).__name__}: {exc}")
    finally:
        done.set()
        beater.join(timeout=5)
    try:  # flush the last log lines; the result below is what matters
        client.heartbeat(run["run_id"], run["claim_token"], reporter.milestone, reporter.drain(200))
    except Exception:  # noqa: BLE001
        log.info("final heartbeat not delivered", exc_info=True)
    return client.result(run["run_id"], run["claim_token"], outcome=outcome.outcome, payload=outcome.payload,
                         usage=outcome.usage, errors=outcome.errors, provider=outcome.provider,
                         model=outcome.model)


def step(config: Config, client: Client, roles: list[str]) -> bool:
    """Claim and finish one run. Returns False when there was nothing to do."""
    run = client.claim(config.worker_id, roles) if roles else None
    if run is None:
        return False
    log.info("claimed %s run %s for %s", run["role"], run["run_id"], run["envelope"].get("task_key"))
    outcome = execute(config, client, run)
    log.info("finished %s: %s", run["run_id"], outcome)
    return True


def seats(config: Config) -> list[tuple[str, Client]]:
    """One project-scoped identity per project served (FR-16): (worker id, client)."""
    if not config.projects:
        return [(config.worker_id, Client(config))]
    return [(f"{config.worker_id}:{key}", Client(config, token=f"dev-service:worker:{key}"))
            for key in config.projects]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config()
    served = seats(config)
    last_hello, roles, caps = 0.0, [], {}
    log.info("worker %s starting against %s for %s", config.worker_id, config.api_url,
             ", ".join(config.projects) or "the project in AITC_WORKER_TOKEN")
    while True:
        announce = time.monotonic() - last_hello > HELLO_SECONDS
        if announce:
            caps = capabilities(config)
            roles = [r for r in ADAPTERS if caps[r]["available"]]
            last_hello = time.monotonic()
            log.info("team available: %s", ", ".join(roles) or "none — check your keys in .env")
        busy = False
        for worker_id, client in served:
            try:
                if announce:  # keeps each project's Team panel live
                    client.hello(worker_id, caps, VERSION)
                busy = step(replace(config, worker_id=worker_id), client, roles) or busy
            except ApiError as exc:
                log.warning("%s: api error: %s", worker_id, exc)
            except Exception:  # noqa: BLE001 - keep the worker alive; the API fences anything it held
                log.exception("%s: worker loop error", worker_id)
        if not busy:
            time.sleep(config.poll_seconds)


if __name__ == "__main__":
    main()
