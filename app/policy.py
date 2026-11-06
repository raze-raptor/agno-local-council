"""When may the council start unattended (LLM-driven) work?  Pure decision code: inputs in, (ok, reasons) out.

All of these must hold (the owner's brief, section 5): inside 03:00-09:00 Europe/London; the PC reported fresh state;
AI Mode ON; not Gaming; Windows manually logged in; LM Studio up; the right model loaded; RAM/commit/VRAM within limits.
We never wake the PC and never log in for it - a missing or stale state simply means 'not eligible', jobs stay queued.
"""
import json
import os
import time

import window as S

PC_STATE_FILE = os.environ.get("AGNO_PC_STATE", "/run/agno-pcstate/state.json")
PC_STATE_MAX_AGE = 90          # seconds; the PC app writes every 15 s, the poller copies every 30 s
RAM_MIN_AVAILABLE_MB = 400     # watchdog abort level used in the Phase 1 tests was 450
COMMIT_MAX_PCT = 93
VRAM_MIN_FREE_MIB = 250


def read_pc_state(path=PC_STATE_FILE, now_ts=None):
    """-> dict (with 'age_s') or None if missing/unreadable. Staleness is judged by the caller via age_s."""
    try:
        with open(path) as f:
            raw = f.read(65536)
        d = json.loads(raw)
        if not isinstance(d, dict):
            return None
    except (OSError, ValueError):
        return None
    # age = time since server1 last polled + how old the PC's own state already was when polled (PC clock), so a dead
    # AI Mode app on a reachable PC goes stale even though the poller keeps succeeding
    poll = float(d.get("poll_ts") or 0)
    now = now_ts if now_ts is not None else time.time()
    d["age_s"] = round((now - poll) + max(0.0, float(d.get("state_age_s") or 0)), 1)
    return d


def proactive_decision(inference, pc, now=None):
    """inference: {'ok': bool, 'reason': str}; pc: dict from read_pc_state or None. -> (ok, [reasons it is NOT ok])"""
    why = []
    if not S.in_window(now):
        why.append("outside 03:00-09:00 Europe/London")
    if pc is None:
        why.append("no PC state (PC off, asleep, tunnel/poller down, or AI Mode app not running)")
    elif pc.get("age_s", 1e9) > PC_STATE_MAX_AGE:
        why.append(f"PC state stale ({pc['age_s']:.0f}s old)")
    else:
        if pc.get("reachable") is False:
            why.append("PC unreachable")
        if pc.get("ai_mode") != "ON":
            why.append("AI Mode is OFF")
        if pc.get("gaming"):
            why.append("Gaming override active")
        if not pc.get("logged_in", False):
            why.append("Windows not logged in")
        if not pc.get("lmstudio_up", False):
            why.append("LM Studio not running")
        if pc.get("ram_available_mb", 0) < RAM_MIN_AVAILABLE_MB:
            why.append(f"RAM too low ({pc.get('ram_available_mb')} MB available)")
        if pc.get("commit_pct", 100) > COMMIT_MAX_PCT:
            why.append(f"committed memory too high ({pc.get('commit_pct')}%)")
        vf = pc.get("vram_free_mib")
        if vf is not None and vf < VRAM_MIN_FREE_MIB:
            why.append(f"VRAM too low ({vf} MiB free)")
    if not inference.get("ok"):
        why.append("model not available: " + str(inference.get("reason")))
    return (not why), why


def evaluate(inference, pc, now=None):
    """Same rules as proactive_decision but as a structured list for the Schedule view: [{id, label, ok, detail}]. Pure; must agree with proactive_decision."""
    pcok = pc is not None and pc.get("age_s", 1e9) <= PC_STATE_MAX_AGE
    has = pc is not None and pcok
    g = (pc or {}).get
    rows = [
        ("window", "Inside 03:00-09:00 Europe/London", S.in_window(now), "inside the window" if S.in_window(now) else "outside the window"),
        ("pc_state", "PC state is fresh", pcok, "no PC state" if pc is None else f"{pc.get('age_s', 0):.0f}s old (limit {PC_STATE_MAX_AGE}s)"),
        ("reachable", "PC reachable", has and pc.get("reachable") is not False, "reachable" if has and pc.get("reachable") is not False else "unreachable or unknown"),
        ("ai_mode", "AI Mode is ON", has and g("ai_mode") == "ON", f"AI Mode {g('ai_mode') or 'unknown'}"),
        ("gaming", "Not gaming", has and not g("gaming"), f"gaming: {g('game')}" if has and g("gaming") else "no game detected"),
        ("logged_in", "Windows logged in", has and bool(g("logged_in", False)), "logged in" if has and g("logged_in", False) else "not logged in"),
        ("lmstudio", "LM Studio running", has and bool(g("lmstudio_up", False)), "running" if has and g("lmstudio_up", False) else "not running"),
        ("ram", f"RAM available >= {RAM_MIN_AVAILABLE_MB} MB", has and g("ram_available_mb", 0) >= RAM_MIN_AVAILABLE_MB, f"{g('ram_available_mb')} MB available"),
        ("commit", f"Committed memory <= {COMMIT_MAX_PCT}%", has and g("commit_pct", 100) <= COMMIT_MAX_PCT, f"{g('commit_pct')}% committed"),
        ("vram", f"VRAM free >= {VRAM_MIN_FREE_MIB} MiB", has and (g("vram_free_mib") is None or g("vram_free_mib") >= VRAM_MIN_FREE_MIB), f"{g('vram_free_mib')} MiB free"),
        ("model", "Model loaded at the expected context", bool(inference.get("ok")), str(inference.get("reason"))),
    ]
    return [{"id": i, "label": l, "ok": bool(o), "detail": d} for i, l, o, d in rows]
