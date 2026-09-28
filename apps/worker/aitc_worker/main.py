"""Worker loop: announce capabilities, claim runs, execute them with heartbeats, report results."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Any

from aitc_worker import git
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config()
    client = Client(config)
    git.ensure_repo(config.repo_path, config.repo_url)
    last_hello, roles = 0.0, []
    log.info("worker %s starting against %s", config.worker_id, config.api_url)
    while True:
        try:
            if time.monotonic() - last_hello > HELLO_SECONDS:
                caps = capabilities(config)
                client.hello(config.worker_id, caps, VERSION)
                roles = [r for r in ADAPTERS if caps[r]["available"]]
                last_hello = time.monotonic()
                log.info("team available: %s", ", ".join(roles) or "none — check your keys in .env")
            if not step(config, client, roles):
                time.sleep(config.poll_seconds)
        except ApiError as exc:
            log.warning("api error: %s", exc)
            time.sleep(config.poll_seconds * 2)
        except Exception:  # noqa: BLE001 - keep the worker alive; the API fences anything it held
            log.exception("worker loop error")
            time.sleep(config.poll_seconds * 2)


if __name__ == "__main__":
    main()
