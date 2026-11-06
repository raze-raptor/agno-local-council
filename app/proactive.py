"""Proactive job kinds. Each kind = a DETERMINISTIC collector (python, no model) that decides whether there is anything worth doing and builds
the facts; the council (LLM) only interprets them inside the 03:00-09:00 window. Nothing here changes any system: findings and proposals only.

kinds: health_summary, service_watch, morning_briefing, docs_gaps, project_status, research_digest, vault_review.
Config (per-kind enable, research topics, per-night cap) lives in jobs.kv 'proactive_config' and is edited from the Tasks view."""
import hashlib
import json
import os
import re
import time
from datetime import date, datetime

import jobs
import known
import vaultlint
import window as W

MIRROR = os.environ.get("AGNO_VAULT_MIRROR", "/var/lib/agno-vault")
FACTS = os.environ.get("AGNO_FACTS", "/var/lib/agno-facts/health.json")
KINDS = {  # kind: (title, priority, default_enabled, what it is for)
    "health_summary":   ("Health briefing", 80, True, "daily server1 health briefing with deterministic severity (old exited containers are not 'high')"),
    "service_watch":    ("Service watch", 75, True, "new failed units / containers on server1 and server2 compared with the last snapshot"),
    "vault_review":     ("Vault review", 40, True, "obsidian vault lint: broken links, missing or stale last_verified"),
    "docs_gaps":        ("Docs gaps", 35, True, "oldest-verified infrastructure notes and which to re-check first (weekly)"),
    "project_status":   ("Project status", 30, True, "open items from the Outstanding Decisions / Open Questions notes (weekly or when they change)"),
    "research_digest":  ("Research digest", 25, True, "one standing topic per night through the Researcher with sources (topics edited in Tasks)"),
    "morning_briefing": ("Morning briefing", 10, True, "overnight summary: what ran, health, server2 facts, backup status (runs last)"),
}
DEFAULT_CFG = {"kinds": {k: v[2] for k, v in KINDS.items()}, "topics": [], "max_per_night": 6}
RESEARCH_DAYS = 6          # a topic is researched at most once per 6 days
WEEKLY_DAYS = 6


# ---------------- config ----------------
def get_config():
    try:
        c = json.loads(jobs.kv_get("proactive_config") or "{}")
    except ValueError:
        c = {}
    out = {"kinds": dict(DEFAULT_CFG["kinds"]), "topics": [], "max_per_night": DEFAULT_CFG["max_per_night"]}
    out["kinds"].update({k: bool(v) for k, v in (c.get("kinds") or {}).items() if k in KINDS})
    out["topics"] = [t for t in (c.get("topics") or []) if isinstance(t, str)][:8]
    mp = c.get("max_per_night")
    out["max_per_night"] = mp if isinstance(mp, int) and 1 <= mp <= 8 else DEFAULT_CFG["max_per_night"]
    return out


def set_config(body):
    """validate and store; returns (ok, config_or_error)"""
    cur = get_config()
    if "kinds" in body:
        if not isinstance(body["kinds"], dict) or any(k not in KINDS or not isinstance(v, bool) for k, v in body["kinds"].items()):
            return False, "kinds must map known job kinds to true/false"
        cur["kinds"].update(body["kinds"])
    if "topics" in body:
        t = body["topics"]
        if not isinstance(t, list) or len(t) > 8:
            return False, "topics must be a list of at most 8 strings"
        clean = []
        for x in t:
            if not isinstance(x, str):
                return False, "each topic must be text"
            x = re.sub(r"[\x00-\x1f\x7f]", " ", x).strip()
            if not 5 <= len(x) <= 120:
                return False, "each topic must be 5-120 characters"
            if x.lower() not in [c.lower() for c in clean]:
                clean.append(x)
        cur["topics"] = clean
    if "max_per_night" in body:
        mp = body["max_per_night"]
        if not isinstance(mp, int) or isinstance(mp, bool) or not 1 <= mp <= 8:
            return False, "max_per_night must be a whole number from 1 to 8"
        cur["max_per_night"] = mp
    jobs.kv_set("proactive_config", json.dumps(cur))
    return True, cur


# ---------------- helpers ----------------
def _digest(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _days_since(kv_key, now_ts):
    try:
        return (now_ts - float(jobs.kv_get(kv_key) or 0)) / 86400
    except ValueError:
        return 1e9


def _load_facts():
    try:
        return json.load(open(FACTS))
    except (OSError, ValueError):
        return None


def parse_units(out):
    names = []
    for ln in (out or "").splitlines():
        m = re.match(r"^[\s●*]*([A-Za-z0-9@:_.\\-]+\.(?:service|timer|mount|socket|target|path))\b", ln)
        if m: names.append(m.group(1))
    return sorted(set(names))


def parse_docker_ps(out):
    """-> [(name, status)] for containers that are NOT running (Up)"""
    rows = []
    for ln in (out or "").splitlines():
        parts = [p.strip() for p in ln.split("\t")] if "\t" in ln else re.split(r"\s{2,}", ln.strip())
        if len(parts) < 2 or parts[0].upper() in ("NAMES", "CHECK") or parts[0].startswith("CHECK "):
            continue
        if not parts[1].startswith("Up"):
            rows.append((parts[0], parts[1]))
    return sorted(rows)


def snapshot(ask):
    """current problem set on both hosts (via the read-only ops broker)"""
    snap = {}
    for host in ("server1", "server2"):
        u = ask(host=host, check="failed_units", arg=""); c = ask(host=host, check="docker_ps", arg="")
        snap[host] = {"ok": u.get("rc") == 0 and c.get("rc") == 0, "units": parse_units(u.get("output")), "containers": parse_docker_ps(c.get("output"))}
    return snap


def classify_snapshot(snap):
    out = []
    for host, s in snap.items():
        if not s["ok"]:
            out.append({"host": host, "sev": "medium", "what": "could not read this host through the ops broker", "why": "check unavailable"}); continue
        for u in s["units"]:
            sev, why = known.classify_unit(u); out.append({"host": host, "sev": sev, "what": f"failed unit {u}", "why": why})
        for n, st in s["containers"]:
            sev, why = known.classify_container(n, st); out.append({"host": host, "sev": sev, "what": f"container {n} ({st})", "why": why})
    return out


# ---------------- collectors: each returns (title, prompt, priority, digest) or None ----------------
RULES = ("SEVERITY RULES (already applied by a script; do not change them): 'info' = documented and expected, mention in one line at most; 'low' = old or probably intentional; "
         "'medium' = needs a look soon; 'high' = needs the owner now. An old exited container is never high. Do NOT recommend restarting, deleting or changing anything yourself; "
         "if something needs a change, say 'propose' and describe it. Do not invent numbers. Never claim you checked anything.")


def collect_health(wd, now_ts, ask=None):
    facts = _load_facts()
    if not facts:
        return None
    a = known.assess_facts(facts)
    body = {"overall": a["overall"], "findings": a["findings"], "documented_known_state": a["known"], "raw_facts": {k: v for k, v in facts.items() if k in ("uptime_h", "load_1_5_15", "mem", "disk_root", "backup", "containers_total")}}
    prompt = (f"Daily system health briefing for the owner, written from deterministic facts collected by a script on server1. Overall status per the script: {a['overall'].upper()}.\n"
              "Write at most 120 words: one line of overall status, then only the findings that are not 'info' (severity, one concrete next step each), then anything that needs the owner's decision. "
              "If there are no findings above info say 'all calm' and nothing else.\n" + RULES + "\nASSESSMENT:\n" + json.dumps(body, indent=0)[:4500])
    return f"Health briefing {wd}", prompt, KINDS["health_summary"][1], None


def collect_service_watch(wd, now_ts, ask=None):
    if ask is None:
        return None
    snap = snapshot(ask)
    if not all(h["ok"] for h in snap.values()):
        return None                                   # a host could not be read: never diff against a half snapshot
    prev = None
    try:
        prev = json.loads(jobs.kv_get("service_snapshot") or "null")
    except ValueError:
        pass
    jobs.kv_set("service_snapshot", json.dumps({"ts": now_ts, "snap": snap}))
    if prev is None:
        return None                                   # first night: baseline only
    old = {(h, x) for h, s in prev["snap"].items() for x in [("u", u) for u in s["units"]] + [("c", n) for n, _ in s["containers"]]}
    new = {(h, x) for h, s in snap.items() for x in [("u", u) for u in s["units"]] + [("c", n) for n, _ in s["containers"]]}
    appeared, gone = sorted(new - old), sorted(old - new)
    if not appeared and not gone:
        return None
    cls = {(c["host"], c["what"]): c for c in classify_snapshot(snap)}
    lines = []
    for h, (t, n) in appeared:
        lines.append({"change": "NEW problem", "host": h, "kind": "unit" if t == "u" else "container", "name": n})
    for h, (t, n) in gone:
        lines.append({"change": "recovered", "host": h, "kind": "unit" if t == "u" else "container", "name": n})
    prompt = ("Service watch for the owner: a script compared failed systemd units and non-running containers on server1 and server2 with the previous snapshot and found changes. "
              "Write at most 100 words: for each change say what it is, the severity from the table, and one concrete next step (propose only).\n" + RULES +
              "\nCHANGES:\n" + json.dumps(lines, indent=0)[:2500] + "\nCURRENT PROBLEM SET WITH SEVERITIES:\n" + json.dumps(classify_snapshot(snap), indent=0)[:2500])
    return f"Service watch {wd}", prompt, KINDS["service_watch"][1], _digest(lines)


def collect_vault_review(wd, now_ts, ask=None):
    try:
        res = vaultlint.lint(MIRROR)
    except Exception:
        return None
    if not res or res["issue_count"] <= 0 or jobs.kv_get("vault_review_digest") == res["digest"]:
        return None
    slim = {k: v for k, v in res.items() if k != "digest"}
    prompt = ("Review of the owner's Obsidian infrastructure vault, based on a deterministic lint (not your own reading). Write at most 150 words: group the findings, say which matter most and why, "
              "and propose a concrete fix for each (which note needs re-verifying, which link is likely a typo). Do not claim you opened or changed any note. Do not invent notes.\nLINT:\n" + json.dumps(slim, indent=0)[:5000])
    return f"Vault review {wd}", prompt, KINDS["vault_review"][1], res["digest"]


_LV = re.compile(r"^last_verified:\s*(\d{4}-\d{2}-\d{2})", re.M)


def stale_notes(root=MIRROR, today=None, limit=12, min_days=45):
    today = today or date.today(); out = []
    for sub in ("01 - Infrastructure", "02 - Services", "13 - Deep Dives", "03 - Operations"):
        for dp, dn, fn in os.walk(os.path.join(root, sub)):
            for f in fn:
                if not f.endswith(".md"): continue
                try: head = open(os.path.join(dp, f), encoding="utf-8", errors="replace").read(1200)
                except OSError: continue
                m = _LV.search(head)
                if not m: continue
                age = (today - date.fromisoformat(m.group(1))).days
                if age >= min_days: out.append((age, f[:-3], sub))
    return sorted(out, reverse=True)[:limit]


def collect_docs_gaps(wd, now_ts, ask=None):
    if _days_since("docs_gaps_last", now_ts) < WEEKLY_DAYS:
        return None
    st = stale_notes()
    if not st:
        return None
    rows = [{"note": n, "folder": f, "days_since_verified": a} for a, n, f in st]
    prompt = ("Documentation gaps for the owner's homelab vault: a script found the infrastructure notes that were verified longest ago (more than 45 days). "
              "Write at most 150 words: say which three to re-verify first and why (services that change often or that matter most), and for each name the ONE read-only check that would verify it. "
              "Do not claim you opened or changed any note.\nNOTES:\n" + json.dumps(rows, indent=0))
    return f"Docs gaps {wd}", prompt, KINDS["docs_gaps"][1], _digest(rows)


def open_items(root=MIRROR):
    items = []
    for rel in ("07 - Projects/Agno - Outstanding Decisions.md", "07 - Projects/Open Questions - GPU Box AI.md"):
        try: txt = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        except OSError: continue
        for ln in txt.splitlines():
            if re.match(r"^\|\s*\d+\s*\|", ln) and "RESOLVED" not in ln.upper():
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                items.append({"note": os.path.basename(rel)[:-3], "item": re.sub(r"\[\[|\]\]|\*\*", "", cells[1])[:220] if len(cells) > 1 else ln[:220]})
    return items


PROJECT_CONTEXT = ("Context you must respect: the council currently runs ONE model for every role (Critic on another model family is a wish, not the current state); "
                   "items marked as a question are waiting for the owner, not for the council.")


def collect_project_status(wd, now_ts, ask=None):
    items = open_items()
    if not items:
        return None
    d = _digest(items)
    if jobs.kv_get("project_status_digest") == d and _days_since("project_status_last", now_ts) < WEEKLY_DAYS:
        return None
    prompt = ("Project status for the owner: these are the still-open items in their decision and open-question notes (resolved ones are already removed by a script). "
              "Write at most 150 words: group them, say which two block the most progress, and for each give the smallest next step and who has to do it (the owner or the council). "
              "Do not invent items. " + PROJECT_CONTEXT + "\nOPEN ITEMS:\n" + json.dumps(items, indent=0)[:4500])
    return f"Project status {wd}", prompt, KINDS["project_status"][1], d


def _slug(t): return hashlib.sha256(t.lower().encode()).hexdigest()[:12]


def collect_research(wd, now_ts, ask=None):
    for t in get_config()["topics"]:
        if _days_since("topic_last:" + _slug(t), now_ts) >= RESEARCH_DAYS:
            prompt = (f"Research digest for the owner on the standing topic: \"{t}\". Ask the Researcher (web search with source checking) what is new or important about this in the last few weeks. "
                      "Result: at most 150 words, every web claim with a [S#] citation, tier A/B sources preferred, UNCONFIRMED when only one source, and the date retrieved. If nothing useful is found say 'nothing new found'.")
            return f"Research: {t[:60]}", prompt, KINDS["research_digest"][1], "topic:" + _slug(t)
    return None


def build_morning(wd, now_ts=None, ask=None):
    """built at RUN time so it describes what actually ran tonight"""
    now_ts = now_ts or time.time()
    todays = [j for j in jobs.recent(60) if j.get("window_date") == wd or (j.get("created_at") or 0) > now_ts - 12 * 3600]
    ran = [{"kind": j["kind"], "title": j["title"], "status": j["status"], "attempts": j["attempts"], "note": j.get("note")} for j in todays if j["kind"] != "morning_briefing"]
    facts = _load_facts() or {}
    a = known.assess_facts(facts) if facts else None
    extra = {}
    if ask:
        for host in ("server2",):
            for chk in ("disk", "memory"):
                d = ask(host=host, check=chk, arg=""); extra[f"{host}_{chk}"] = (d.get("output") or "")[:600] if d.get("rc") == 0 else "unavailable"
    snap = jobs.kv_get("service_snapshot")
    data = {"overnight_jobs": ran, "server1": {"overall": a["overall"] if a else "unknown", "findings": (a or {}).get("findings", [])[:6], "backup": facts.get("backup")}, "server2": extra}
    return ("Morning briefing for the owner, written from what a script collected overnight. Write at most 150 words in this order: (1) what ran tonight and whether it worked (failed or unfinished jobs first), "
            "(2) server1 and server2 status in one line each, (3) backup status, (4) anything that needs the owner's decision today. " + RULES + "\nDATA:\n" + json.dumps(data, indent=0, default=str)[:5000])


def collect_morning(wd, now_ts, ask=None):
    h = datetime.fromtimestamp(now_ts, W.LON).hour
    pending = [j for j in jobs.recent(60) if j["status"] in ("queued", "running") and j["kind"] != "morning_briefing" and j.get("window_date") == wd]
    done = [j for j in jobs.recent(60) if j["status"] == "done" and j.get("window_date") == wd]
    if pending or h < 7 or not done:                  # the briefing describes the night, so it runs late in the window (07:00-09:00) after everything else
        return None
    return f"Morning briefing {wd}", "(built when it runs)", KINDS["morning_briefing"][1], None


COLLECTORS = {"health_summary": collect_health, "service_watch": collect_service_watch, "vault_review": collect_vault_review, "docs_gaps": collect_docs_gaps,
              "project_status": collect_project_status, "research_digest": collect_research, "morning_briefing": collect_morning}


def seed(now=None, ask=None, now_ts=None):
    """called by the scheduler tick while inside the window. Each kind is evaluated at most once per window (morning_briefing and research are re-evaluated until queued)."""
    now_ts = now_ts or time.time()
    wd = W.window_date(now)
    cfg = get_config()
    made = []
    for kind, fn in COLLECTORS.items():
        if not cfg["kinds"].get(kind) or jobs.exists_for_window(kind, wd):
            continue
        retry_kinds = kind in ("morning_briefing", "research_digest")
        if not retry_kinds and jobs.kv_get(f"seeded:{wd}:{kind}"):
            continue
        if kind in ("health_summary", "service_watch", "morning_briefing"):
            jobs.supersede(kind, wd)                  # yesterday's unfinished briefing is stale
        elif jobs.unfinished(kind):
            continue                                  # unfinished work carries over; do not pile up a duplicate
        try:
            r = fn(wd, now_ts, ask)
        except Exception as e:                       # a collector must never stop the scheduler
            jobs.kv_set(f"seeded:{wd}:{kind}", f"error {type(e).__name__}"); continue
        if not retry_kinds:
            jobs.kv_set(f"seeded:{wd}:{kind}", "1")
        if r:
            title, prompt, prio, dig = r
            jid = jobs.enqueue(kind, title, prompt, priority=prio, window_date=wd)
            if kind == "vault_review" and dig: jobs.kv_set(f"digest_for_job_{jid}", dig)
            if kind == "research_digest" and dig: jobs.kv_set(f"topic_for_job_{jid}", dig)
            made.append((kind, jid))
    return made


def on_done(job, now_ts=None):
    """bookkeeping when a job finished successfully (so the same thing is not repeated tomorrow)"""
    now_ts = now_ts or time.time(); k, jid = job["kind"], job["id"]
    if k == "vault_review":
        d = jobs.kv_get(f"digest_for_job_{jid}")
        if d: jobs.kv_set("vault_review_digest", d)
    elif k == "research_digest":
        t = jobs.kv_get(f"topic_for_job_{jid}")
        if t: jobs.kv_set("topic_last:" + t.split(":", 1)[1], now_ts)
    elif k == "docs_gaps":
        jobs.kv_set("docs_gaps_last", now_ts)
    elif k == "project_status":
        jobs.kv_set("project_status_last", now_ts); jobs.kv_set("project_status_digest", _digest(open_items()))


def refresh_prompt(job, ask=None):
    """prompts that must describe the situation at run time"""
    if job["kind"] == "morning_briefing":
        return build_morning(job.get("window_date") or W.window_date(), ask=ask)
    return job["prompt"]
