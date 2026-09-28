"""Outbox at-least-once delivery with consumer dedup (AT-21) and lease fencing (AT-05, AT-14)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from control_api.db.models import BranchLease, OutboxEvent, Project, Repository, Task, User
from control_api.db.session import get_sessionmaker
from control_api.errors import Conflict
from control_api.services import leases
from control_api.services.outbox import consume_once, emit, relay_once


class Recorder:
    def __init__(self, fail_after: int | None = None) -> None:
        self.events: list[dict] = []
        self.fail_after = fail_after

    def publish(self, event: dict) -> None:
        if self.fail_after is not None and len(self.events) >= self.fail_after:
            raise ConnectionError("broker down")
        self.events.append(event)


@pytest.fixture
def seeded(db):
    user = User(subject="u", display_name="U")
    project = Project(key="LEASE", name="L", classification="SYNTHETIC")
    db.add_all([user, project])
    db.flush()
    repo = Repository(project_id=project.id, github_id=1, installation_id=1, owner="o",
                      name="n", base_branch="main")
    task = Task(project_id=project.id, key="LEASE-1", title="t", priority="P1", owner_id=user.id)
    db.add_all([repo, task])
    db.commit()
    return project, repo, task


def _emit(db, project, n):
    for i in range(n):
        emit(db, project_id=project.id, aggregate_type="task", aggregate_id="a",
             aggregate_version=i + 1, type="test", payload={"i": i})
    db.commit()


def test_relay_delivers_in_order_once(db, seeded):
    project, _, _ = seeded
    _emit(db, project, 3)
    rec = Recorder()
    assert relay_once(get_sessionmaker(), rec) == 3
    assert relay_once(get_sessionmaker(), rec) == 0
    assert [e["payload"]["i"] for e in rec.events] == [0, 1, 2]


def test_crash_mid_relay_redelivers_and_consumer_dedups(db, seeded):  # AT-21
    project, _, _ = seeded
    _emit(db, project, 2)
    flaky = Recorder(fail_after=1)
    with pytest.raises(ConnectionError):
        relay_once(get_sessionmaker(), flaky)  # published #0, then crashed: nothing committed
    assert db.scalar(select(OutboxEvent).where(OutboxEvent.delivered_at.is_not(None))) is None
    healthy = Recorder()
    relay_once(get_sessionmaker(), healthy)
    delivered = flaky.events + healthy.events
    assert len(delivered) == 3  # event #0 delivered twice: at-least-once

    effects: list[str] = []
    for event in delivered:
        with get_sessionmaker()() as s, s.begin():
            consume_once(s, "notifier", event["event_id"], lambda e=event: effects.append(e["event_id"]))
    assert len(effects) == 2 == len(set(effects))  # exactly one logical effect per event


def test_one_active_writer_per_branch(db, seeded):  # AT-05 (lease side)
    project, repo, task = seeded
    kw = dict(project_id=project.id, repository_id=repo.id, task_id=task.id,
              branch="feature/LEASE-1/abc")
    first = leases.acquire(db, holder="worker-a", **kw)
    again = leases.acquire(db, holder="worker-a", **kw)  # duplicate start → same lease
    assert again.fencing_token == first.fencing_token
    with pytest.raises(Conflict):
        leases.acquire(db, holder="worker-b", **kw)
    db.commit()


def test_expired_worker_is_fenced(db, seeded):  # AT-14
    project, repo, task = seeded
    kw = dict(project_id=project.id, repository_id=repo.id, task_id=task.id,
              branch="feature/LEASE-1/abc")
    old = leases.acquire(db, holder="worker-a", **kw)
    db.commit()
    db.execute(update(BranchLease).values(expires_at=BranchLease.expires_at - timedelta(minutes=5)))
    db.commit()
    new = leases.acquire(db, holder="worker-b", **kw)
    db.commit()
    assert new.fencing_token > old.fencing_token
    with pytest.raises(Conflict, match="fencing"):
        leases.assert_current(db, repository_id=repo.id, branch=kw["branch"],
                              token=old.fencing_token)
    with pytest.raises(Conflict):
        leases.heartbeat(db, repository_id=repo.id, branch=kw["branch"], token=old.fencing_token)
    leases.assert_current(db, repository_id=repo.id, branch=kw["branch"],
                          token=new.fencing_token)


def test_release_fences_holder(db, seeded):
    project, repo, task = seeded
    grant = leases.acquire(db, project_id=project.id, repository_id=repo.id, task_id=task.id,
                           branch="b", holder="w")
    assert leases.release(db, task_id=task.id, reason="CANCELLED") == 1
    with pytest.raises(Conflict):
        leases.assert_current(db, repository_id=repo.id, branch="b", token=grant.fencing_token)
    assert uuid.UUID(str(grant.lease_id))
