"""Read models for the dashboard sections of the browser UI (Tasks, Approvals, Schedule, AI Mode, Models, Memory, System).
Everything here is derived from real state (council.db, agno.db, PC state file, LM Studio, logs, systemd). Read-only, except two explicitly gated writes:
proactive config (Tasks view) and the AI Mode request file consumed by the audited poller."""
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request

import jobs
import known
import policy
import proactive as PRO
import window as W

STATE = os.environ.get("AGNO_STATE_DIR", "/var/lib/agno")
AGNO_DB = os.environ.get("AGNO_DB", f"{STATE}/agno.db")
MIRROR = os.environ.get("AGNO_VAULT_MIRROR", "/var/lib/agno-vault")
PCDIR = os.environ.get("AGNO_PCSTATE_DIR", "/run/agno-pcstate")
CTL = os.path.join(PCDIR, "control")
UNITS = ["agno-council", "agno-ui", "agno-web", "agno-ops", "agno-pcstate"]
TIMERS = ["agno-facts", "agno-vault-mirror", "agno-digest"]
TREND = os.path.join(STATE, "audit", "pc-trend.jsonl")
SECRETISH = re.compile(r"pass|secret|token|key|auth|cookie|credential", re.I)
LONG = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")


def redact(o):
    """redact by FIELD NAME (never by length alone), plus long opaque strings anywhere"""
    if isinstance(o, dict):
        return {k: ("[redacted]" if SECRETISH.search(str(k)) else redact(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(x) for x in o]
    if isinstance(o, str):
        return LONG.sub("[redacted-token]", o)
    return o


def _tail(path, n=40):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 64 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try: out.append(json.loads(ln))
        except ValueError: pass
    return out


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


# ---------------- Tasks ----------------
def tasks(sched):
    cfg = PRO.get_config()
    rows = []
    for j in jobs.recent(60):
        j = dict(j); j["duration_s"] = round(j["finished_at"] - j["started_at"]) if j.get("finished_at") and j.get("started_at") else None
        rows.append(j)
    return {"jobs": rows, "counts": jobs.counts(), "running": sched.running_job if sched else None, "config": cfg,
            "kinds": {k: {"title": v[0], "priority": v[1], "about": v[3]} for k, v in PRO.KINDS.items()},
            "known_state": {"containers": known.KNOWN_STOPPED, "units": known.KNOWN_FAILED_UNITS}}


def task(jid):
    j = jobs.get(jid)
    if j and j.get("report_path"):
        try: j["report"] = open(j["report_path"]).read()[:12000]
        except OSError: j["report"] = None
    if j: j.pop("prompt_full", None)
    return j


# ---------------- Approvals ----------------
def approvals():
    ps = jobs.proposals()
    audit = [r for r in _tail(os.path.join(STATE, "audit", "audit.jsonl"), 400) if r.get("event") == "proposal_decision"][-30:]
    return {"proposals": ps, "counts": {s: sum(1 for p in ps if p["status"] == s) for s in ("pending", "approved", "rejected")}, "decisions": audit,
            "note": "A decision is only recorded. The council in this version cannot execute anything, approved or not."}


# ---------------- Schedule ----------------
def schedule(sched, inference):
    pc = policy.read_pc_state()
    ok, why = policy.proactive_decision(inference, pc)
    nights = {}
    with jobs._con() as c:
        for r in c.execute("SELECT window_date, status, kind, COUNT(*) n FROM jobs WHERE window_date IS NOT NULL GROUP BY window_date, status, kind ORDER BY window_date DESC LIMIT 200"):
            n = nights.setdefault(r["window_date"], {"date": r["window_date"], "total": 0, "by_status": {}, "kinds": []})
            n["total"] += r["n"]; n["by_status"][r["status"]] = n["by_status"].get(r["status"], 0) + r["n"]; n["kinds"].append(r["kind"])
    wd = W.window_date()
    return {"window": {"now_london": W.now_london().strftime("%Y-%m-%d %H:%M %Z"), "in_window": W.in_window(), "next_start": W.next_window_start().isoformat(), "end": W.window_end().isoformat(), "start_time": "03:00", "end_time": "09:00"},
            "allowed": ok, "reasons": why, "checks": policy.evaluate(inference, pc), "pc_age_s": (pc or {}).get("age_s"),
            "caps": {"max_per_night": PRO.get_config()["max_per_night"], "runs_tonight": (sched.runs_in_window.get(wd, 0) if sched else 0), "tick_s": getattr(__import__("scheduler"), "TICK", 30)},
            "running": sched.running_job if sched else None, "queued": [j for j in jobs.recent(60) if j["status"] == "queued"][:12],
            "nights": sorted(nights.values(), key=lambda n: n["date"], reverse=True)[:14]}


# ---------------- AI Mode ----------------
def _read_json(path):
    try: return json.load(open(path))
    except (OSError, ValueError): return None


def aimode():
    pc = policy.read_pc_state()
    return {"pc": pc, "config": _read_json(os.path.join(PCDIR, "config.json")), "control": {"audit": _tail(os.path.join(CTL, "audit.jsonl"), 15), "last_result": _read_json(os.path.join(CTL, "result.json"))},
            "rules": ["The buttons here have the same authority as the tray icon: ON and OFF both work", "nothing ever turns AI Mode on by itself: only your click does", "every request is audited",
                      "the PC gets no new listening port: the request is applied by the audited poller over the existing SSH access, one fixed command"]}


def aimode_set(cmd, confirm):
    """council side of the audited path: validates, writes ONE request for the poller (which re-validates), waits for its result."""
    if cmd not in ("on", "off", "rogue", "main"):
        return 400, {"error": "cmd must be 'on', 'off', 'rogue' or 'main'"}
    if cmd in ("rogue", "main"):                               # model swap: the tray also refuses, this just gives a clear message first
        pc = policy.read_pc_state() or {}
        if pc.get("ai_mode") != "ON": return 409, {"ok": False, "msg": "Turn AI Mode on first: a model swap only works while AI Mode is on."}
        if pc.get("gaming"): return 409, {"ok": False, "msg": "A game is running; the model is not swapped while gaming."}
    if confirm is not True:
        return 400, {"error": "confirmation required"}
    rid = f"{int(time.time())}-{os.urandom(3).hex()}"
    try:
        os.makedirs(CTL, exist_ok=True)
        tmp = os.path.join(CTL, "request.tmp")
        with open(tmp, "w") as f:
            json.dump({"id": rid, "cmd": cmd, "ts": time.time()}, f)
        os.chmod(tmp, 0o660)                                   # the service umask is 0077: the poller (another user, same group) must be able to read it
        os.replace(tmp, os.path.join(CTL, "request.json"))
    except OSError as e:
        return 503, {"error": "control channel unavailable", "reason": type(e).__name__}
    for _ in range(40):                                   # up to ~20 s
        time.sleep(0.5)
        r = _read_json(os.path.join(CTL, "result.json"))
        if r and r.get("id") == rid:
            return (200 if r.get("ok") else 409), r
    return 504, {"error": "timeout", "reason": "the poller did not answer; the request will expire"}


# ---------------- Models ----------------
def models(llm_root):
    pc = policy.read_pc_state() or {}
    try:
        with urllib.request.urlopen(llm_root + "/api/v0/models", timeout=4) as r:
            data = json.load(r)["data"]
    except Exception as e:
        return {"ok": False, "reason": f"LM Studio unreachable through the tunnel ({type(e).__name__})", "models": [], "pc": pc}
    keep = ("id", "type", "state", "arch", "quantization", "publisher", "max_context_length", "loaded_context_length", "compatibility_type")
    ms = [{k: m.get(k) for k in keep} for m in data]
    return {"ok": True, "models": sorted(ms, key=lambda m: (m["state"] != "loaded", m["id"])), "pc": {k: pc.get(k) for k in ("model_loaded", "model", "context", "vram_used_mib", "vram_free_mib", "ram_available_mb", "commit_pct", "gaming", "ai_mode")},
            "controls": "read-only: loading is deliberately approved on the PC itself (tray > Load model asks Yes/No and refuses if a game runs or RAM < 3500 MB)"}


# ---------------- Memory ----------------
def memory():
    out = {"agno_db": {"present": os.path.exists(AGNO_DB)}, "conversations": {}, "vault_mirror": {}, "kv": [], "user_memories": {"enabled": True, "note": "Say \"remember ...\" in any chat and it is kept here and given to every later run. Nothing is saved unless you ask. Agno's automatic memory stays off: it costs an extra model request per run."}}
    try:
        import memory as _M
        out["user_memories"]["notes"] = _M.items()
    except Exception:
        out["user_memories"]["notes"] = []
    try:
        c = _ro(AGNO_DB)
        out["agno_db"].update({"sessions": c.execute("SELECT COUNT(*) FROM agno_sessions").fetchone()[0], "runs": c.execute("SELECT COUNT(*) FROM agno_runs").fetchone()[0],
                               "size_kb": os.path.getsize(AGNO_DB) // 1024, "tables": [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]})
        out["agno_db"]["top_sessions"] = [{"session_id": r[0], "type": r[1], "runs": r[2], "updated": r[3]} for r in c.execute(
            "SELECT s.session_id, s.session_type, (SELECT COUNT(*) FROM agno_runs r WHERE r.session_id=s.session_id), s.updated_at FROM agno_sessions s ORDER BY s.updated_at DESC LIMIT 12")]
        has_mem = any("memor" in t for t in out["agno_db"]["tables"])
        out["user_memories"]["table_present"] = has_mem
        if has_mem:
            t = [t for t in out["agno_db"]["tables"] if "memor" in t][0]; out["user_memories"]["rows"] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except Exception as e:
        out["agno_db"]["error"] = type(e).__name__
    try:
        with jobs._con() as c:
            out["conversations"] = {"conversations": c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], "messages": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0], "runs": c.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                                    "runs_by_status": {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM runs GROUP BY status")}}
            out["kv"] = [{"k": r["k"], "v": (r["v"] or "")[:90]} for r in c.execute("SELECT k, v FROM kv WHERE k NOT LIKE 'seeded:%' ORDER BY k LIMIT 60")]
            out["kv_seeded_flags"] = c.execute("SELECT COUNT(*) FROM kv WHERE k LIKE 'seeded:%'").fetchone()[0]
    except Exception as e:
        out["conversations"] = {"error": type(e).__name__}
    try:
        n, newest = 0, 0.0
        for dp, dn, fn in os.walk(MIRROR):
            for f in fn:
                if f.endswith(".md"):
                    n += 1; newest = max(newest, os.path.getmtime(os.path.join(dp, f)))
        out["vault_mirror"] = {"notes": n, "newest_note_mtime": newest, "path": MIRROR, "refresh": "every 10 minutes (agno-vault-mirror.timer)"}
    except OSError as e:
        out["vault_mirror"] = {"error": type(e).__name__}
    return out


# ---------------- System ----------------
def _sh(args, timeout=4):
    try: return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception: return ""


def services():
    st = _sh(["systemctl", "is-active"] + [u + ".service" for u in UNITS] + [t + ".timer" for t in TIMERS]).split()
    names = [u + ".service" for u in UNITS] + [t + ".timer" for t in TIMERS]
    out = [{"unit": n, "active": (st[i] if i < len(st) else "unknown")} for i, n in enumerate(names)]
    for t in TIMERS:
        sh = _sh(["systemctl", "show", t + ".timer", "-p", "NextElapseUSecRealtime", "-p", "LastTriggerUSec"])
        d = dict(l.split("=", 1) for l in sh.splitlines() if "=" in l)
        for o in out:
            if o["unit"] == t + ".timer": o["next"] = d.get("NextElapseUSecRealtime"); o["last"] = d.get("LastTriggerUSec")
    return out


def model_calls(n=80):
    rows = _tail(os.path.join(STATE, "audit", "model-calls.jsonl"), n)
    return [{"t": r.get("t_req"), "dur": r.get("dur"), "ptok": r.get("ptok"), "ctok": r.get("ctok"), "wait": r.get("wait"), "recovery": len(r.get("recovery") or [])} for r in rows if r.get("t_req")]


def system(gate_stats, inference):
    facts = _read_json(os.environ.get("AGNO_FACTS", "/var/lib/agno-facts/health.json"))
    du = {p: dict(zip(("total_gb", "used_gb", "free_gb"), [round(x / 1e9, 1) for x in shutil.disk_usage(p)])) for p in ("/", STATE) if os.path.exists(p)}
    return {"facts": facts, "assessment": known.assess_facts(facts) if facts else None, "services": services(), "gate": dict(gate_stats), "inference": inference,
            "calls": model_calls(), "pc_trend": _tail(TREND, 360), "disk": du,
            "tool_calls": redact(_tail(os.path.join(STATE, "audit", "tool-calls.jsonl"), 25)), "audit": redact(_tail(os.path.join(STATE, "audit", "audit.jsonl"), 25)),
            "ops_audit": redact(_tail("/var/lib/agno-ops/audit.jsonl", 15)), "web_audit": redact(_tail("/var/lib/agno-web/audit.jsonl", 10))}


# ---------------- PC trend sampler (so the System view has a real history) ----------------
def start_sampler(every=60, keep=1440):
    def loop():
        while True:
            try:
                pc = policy.read_pc_state()
                if pc and pc.get("reachable") is not False:
                    row = {"t": time.time(), **{k: pc.get(k) for k in ("ram_available_mb", "commit_pct", "vram_used_mib", "vram_free_mib")}, "ai_mode": pc.get("ai_mode"), "model_loaded": pc.get("model_loaded")}
                    os.makedirs(os.path.dirname(TREND), exist_ok=True)
                    with open(TREND, "a") as f: f.write(json.dumps(row) + "\n")
                    if os.path.getsize(TREND) > keep * 220:
                        lines = open(TREND).read().splitlines()[-keep:]
                        open(TREND + ".tmp", "w").write("\n".join(lines) + "\n"); os.replace(TREND + ".tmp", TREND)
            except Exception:
                pass
            time.sleep(every)
    threading.Thread(target=loop, daemon=True, name="pc-sampler").start()
