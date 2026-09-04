# -*- coding: utf-8 -*-
"""Job runner: everything long-running is a job (DM5).

- jobs + events live in SQLite (task state)
- progress is emitted as events with a global autoincrement seq
- WS polls events by seq > last -> incremental catch-up (reconnect-safe)
- checkpoint = progress_current persisted transactionally per unit
  (DM7: same SQLite tx as the data write)
- cooperative control: pause/delete set jobs.cancel_flag; worker loops call
  poll_control() at chunk boundaries and react (JobPaused / JobCancelled)
"""
import threading
import traceback

from . import db

_lock = threading.RLock()
_threads: dict[int, threading.Thread] = {}
_emit_lock = threading.RLock()  # serializes writes to the shared conn (events)

_threadlocal = threading.local()


class JobPaused(Exception):
    """Raised inside a worker loop at a chunk boundary: status already 'paused'."""


class JobCancelled(Exception):
    """Raised inside a worker loop: job row + its events already deleted."""


def current_job_id() -> int | None:
    return getattr(_threadlocal, "job_id", None)


def set_current_job(job_id: int | None) -> None:
    """Bind THIS thread to a job id. Worker threads of a concurrent job call
    this before extracting, so llm.* telemetry inside llm.chat() still
    attributes to the right job (thread-local, other threads unaffected)."""
    _threadlocal.job_id = job_id


# ---------------- worker telemetry buffer ----------------
# Concurrent extraction workers must NEVER touch the shared sqlite
# connection: interleaved write transactions from several threads on one
# connection corrupt transaction boundaries (integrity errors, even native
# crashes when a test closes the conn under an abandoned worker). Workers
# buffer their llm.* telemetry here instead; the scheduler thread is the
# single writer and drains the buffer into the events table.
_telemetry_buf: list[tuple[int, str, dict]] = []
_telemetry_lock = threading.Lock()


def set_telemetry_buffer(mode: bool) -> None:
    """Mark THIS thread as a buffering worker (no direct DB writes)."""
    _threadlocal.telemetry_buffer = mode


def telemetry_buffered() -> bool:
    return getattr(_threadlocal, "telemetry_buffer", False)


def buffered_telemetry(job_id: int, type_: str, payload: dict) -> None:
    with _telemetry_lock:
        _telemetry_buf.append((job_id, type_, payload))


def drain_telemetry(conn) -> None:
    """Scheduler thread only: flush buffered worker telemetry to the DB."""
    with _telemetry_lock:
        items, _telemetry_buf[:] = _telemetry_buf[:], []
    for job_id, type_, payload in items:
        emit(conn, job_id, type_, payload)


def create_job(conn, kind: str, total: int, detail: str = "",
               ref_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO jobs(kind, status, progress_current, progress_total, detail,"
        " ref_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (kind, "running", 0, total, detail, ref_id, db.now(), db.now()))
    conn.commit()
    job_id = cur.lastrowid
    emit(conn, job_id, "job.started", {"kind": kind, "total": total, "detail": detail})
    return job_id


def emit(conn, job_id: int | None, type_: str, payload: dict) -> None:
    # Serialize event writes: with concurrent workers, llm.* telemetry and
    # the scheduler both emit through the SHARED db.get_conn() singleton -
    # the lock keeps INSERT+commit atomic so an event write never lands in
    # the middle of another thread's transaction on the same connection.
    with _emit_lock:
        conn.execute("INSERT INTO events(job_id, type, payload, ts) VALUES(?,?,?,?)",
                     (job_id, type_, json_dumps(payload), db.now()))
        conn.commit()


def update_progress(conn, job_id: int, current: int, total: int | None = None) -> None:
    if total is not None:
        conn.execute("UPDATE jobs SET progress_current=?, progress_total=?, updated_at=?"
                     " WHERE id=?", (current, total, db.now(), job_id))
    else:
        conn.execute("UPDATE jobs SET progress_current=?, updated_at=? WHERE id=?",
                     (current, db.now(), job_id))
    conn.commit()


def finish_job(conn, job_id: int, ok: bool, error: str | None = None) -> None:
    conn.execute("UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?",
                 ("done" if ok else "failed", error, db.now(), job_id))
    conn.commit()
    emit(conn, job_id, "job.finished" if ok else "job.failed", {"error": error})


# ---------------- cooperative control (pause / resume / delete) ----------------

def request_pause(conn, job_id: int) -> bool:
    """Pause a running job at its next chunk boundary."""
    cur = conn.execute(
        "UPDATE jobs SET cancel_flag=1, updated_at=? WHERE id=? AND status='running'",
        (db.now(), job_id))
    conn.commit()
    return cur.rowcount > 0


def auto_pause(conn, job_id: int, reason: str) -> None:
    """Worker-side pause with a user-visible reason (e.g. LLM unreachable).

    Sets status='paused' + error on the job row so the task list shows what
    went wrong; the worker then unwinds with JobPaused (same semantics as a
    manual pause - partial doc keeps its chunks and is re-run on resume)."""
    conn.execute("UPDATE jobs SET status='paused', cancel_flag=0, error=?,"
                 " updated_at=? WHERE id=?", (reason, db.now(), job_id))
    conn.commit()
    emit(conn, job_id, "job.autopaused",
         {"reason": reason, "progress": row_status(job_id, conn)})


def request_delete(conn, job_id: int) -> str:
    """Delete a job. Running jobs stop at next boundary then self-delete;
    finished/paused jobs are removed immediately. Returns action taken."""
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return "missing"
    if row["status"] == "running":
        conn.execute("UPDATE jobs SET cancel_flag=2, updated_at=? WHERE id=?",
                     (db.now(), job_id))
        conn.commit()
        return "deleting"  # worker cleans up at next boundary
    _purge(conn, job_id)
    return "deleted"


def _purge(conn, job_id: int) -> None:
    emit(conn, None, "job.deleted", {"id": job_id})
    conn.execute("DELETE FROM events WHERE job_id=?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.commit()


def resume_job(conn, job_id: int) -> bool:
    """Resume a paused/cancelled/failed job: same row, re-run.

    progress_current is KEPT as the checkpoint (DM7) - each worker decides
    how to skip already-done units:
    - ingest: skips unchanged docs by content hash, re-runs incomplete docs
    - ontology: skips chunks with index <= progress_current (no re-LLM)
    - repair: re-scans fallback chunks (self-idempotent)"""
    cur = conn.execute(
        "UPDATE jobs SET status='running', cancel_flag=0, error=NULL,"
        " updated_at=? WHERE id=? AND status IN ('paused','cancelled','failed')",
        (db.now(), job_id))
    conn.commit()
    if cur.rowcount == 0:
        return False
    emit(conn, job_id, "job.resumed", {})
    return True


def poll_control(conn, job_id: int) -> None:
    """Worker calls this at every chunk boundary. Raises JobPaused/JobCancelled
    after the control action has been applied (status/event/rows)."""
    row = conn.execute(
        "SELECT status, cancel_flag FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:  # row was purged underneath us (should not happen)
        raise JobCancelled()
    flag = row["cancel_flag"] or 0
    if flag == 1:
        conn.execute("UPDATE jobs SET status='paused', cancel_flag=0, updated_at=?"
                     " WHERE id=?", (db.now(), job_id))
        conn.commit()
        emit(conn, job_id, "job.paused", {"progress": row_status(job_id, conn)})
        raise JobPaused()
    if flag == 2:
        conn.execute("UPDATE jobs SET status='cancelled', updated_at=? WHERE id=?",
                     (db.now(), job_id))
        conn.commit()
        emit(conn, job_id, "job.cancelled", {})
        _purge(conn, job_id)
        raise JobCancelled()


def row_status(job_id: int, conn) -> dict:
    r = conn.execute("SELECT progress_current, progress_total FROM jobs WHERE id=?",
                     (job_id,)).fetchone()
    if r is None:
        return {}
    return {"current": r["progress_current"], "total": r["progress_total"]}


def run_in_background(job_id: int, fn, *args) -> threading.Thread:
    """Run fn(conn, job_id, *args) in a daemon thread with its own conn."""
    def _wrapper():
        set_current_job(job_id)
        conn = db.get_conn()
        try:
            fn(conn, job_id, *args)
        except (JobPaused, JobCancelled):
            pass  # status already set / rows already cleaned by poll_control
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            try:
                row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row is not None:  # row may be gone if delete raced the failure
                    finish_job(conn, job_id, ok=False, error=f"{type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            set_current_job(None)

    t = threading.Thread(target=_wrapper, daemon=True, name=f"job-{job_id}")
    with _lock:
        _threads[job_id] = t
    t.start()
    return t


def active_job(conn, kind: str) -> dict | None:
    """A running/paused job of this kind, if any (concurrency guard)."""
    row = conn.execute(
        "SELECT * FROM jobs WHERE kind=? AND status IN ('running','paused')"
        " ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    return job_to_dict(row) if row else None


def active_job_in_set(conn, kinds: set[str] | list[str]) -> dict | None:
    """Mutex gate across a SET of job kinds: returns the first running/paused
    job of any kind in `kinds`, or None. Used to enforce "only one of these
    three task families may run at a time" (ingest / repair / ontology)."""
    if not kinds:
        return None
    placeholders = ",".join("?" * len(kinds))
    row = conn.execute(
        f"SELECT * FROM jobs WHERE kind IN ({placeholders})"
        " AND status IN ('running','paused')"
        " ORDER BY id DESC LIMIT 1",
        tuple(kinds)).fetchone()
    return job_to_dict(row) if row else None


def latest_resumable_job(conn, kind: str) -> dict | None:
    """Most recent ontology-style job that the user can RESUME
    (paused / failed / cancelled). None if there is no such row.

    Used by the ontology-extract button: if a paused/failed ontology job
    exists, the click is treated as "resume" rather than "create new" — so
    re-running doesn't double-process already-seen chunks.
    """
    row = conn.execute(
        "SELECT * FROM jobs WHERE kind=? AND status IN"
        " ('paused','failed','cancelled')"
        " ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    return job_to_dict(row) if row else None


def recover_stale_jobs(conn, started_at: float | None = None) -> int:
    """Power-cut recovery: any job left 'running' from a previous process
    is marked failed (progress is persisted; user re-runs).

    A running row is stale iff it was created before `started_at` (this
    process's start time): at startup the current process has not created
    any job yet, so every 'running' row older than it can only belong to a
    killed previous process. Using creation time — not a 60s updated_at
    heuristic — closes the gap where a job killed within 60s of a restart
    was skipped and left a permanent zombie (which then blocks its kind via
    the `active_job` concurrency guard). Returns count."""
    if started_at is None:
        started_at = db.now()
    rows = conn.execute(
        "SELECT id, kind FROM jobs WHERE status='running' AND created_at < ?",
        (started_at,)).fetchall()
    for r in rows:
        if r["kind"] == "benchmark":
            # link table mirrors the job status; without this the card keeps
            # showing "running" and polls forever (run_benchmark never got a
            # chance to write its own failure before the process died)
            conn.execute(
                "UPDATE persona_benchmarks SET status='failed',"
                " error='interrupted (service restart)'"
                " WHERE job_id=? AND status='running'", (r["id"],))
        finish_job(conn, r["id"], ok=False, error="interrupted (service restart)")
    return len(rows)


def job_to_dict(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "status": row["status"],
            "progress_current": row["progress_current"],
            "progress_total": row["progress_total"], "detail": row["detail"],
            "error": row["error"]}


def events_since(conn, since_seq: int, limit: int = 500,
                 job_id: int | None = None) -> list[dict]:
    """Events after seq (ASC). job_id filter + DESC for job detail panels.

    `payload` lives in JSONB; psycopg3 hands it back as a Python dict/list
    already. The shim & maybe_jsonb helper make this lazy so legacy rows
    written as TEXT JSON are also decoded transparently."""
    from .jsonb import maybe_jsonb
    if job_id is not None:
        rows = conn.execute(
            "SELECT seq, job_id, type, payload, ts FROM events WHERE job_id=?"
            " ORDER BY seq DESC LIMIT ?", (job_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT seq, job_id, type, payload, ts FROM events WHERE seq > ?"
            " ORDER BY seq ASC LIMIT ?", (since_seq, limit)).fetchall()
    out = []
    for r in rows:
        payload = maybe_jsonb(r["payload"]) or {}
        out.append({"seq": r["seq"], "job_id": r["job_id"], "type": r["type"],
                    "payload": payload, "ts": r["ts"]})
    return out


def json_dumps(payload) -> str:
    import json as _json
    return _json.dumps(payload, ensure_ascii=False)
