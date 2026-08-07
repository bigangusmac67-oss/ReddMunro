"""
jobs.py — asynchronous execution for audits too large for a request.

An audit is CPU-bound (eigendecomposition, an O(N^2) regression sweep,
and a shuffle-baseline MI estimate). A 150-metric x 250k-row Pro audit
will not finish inside a normal HTTP timeout, so anything above a tier's
`async_only_above_cells` threshold is queued.

WHAT THIS MODULE IS AND IS NOT.

It defines the JobStore INTERFACE and ships two implementations:

  InMemoryJobStore   single process, threads. Correct for development
                     and for a single-instance deployment. Loses jobs on
                     restart, and does not share state between workers.
  RedisJobStore      stub with the exact methods to implement. Left
                     unimplemented rather than half-implemented: a job
                     store that silently loses jobs under concurrency is
                     worse than one that refuses to start.

For production the recommended path is Celery or RQ with Redis, and
`submit()` becomes a task dispatch. The interface below is deliberately
narrow so that swap is a single class, not a refactor.

WHY NOT asyncio: the work is CPU-bound and holds the GIL. Running it on
the event loop would block every other request on the worker. Threads
help only because numpy releases the GIL inside BLAS calls; a process
pool or a real broker is the correct answer at scale, and the interface
supports both.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Protocol

import service as SVC

# Terminal states are those a client may stop polling on.
PENDING, RUNNING, SUCCEEDED, FAILED, EXPIRED = (
    "pending", "running", "succeeded", "failed", "expired")
TERMINAL = {SUCCEEDED, FAILED, EXPIRED}


@dataclass
class Job:
    id: str
    account_id: str
    tier: str
    status: str = PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict | None = None
    error: dict | None = None
    # progress is coarse on purpose: the engine does not expose
    # intermediate progress, and a fake percentage that jumps 0 -> 100
    # is worse than an honest stage label
    stage: str = "queued"
    source_path: str | None = None
    ignore: tuple = ()
    dataset_id: str | None = None
    # The basis declaration travels WITH the job. Wiring only the
    # synchronous path would mean a large upload silently loses its
    # declaration and comes back `declared: false` — the same class of
    # silent failure the declaration exists to expose, reintroduced at
    # the queue boundary.
    scale_by: tuple = ()
    scale_exempt: tuple = ()
    basis: str | None = None
    require_basis: bool = False

    def to_dict(self, include_result: bool = True) -> dict:
        d = asdict(self)
        d.pop("source_path", None)
        d["ignore"] = list(self.ignore)
        d["scale_by"] = list(self.scale_by)
        d["scale_exempt"] = list(self.scale_exempt)
        if not include_result:
            d.pop("result", None)
        d["duration_ms"] = (
            int(((self.finished_at or time.time()) - self.started_at) * 1000)
            if self.started_at else None)
        return d


class JobStore(Protocol):
    def create(self, job: Job) -> None: ...
    def get(self, job_id: str) -> Job | None: ...
    def update(self, job: Job) -> None: ...
    def list_for_account(self, account_id: str, limit: int = 50
                         ) -> list[Job]: ...


class InMemoryJobStore:
    """Thread-safe, process-local. See module docstring for limits."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def create(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def list_for_account(self, account_id: str, limit: int = 50) -> list[Job]:
        with self._lock:
            js = [j for j in self._jobs.values()
                  if j.account_id == account_id]
        js.sort(key=lambda j: j.created_at, reverse=True)
        return js[:limit]

    def purge_expired(self, retention_days: int) -> int:
        """Retention is a tier property and a compliance obligation, not
        a cleanup nicety. Returns the number purged."""
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            stale = [j.id for j in self._jobs.values()
                     if j.created_at < cutoff]
            for jid in stale:
                del self._jobs[jid]
        return len(stale)


class RedisJobStore:
    """Not implemented. Methods listed so the shape is unambiguous."""

    def __init__(self, *_a, **_kw):
        raise NotImplementedError(
            "RedisJobStore is a placeholder. Implement create/get/update/"
            "list_for_account against Redis, or use Celery/RQ and make "
            "submit() a task dispatch. Do not ship InMemoryJobStore "
            "behind more than one worker process: jobs created on one "
            "worker are invisible to the others, and clients polling a "
            "different worker will see 404 for a job that is running.")


# ----------------------------------------------------------------------
class AuditRunner:
    """Bounded worker pool executing audits against a JobStore.

    `max_workers` is deliberately small by default. Each audit holds a
    full copy of the matrix plus intermediates; concurrency is bounded
    by memory long before it is bounded by cores, and an OOM kill takes
    down every in-flight job on the instance, not just the greedy one.
    """

    def __init__(self, store: JobStore | None = None, max_workers: int = 2,
                 delete_source_on_finish: bool = True):
        self.store = store or InMemoryJobStore()
        self.max_workers = max_workers
        self.delete_source = delete_source_on_finish
        self._q: queue.Queue[str] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._threads:
            return
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker, name=f"audit-{i}",
                                 daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for _ in self._threads:
            self._q.put("")
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        self._stop.clear()

    # -- submission ----------------------------------------------------
    def submit(self, path: str, *, account_id: str, tier: str = "pro",
               ignore=(), dataset_id: str | None = None,
               scale_by=(), scale_exempt=(), basis: str | None = None,
               require_basis: bool = False) -> Job:
        """Queue an audit. Quota is checked HERE, synchronously, before
        the job is accepted — a client should learn it is over limit
        from the submit call, not by polling a job that fails later."""
        t = SVC.TIERS.get(tier)
        if t is None:
            raise SVC.InvalidInput(f"unknown tier {tier!r}")
        try:
            metrics, rows, _ = SVC.measure(path, ignore=ignore)
        except (ValueError, FileNotFoundError) as exc:
            raise SVC.InvalidInput(str(exc)) from exc
        SVC.enforce(t, metrics, rows)
        # Same validation the synchronous path applies, so an async
        # caller cannot get a 202 for a request the sync route would
        # have rejected with a 422.
        SVC.validate_basis_request(path, scale_by=scale_by,
                                   scale_exempt=scale_exempt, basis=basis,
                                   require_basis=require_basis)

        job = Job(id=uuid.uuid4().hex, account_id=account_id, tier=tier,
                  source_path=path, ignore=tuple(ignore),
                  dataset_id=dataset_id,
                  scale_by=tuple(scale_by), scale_exempt=tuple(scale_exempt),
                  basis=basis, require_basis=require_basis)
        self.store.create(job)
        self._q.put(job.id)
        if not self._threads:
            self.start()
        return job

    # -- execution -----------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            job_id = self._q.get()
            if not job_id:
                self._q.task_done()
                continue
            try:
                self._run(job_id)
            finally:
                self._q.task_done()

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        job.status, job.started_at, job.stage = RUNNING, time.time(), "analysing"
        self.store.update(job)
        try:
            job.result = SVC.run_audit(
                job.source_path, tier=job.tier, ignore=job.ignore,
                dataset_id=job.dataset_id,
                scale_by=job.scale_by, scale_exempt=job.scale_exempt,
                basis=job.basis, require_basis=job.require_basis)
            job.status, job.stage = SUCCEEDED, "complete"
        except (SVC.QuotaExceeded, SVC.InvalidInput) as exc:
            job.status, job.stage = FAILED, "rejected"
            job.error = exc.to_dict()
        except Exception as exc:                      # unexpected
            # The traceback is kept server-side; the client gets an id.
            # The engine is Apache 2.0, so this is no longer about IP —
            # it is that a stack trace names server paths and internal
            # structure, which is a support burden and a small gift to
            # anyone probing the service.
            job.status, job.stage = FAILED, "error"
            job.error = {"error": "internal_error", "job_id": job.id,
                         "type": type(exc).__name__}
            job.trace = traceback.format_exc()        # type: ignore[attr-defined]
        finally:
            job.finished_at = time.time()
            self.store.update(job)
            if self.delete_source and job.source_path:
                try:
                    os.remove(job.source_path)
                except OSError:
                    pass

    def wait(self, job_id: str, timeout: float = 30.0,
             poll: float = 0.05) -> Job | None:
        """Block until terminal. For tests and for the synchronous
        convenience path — never call this from a request handler."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.store.get(job_id)
            if job and job.status in TERMINAL:
                return job
            time.sleep(poll)
        return self.store.get(job_id)
