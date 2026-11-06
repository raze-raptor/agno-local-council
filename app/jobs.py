"""Persistent job queue + history for unattended work (SQLite, separate from Agno's own session DB).

A job is a prompt for the council plus bookkeeping. Statuses: queued -> running -> done | failed | cancelled;
a running job that is interrupted by the policy (window closed, AI Mode off, gaming, low RAM...) goes back to 'queued'
with a note and its Agno session id kept, so the next eligible window continues it instead of starting over.
Nothing here ever deletes a row.
"""
import json
import os
import sqlite3
import threading
import time

DB_FILE = os.environ.get("AGNO_JOBS_DB", "/var/lib/agno/council.db")
MAX_ATTEMPTS = 3
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL, prompt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 50,
  created_at REAL NOT NULL, window_date TEXT, started_at REAL, finished_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
  session_id TEXT, report_path TEXT, note TEXT, source TEXT NOT NULL DEFAULT 'auto');
CREATE TABLE IF NOT EXISTS job_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, ts REAL NOT NULL, event TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS proposals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, host TEXT, what TEXT, command TEXT, why TEXT, status TEXT NOT NULL DEFAULT 'pending', decided_at REAL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority DESC, id);
"""


def _con():
    c = sqlite3.connect(DB_FILE, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with _LOCK, _con() as c:
        c.executescript(SCHEMA)
        # a crash/restart while a job was 'running' must not strand it
        n = c.execute("UPDATE jobs SET status='queued', note='recovered after restart' WHERE status='running'").rowcount
        if n:
            c.execute("INSERT INTO job_events(job_id, ts, event, detail) SELECT id, ?, 'recovered', 'service restarted mid-run' FROM jobs WHERE note='recovered after restart' AND status='queued'", (time.time(),))
    return n


def event(job_id, ev, detail=""):
    with _LOCK, _con() as c:
        c.execute("INSERT INTO job_events(job_id, ts, event, detail) VALUES(?,?,?,?)", (job_id, time.time(), ev, str(detail)[:500]))


def enqueue(kind, title, prompt, priority=50, window_date=None, source="auto"):
    with _LOCK, _con() as c:
        cur = c.execute("INSERT INTO jobs(kind,title,prompt,priority,created_at,window_date,source) VALUES(?,?,?,?,?,?,?)",
                        (kind, title, prompt, priority, time.time(), window_date, source))
        jid = cur.lastrowid
        c.execute("INSERT INTO job_events(job_id, ts, event, detail) VALUES(?,?,?,?)", (jid, time.time(), "queued", f"{kind} ({source})"))
    return jid


def exists_for_window(kind, window_date):
    with _con() as c:
        return c.execute("SELECT 1 FROM jobs WHERE kind=? AND window_date=? AND source='auto'", (kind, window_date)).fetchone() is not None


def unfinished(kind):
    with _con() as c:
        return [dict(r) for r in c.execute("SELECT id, window_date FROM jobs WHERE kind=? AND status IN ('queued','running') AND source='auto'", (kind,))]


def supersede(kind, keep_window):
    """a stale briefing from an earlier night is replaced by tonight's fresh one (kept in history as cancelled, never deleted)"""
    n = 0
    for j in unfinished(kind):
        if j["window_date"] != keep_window:
            mark(j["id"], "cancelled", finished_at=time.time(), note="superseded by a newer " + kind); event(j["id"], "superseded", keep_window); n += 1
    return n


def next_queued():
    with _con() as c:
        r = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY priority DESC, id LIMIT 1").fetchone()
    return dict(r) if r else None


def mark(job_id, status, **fields):
    sets = ["status=?"]; vals = [status]
    for k, v in fields.items():
        sets.append(f"{k}=?"); vals.append(v)
    vals.append(job_id)
    with _LOCK, _con() as c:
        c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)


def start(job_id):
    with _LOCK, _con() as c:
        c.execute("UPDATE jobs SET status='running', started_at=?, attempts=attempts+1, session_id=COALESCE(session_id, ?) WHERE id=?",
                  (time.time(), f"job-{job_id}", job_id))
    event(job_id, "started")
    return get(job_id)


def get(job_id):
    with _con() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        ev = [dict(x) for x in c.execute("SELECT ts,event,detail FROM job_events WHERE job_id=? ORDER BY id", (job_id,))]
    if not r:
        return None
    d = dict(r); d["events"] = ev
    return d


def recent(limit=30):
    with _con() as c:
        return [dict(r) for r in c.execute("SELECT id,kind,title,status,priority,created_at,started_at,finished_at,attempts,note,source,report_path,window_date FROM jobs ORDER BY id DESC LIMIT ?", (limit,))]


def counts():
    with _con() as c:
        return {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}


def cancel(job_id):
    j = get(job_id)
    if not j or j["status"] in ("done", "failed", "cancelled"):
        return False
    mark(job_id, "cancelled", finished_at=time.time(), note="cancelled by user")
    event(job_id, "cancelled")
    return True


def kv_get(k, default=None):
    with _con() as c:
        r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def kv_set(k, v):
    with _LOCK, _con() as c:
        c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def add_proposal(host, what, command, why):
    with _LOCK, _con() as c:
        return c.execute("INSERT INTO proposals(ts,host,what,command,why) VALUES(?,?,?,?,?)", (time.time(), host, what, command, why)).lastrowid


def proposals(status=None):
    with _con() as c:
        q, a = ("SELECT * FROM proposals WHERE status=? ORDER BY id DESC LIMIT 50", (status,)) if status else ("SELECT * FROM proposals ORDER BY id DESC LIMIT 50", ())
        return [dict(r) for r in c.execute(q, a)]


def decide_proposal(pid, status):
    """Records the owner's decision only. Approval does NOT execute anything."""
    with _LOCK, _con() as c:
        return c.execute("UPDATE proposals SET status=?, decided_at=? WHERE id=? AND status='pending'", (status, time.time(), pid)).rowcount > 0
