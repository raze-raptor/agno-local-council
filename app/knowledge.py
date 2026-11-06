"""Vault knowledge index: SQLite FTS5 (BM25, porter stemming) over the read-only vault mirror, chunked by heading. Stdlib only, no embedding model,
nothing leaves the box. Rebuilt when any note changed (checked at most every 5 minutes); search never writes to the vault."""
import os
import re
import sqlite3
import threading
import time

MIRROR = os.environ.get("AGNO_VAULT_MIRROR", "/var/lib/agno-vault")
DB = os.path.join(os.environ.get("AGNO_STATE_DIR", "/var/lib/agno"), "knowledge.db")
SKIP = {"Templates", ".stversions", ".obsidian", "raw"}
_L = threading.Lock()
_checked = 0.0


def _notes():
    for dp, dn, fn in os.walk(MIRROR):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fn:
            if f.endswith(".md"): yield os.path.join(dp, f)


def _chunks(txt):
    txt = re.sub(r"^---\n.*?\n---\n", "", txt, count=1, flags=re.S)
    parts = re.split(r"(?m)^(?=#{1,3} )", txt)
    for p in parts:
        p = p.strip()
        while p:
            yield p[:1200]; p = p[1200:].strip()


def _sig():
    n = m = 0
    for p in _notes():
        try: m = max(m, os.stat(p).st_mtime); n += 1
        except OSError: pass
    return n, int(m)


def reindex(force=False):
    global _checked
    with _L:
        con = sqlite3.connect(DB, timeout=10)
        try:
            con.execute("create table if not exists meta(k text primary key, v text)")
            con.execute("create virtual table if not exists chunks using fts5(note, path, body, tokenize='porter unicode61')")
            n, m = _sig()
            cur = con.execute("select v from meta where k='sig'").fetchone()
            if not force and cur and cur[0] == f"{n}:{m}":
                _checked = time.time(); return False
            con.execute("delete from chunks")
            for p in _notes():
                try: txt = open(p, encoding="utf-8", errors="replace").read()
                except OSError: continue
                name = os.path.basename(p)[:-3]; rel = os.path.relpath(p, MIRROR)
                for c in _chunks(txt): con.execute("insert into chunks values(?,?,?)", (name, rel, c))
            con.execute("insert or replace into meta values('sig',?)", (f"{n}:{m}",))
            con.execute("insert or replace into meta values('built',?)", (str(int(time.time())),))
            con.commit(); _checked = time.time(); return True
        finally:
            con.close()


def _q(query):
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9._-]{1,}", query.lower()) if len(w) > 2][:8]
    return " OR ".join('"%s"' % w.replace('"', "") for w in words)


def search(query, k=5):
    if time.time() - _checked > 300: reindex()
    q = _q(query)
    if not q: return []
    con = sqlite3.connect(DB, timeout=10)
    try:
        rows = con.execute("select note, path, snippet(chunks, 2, '', '', ' ... ', 28), bm25(chunks, 8.0, 1.0, 1.0) s from chunks where chunks match ? order by s limit 30", (q,)).fetchall()
    finally:
        con.close()
    seen, out = set(), []
    for note, path, snip, s in rows:                       # best chunk per note
        if note in seen: continue
        seen.add(note); out.append((note, path, re.sub(r"\s+", " ", snip), s))
        if len(out) >= k: break
    return out


def stats():
    try:
        con = sqlite3.connect(DB, timeout=5)
        n = con.execute("select count(*) from chunks").fetchone()[0]
        notes = con.execute("select count(distinct note) from chunks").fetchone()[0]
        built = con.execute("select v from meta where k='built'").fetchone()
        con.close()
        return {"engine": "SQLite FTS5 (BM25, porter)", "chunks": n, "notes": notes, "built": int(built[0]) if built else None, "embeddings": False}
    except Exception as e:
        return {"engine": "SQLite FTS5", "error": str(e)[:80], "chunks": 0, "notes": 0}
