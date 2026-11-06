"""Conversation persistence for the browser UI (same council.db as jobs). conversation id == AG-UI threadId == Agno session id.
A run's AG-UI events are stored (text deltas coalesced; reasoning never present) so the UI replays history through the SAME
reducer it uses live."""
import json
import os
import sqlite3
import threading
import time
import uuid

import jobs

_L = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT 'New conversation', created REAL NOT NULL, updated REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, run_id TEXT, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, conv_id TEXT NOT NULL, status TEXT NOT NULL, started REAL NOT NULL, finished REAL, events_json TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, id);
CREATE INDEX IF NOT EXISTS idx_runs_conv ON runs(conv_id, started);
"""


def init():
    with _L, jobs._con() as c:
        c.executescript(SCHEMA)
        # runs left 'running' by a restart can never finish
        c.execute("UPDATE runs SET status='interrupted', finished=? WHERE status='running'", (time.time(),))


def create(cid=None, title="New conversation"):
    cid = cid or uuid.uuid4().hex[:16]
    now = time.time()
    with _L, jobs._con() as c:
        c.execute("INSERT OR IGNORE INTO conversations(id,title,created,updated) VALUES(?,?,?,?)", (cid, title, now, now))
    return cid


def exists(cid):
    with jobs._con() as c:
        return c.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone() is not None


def listing(limit=100):
    with jobs._con() as c:
        rows = c.execute("""SELECT c.id, c.title, c.updated, c.created,
                (SELECT status FROM runs r WHERE r.conv_id=c.id ORDER BY started DESC LIMIT 1) AS last_status,
                (SELECT COUNT(*) FROM messages m WHERE m.conv_id=c.id) AS n
                FROM conversations c WHERE archived=0 ORDER BY updated DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get(cid):
    with jobs._con() as c:
        conv = c.execute("SELECT id,title,created,updated FROM conversations WHERE id=? AND archived=0", (cid,)).fetchone()
        if not conv:
            return None
        msgs = [dict(r) for r in c.execute("SELECT id,role,content,run_id,ts FROM messages WHERE conv_id=? ORDER BY id", (cid,))]
        runs = {}
        for r in c.execute("SELECT run_id,status,started,finished,events_json FROM runs WHERE conv_id=? ORDER BY started", (cid,)):
            d = dict(r); d["events"] = json.loads(d.pop("events_json")); runs[d["run_id"]] = d
    return {"conversation": dict(conv), "messages": msgs, "runs": runs}


def rename(cid, title):
    with _L, jobs._con() as c:
        return c.execute("UPDATE conversations SET title=?, updated=? WHERE id=? AND archived=0", (title.strip()[:120] or "Untitled", time.time(), cid)).rowcount > 0


def archive(cid):
    with _L, jobs._con() as c:
        return c.execute("UPDATE conversations SET archived=1 WHERE id=?", (cid,)).rowcount > 0


def run_events(run_id):
    with jobs._con() as c:
        r = c.execute("SELECT events_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
    try: return json.loads(r["events_json"]) if r and r["events_json"] else []
    except ValueError: return []


def _runs_of(cid):
    with jobs._con() as c:
        return c.execute("SELECT run_id, events_json FROM runs WHERE conv_id=?", (cid,)).fetchall()


def offered(cid, run_id):
    """did THIS conversation's run end with a recorded rogue_offer (the council declined)? The browser cannot forge this: it is read from the stored events."""
    for r in _runs_of(cid):
        if r["run_id"] == run_id:
            try: return any(e.get("type") == "CUSTOM" and e.get("name") == "rogue_offer" for e in json.loads(r["events_json"] or "[]"))
            except ValueError: return False
    return False


def handoff_used(cid, run_id):
    for r in _runs_of(cid):
        try:
            if any(e.get("type") == "CUSTOM" and e.get("name") == "handoff_from" and e.get("value") == run_id for e in json.loads(r["events_json"] or "[]")): return True
        except ValueError: pass
    return False


def user_prompt(cid, run_id):
    with jobs._con() as c:
        r = c.execute("SELECT content FROM messages WHERE conv_id=? AND run_id=? AND role='user' ORDER BY id LIMIT 1", (cid, run_id)).fetchone()
    return r["content"] if r else None


def active_run(cid):
    with jobs._con() as c:
        r = c.execute("SELECT run_id FROM runs WHERE conv_id=? AND status='running'", (cid,)).fetchone()
    return r["run_id"] if r else None


def begin_run(cid, run_id, user_text):
    now = time.time()
    with _L, jobs._con() as c:
        row = c.execute("SELECT COUNT(*) n FROM messages WHERE conv_id=?", (cid,)).fetchone()
        if row["n"] == 0:
            c.execute("UPDATE conversations SET title=? WHERE id=?", (" ".join(user_text.split())[:60] or "New conversation", cid))
        c.execute("INSERT INTO messages(conv_id,role,content,run_id,ts) VALUES(?,?,?,?,?)", (cid, "user", user_text, run_id, now))
        c.execute("INSERT OR REPLACE INTO runs(run_id,conv_id,status,started) VALUES(?,?,?,?)", (run_id, cid, "running", now))
        c.execute("UPDATE conversations SET updated=? WHERE id=?", (now, cid))


def coalesce(events):
    """merge consecutive TEXT_MESSAGE_CONTENT deltas of one message (storage only; live clients got the fine-grained stream)"""
    out = []
    for e in events:
        if out and e.get("type") == "TEXT_MESSAGE_CONTENT" and out[-1].get("type") == "TEXT_MESSAGE_CONTENT" and out[-1].get("messageId") == e.get("messageId"):
            out[-1]["delta"] += e["delta"]
        else:
            out.append(dict(e))
    return out


def finish_run(cid, run_id, status, events, final_text):
    now = time.time()
    with _L, jobs._con() as c:
        c.execute("UPDATE runs SET status=?, finished=?, events_json=? WHERE run_id=?", (status, now, json.dumps(coalesce(events)), run_id))
        if final_text:
            c.execute("INSERT INTO messages(conv_id,role,content,run_id,ts) VALUES(?,?,?,?,?)", (cid, "assistant", final_text[:20000], run_id, now))
        c.execute("UPDATE conversations SET updated=? WHERE id=?", (now, cid))


def prior_user(cid):
    """the user message before the one being answered now (begin_run already stored the current one)"""
    with _L, jobs._con() as c:
        r = c.execute("SELECT content FROM messages WHERE conv_id=? AND role='user' ORDER BY id DESC LIMIT 1 OFFSET 1", (cid,)).fetchone()
    return r[0] if r else ""
