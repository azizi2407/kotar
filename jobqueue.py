"""Postgres SKIP LOCKED job queue — workers pull (project owner's context), the web enqueues.

enqueue: web (svc-agency) adds a job. claim: a worker atomically claims a job
(FOR UPDATE SKIP LOCKED, so multiple workers don't collide). complete/fail:
write the result. fail requeues with backoff on transient errors; reap_stuck
recovers stuck jobs.
"""
import threading
from datetime import timedelta

from sqlalchemy import text

from extensions import db
from models import Job, utcnow
from notifications import notify_job_failed, notify_job_stuck

# Backoff steps (seconds): attempts=1->60, 2->300, 3+->900. Exponential-ish, fixed table.
_BACKOFF = (60, 300, 900)

# --- dedup serialization (against the TOCTOU race) ---
# enqueue's dedup is check-then-act: `_active_dupe` SELECT + an unconditional
# INSERT. Left unlocked, two concurrent requests (gthread 2x4) could each
# find the check "empty" without seeing the other's commit and produce 2
# active jobs. Two-layer serialization:
#   1) Within-process: a threading.Lock per (type, dedup_key) — deterministically
#      serializes threads of the same process (gthread 4 threads/worker) and the sqlite test.
#   2) Across processes: a Postgres transaction-scoped advisory lock
#      (pg_advisory_xact_lock) — also serializes 2 separate gunicorn
#      processes' connections; automatically released on commit.
#      sqlite has no advisory lock -> skipped (single file + the in-process lock is enough).
_KEY_LOCKS = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(job_type, dedup_key):
    """Return a shared threading.Lock per (type, dedup_key) (in-process serialization)."""
    k = (job_type, dedup_key)
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(k)
        if lock is None:
            lock = _KEY_LOCKS[k] = threading.Lock()
        return lock


def _backoff(n):
    """Backoff seconds based on the attempts count (clamp the table)."""
    return _BACKOFF[min(max(n, 1), len(_BACKOFF)) - 1]


def _active_dupe(job_type, dedup_key):
    """Return the job among the same type's active (queued|running) jobs
    whose payload._dedup_key matches. A SINGLE dialect-independent strategy:
    since the active queue is NARROW, we pull them all and match in Python
    (NOT dependent on a PG JSON-path / JSONB index; works the same way in
    the sqlite test). If scale becomes an issue, a separately indexed column
    can be added later (YAGNI)."""
    actives = Job.query.filter(
        Job.type == job_type, Job.status.in_(('queued', 'running'))).all()
    for j in actives:
        if (j.payload or {}).get('_dedup_key') == dedup_key:
            return j
    return None


def enqueue(job_type, payload, priority=0, dedup_key=None, created_by=None):
    """Add a job. priority: a higher number is claimed first (caption=10, batch=0).
    If dedup_key is given, it's written to the payload as `_dedup_key` and
    DEDUP is applied: if an active (queued|running) job for the same `type`
    already carries the same dedup_key, NO new INSERT happens, the existing
    job is returned (a repeat call is a no-op). Once the active job is
    done/failed, enqueue with the same key produces a new job (dedup only
    covers active jobs). If dedup_key is None, the payload is untouched and
    no dedup is applied."""
    if dedup_key is None:
        j = Job(type=job_type, status='queued', payload=payload,
                priority=priority, created_by=created_by)
        db.session.add(j)
        db.session.commit()
        return j
    # dedup: serialize the check+insert (closes the TOCTOU race). The
    # in-process lock + the (PG) transaction-scoped advisory lock together
    # cut off both thread and process collisions.
    with _key_lock(job_type, dedup_key):
        if db.session.get_bind().dialect.name == 'postgresql':
            # xact lock: other connections waiting on the same key are
            # blocked until this transaction commits (the INSERT becomes
            # visible) -> no double INSERT.
            db.session.execute(
                text('SELECT pg_advisory_xact_lock(hashtext(:k))'),
                {'k': f'{job_type}:{dedup_key}'})
        existing = _active_dupe(job_type, dedup_key)
        if existing is not None:
            # Release the advisory lock (no new INSERT). commit, NOT
            # rollback: the SELECT changed nothing, but the caller could have
            # made other pending changes in the same transaction BEFORE
            # calling enqueue; a rollback would silently discard those.
            # commit both releases the transaction-scoped advisory lock and
            # preserves the caller's pending state (consistent with the
            # commit on the normal INSERT path).
            db.session.commit()
            return existing
        payload = {**(payload or {}), '_dedup_key': dedup_key}
        j = Job(type=job_type, status='queued', payload=payload,
                priority=priority, created_by=created_by)
        db.session.add(j)
        db.session.commit()  # the advisory lock is released here
        return j


def enqueue_per_client(job_type, client_ids, payload_fn, priority=0,
                       dedup_key_fn=None, created_by=None):
    """Fans out batch jobs (brief/special day) into a SEPARATE job per
    client — NOT a loop inside a single job; this isolates partial failures
    (if one client blows up, the others still proceed). Builds the payload
    for each client_id via `payload_fn(client_id)`; if `dedup_key_fn` is
    given, each client gets ITS OWN key (`dedup_key_fn(client_id)` — per
    client, NOT a single scalar). Returns the list of created Jobs. Used by
    brief/special-day Phase 2/3."""
    jobs = []
    for cid in client_ids:
        dedup_key = dedup_key_fn(cid) if dedup_key_fn is not None else None
        jobs.append(enqueue(job_type, payload_fn(cid), priority=priority,
                            dedup_key=dedup_key, created_by=created_by))
    return jobs


def _select_query(types, now, for_update=False):
    """claim's base query — includes the available_at window. for_update
    produces SKIP LOCKED on the PG dialect (a compile test validates this query)."""
    q = (Job.query.filter(
            Job.status == 'queued',
            Job.type.in_(types),
            (Job.available_at.is_(None) | (Job.available_at <= now)))
         .order_by(Job.priority.desc(), Job.id.asc()))
    if for_update:
        q = q.with_for_update(skip_locked=True)
    return q


def claim(types):
    """Atomically claim the oldest claimable job from the given types
    (status=running). Jobs with an available_at in the future (waiting on
    backoff) are skipped."""
    now = utcnow()
    pg = db.session.get_bind().dialect.name == 'postgresql'
    # sqlite (tests) — no SKIP LOCKED, single-process
    job = _select_query(types, now, for_update=pg).first()
    if job is None:
        return None
    job.status = 'running'
    job.claimed_at = utcnow()
    job.attempts += 1
    db.session.commit()
    return job


def complete(job, result):
    job.status = 'done'
    job.result = result
    job.finished_at = utcnow()
    db.session.commit()


def fail(job, error, transient=False, max_attempts=3):
    """Mark the job as failed. Requeues with backoff if transient and
    attempts<max_attempts; otherwise terminal 'failed'. First thing: clean
    up the handler's dirty/pending-rollback session (otherwise the commit
    here blows up).

    On the terminal branch, `notifications.push` (flush-only) is called —
    NO extra commit, this function's already-existing SINGLE commit
    persists both the job and the notification."""
    db.session.rollback()  # FIRST line — clean the dirty session, then reload the job
    job = db.session.get(Job, job.id)  # rollback expired the job; read fresh
    if job is None:
        return
    if transient and job.attempts < max_attempts:
        job.status = 'queued'
        job.available_at = utcnow() + timedelta(seconds=_backoff(job.attempts))
        job.result = {'last_error': str(error)[:500], 'retry': True}
    else:
        job.status = 'failed'
        job.result = {'error': str(error)[:500]}
        job.finished_at = utcnow()
        notify_job_failed(job)  # flushes; commit happens once, below
    db.session.commit()


def reap_stuck(timeout_seconds=1800):
    """Reclaim jobs stuck in 'running' longer than the timeout back to the
    queue (janitor). Doesn't touch attempts; returns the list of requeued
    jobs. `notifications.push` (flush-only) is called for each requeue —
    the SINGLE commit below persists all of them."""
    cutoff = utcnow() - timedelta(seconds=timeout_seconds)
    stuck = Job.query.filter(Job.status == 'running', Job.claimed_at < cutoff).all()
    for j in stuck:
        j.status = 'queued'
        j.available_at = None
        notify_job_stuck(j)
    db.session.commit()
    return stuck
