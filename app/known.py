"""Known and documented state, plus deterministic severity rules. The model never decides severity: it explains what these rules found.
Sources: vault notes server1, server2, Other Containers, Known Issues (2026-11-05). Edit here when the documented state changes."""
import re

# stopped / leftover by design -> never a finding on their own (info only)
KNOWN_STOPPED = {
    "core-runner-app": "stopped project container (documented in Other Containers)",
    "vouc-build": "VOUC build environment, started on demand",
    "tempmail-db-init": "one-shot init container",
    "wg-easy": "server2 leftover (Exited ~3 months); restarting it could clash with the real WireGuard",
    "wg-easy-fallback": "server2 dormant WireGuard fallback (created, not started, by design)",
    "motioneye": "stopped since 2026-10-29 (camera moved to akaso-cam)",
}
KNOWN_FAILED_UNITS = {
    "network-powersave-disable.service": "known harmless",
    "pmie_daily.service": "known harmless",
    "pmlogger_daily.service": "known harmless",
    "systemd-networkd-wait-online.service": "known harmless",
    "fwupd-refresh.service": "known harmless on server2 (firmware refresh)",
}
CRITICAL_NAMES = ("cloudflared", "docker", "ssh", "nginx", "wg-quick", "mailcow", "vaultwarden", "authentik", "pihole", "pi-hole", "wg-easy-main")
RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
_AGE = re.compile(r"(?:Exited|Created)\b.*?(\d+|an?|about an?)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)


def container_age_hours(status):
    m = _AGE.search(status or "")
    if not m:
        return None
    n = 1 if m.group(1).lower().startswith(("a", "about")) else int(m.group(1))
    return n * {"second": 1 / 3600, "minute": 1 / 60, "hour": 1, "day": 24, "week": 168, "month": 720, "year": 8760}[m.group(2).lower()]


def classify_container(name, status):
    """-> (severity, reason). An old exited container is not 'high'."""
    n = name.strip().lstrip("/")
    st = status or ""
    if n in KNOWN_STOPPED:
        return "info", KNOWN_STOPPED[n]
    crit = any(c in n.lower() for c in CRITICAL_NAMES)
    if re.search(r"restarting|unhealthy", st, re.I):
        return ("high" if crit else "medium"), "restarting or unhealthy now"
    if st.startswith(("Exited", "Created", "Dead")):
        h = container_age_hours(st)
        if h is None:
            return "medium", "stopped, age unknown"
        if h < 24:
            return ("high" if crit else "medium"), "stopped within the last day"
        if h < 24 * 7:
            return "medium", "stopped within the last week"
        return "low", "stopped for weeks or longer (probably intentional)"
    return "info", "running"


def classify_unit(name):
    if name in KNOWN_FAILED_UNITS:
        return "info", KNOWN_FAILED_UNITS[name]
    if any(c in name.lower() for c in CRITICAL_NAMES):
        return "high", "a critical unit has failed"
    return "medium", "new failed unit"


def assess_facts(facts):
    """Deterministic assessment of the server1 health facts -> {'overall','findings':[{sev,what,why}],'known':[...]}"""
    f, known = [], []

    def add(sev, what, why):
        (known if sev == "info" else f).append({"sev": sev, "what": what, "why": why})
    for u in facts.get("failed_units_known_harmless", []) + facts.get("failed_units", []):
        sev, why = classify_unit(u)
        add(sev, f"failed unit {u}", why)
    for c in facts.get("containers_with_problems", []):
        name, _, st = str(c).partition(":")
        sev, why = classify_container(name, st.strip())
        add(sev, f"container {name.strip()} ({st.strip()})", why)
    d = (facts.get("disk_root") or {}).get("used_pct")
    if isinstance(d, (int, float)):
        if d >= 90: add("high", f"root disk {d}% used", "above 90%")
        elif d >= 80: add("medium", f"root disk {d}% used", "above 80%")
    m = (facts.get("mem") or {}).get("available_gb")
    if isinstance(m, (int, float)) and m < 1.0:
        add("high", f"only {m} GB memory available", "below 1 GB")
    b = facts.get("backup") or {}
    if b.get("last_run_ok") is False: add("high", "last backup run failed", "backup log says not ok")
    if isinstance(b.get("log_age_h"), (int, float)) and b["log_age_h"] > 48: add("medium", f"backup log is {b['log_age_h']:.0f} h old", "no backup in over 48 h")
    if b.get("restic_check_clean") is False: add("high", "restic check not clean", "repository check failed")
    for k in ("agno_service", "tunnel_service"):
        if facts.get(k) not in (None, "active"): add("high", f"{k} is {facts.get(k)}", "service not active")
    worst = max((RANK[x["sev"]] for x in f), default=-1)
    overall = "urgent" if worst >= 3 else "attention" if worst >= 2 else "calm"
    return {"overall": overall, "findings": sorted(f, key=lambda x: -RANK[x["sev"]]), "known": known}
