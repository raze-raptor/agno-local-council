#!/usr/bin/env python3
"""Security-patch watch for the services the homelab depends on (cloudflared, Authentik). Runs hourly from patchwatch.timer and does something ONLY while
AI Mode on the GPU PC is ON (the owner: "an hourly workload when in AI Mode"). Deterministic: reads the installed version, asks GitHub for the releases and the
published security advisories, and decides the severity in code (no model request, so it never competes with chat). It changes NOTHING: updating a package or
an image is an ask-first action, so the output only says what is behind and how the owner would update it.
Output: /var/lib/agno-patchwatch/latest.json (+ history.jsonl). A new medium/high finding sends ONE alert mail (same alert mailbox as the other watchdogs)."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

OUT = os.environ.get("PATCHWATCH_DIR", "/var/lib/agno-patchwatch")
PCSTATE = os.environ.get("PATCHWATCH_PCSTATE", "/run/agno-pcstate/state.json")
UA = {"User-Agent": "server1-patchwatch", "Accept": "application/vnd.github+json"}
SEC_RX = re.compile(r"security|cve-\d|vulnerab", re.I)
SEV_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


# ---------------- pure helpers (unit-tested) ----------------
def vtuple(s):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", s or "")
    return tuple(int(x) for x in m.groups()) if m else None


def in_range(version, rng):
    """GitHub advisory range such as '<= 2026.8.1', '< 2020.8.1', '>= 1.0, < 2.0'. True if `version` falls inside it."""
    v = vtuple(version)
    if v is None or not rng: return False
    for clause in rng.split(","):
        m = re.match(r"\s*(<=|>=|<|>|=)\s*(\S+)", clause)
        t = vtuple(m.group(2)) if m else None
        if not m or t is None: return False
        op = m.group(1)
        if not {"<=": v <= t, ">=": v >= t, "<": v < t, ">": v > t, "=": v == t}[op]: return False
    return True


def classify(installed, releases, advisories, line_len=2):
    """releases: [(tuple, tag, published, body)] stable only; advisories: [{id, severity, ranges}]. `line_len` = how many leading version numbers define the
    supported line (2 = 2026.8). Returns the decision dict (no network, no time)."""
    iv = vtuple(installed)
    out = {"installed": installed, "latest": None, "latest_in_line": None, "status": "unknown", "severity": "none", "details": [], "advisories": [], "behind": 0}
    if iv is None or not releases:
        out["details"].append("could not determine the installed or the latest version"); return out
    rel = sorted(releases, key=lambda r: r[0])
    out["latest"] = rel[-1][1]
    same_line = [r for r in rel if r[0][:line_len] == iv[:line_len]]
    top_line = max(r[0][:line_len] for r in rel)
    if same_line: out["latest_in_line"] = same_line[-1][1]
    newer = [r for r in same_line if r[0] > iv]
    out["behind"] = len(newer)
    hit = [a for a in advisories if any(in_range(installed, rg) for rg in a["ranges"])]
    out["advisories"] = [f"{a['id']} ({a['severity']})" for a in hit]
    secnotes = [r[1] for r in newer if SEC_RX.search(r[3] or "")]
    if hit:
        out.update(status="security_update", severity="high")
        out["details"].append("a published security advisory affects the installed version: " + ", ".join(out["advisories"]))
    elif secnotes:
        out.update(status="security_update", severity="medium")
        out["details"].append("release notes between your version and the latest mention security: " + ", ".join(secnotes))
    elif newer:
        out.update(status="patch_available", severity="low")
        out["details"].append(f"{len(newer)} newer patch release(s) in your line; none mention security and no advisory affects your version")
    else:
        out.update(status="ok")
        out["details"].append("up to date in your release line")
    if iv[:line_len] < top_line and not hit:
        out["details"].append(f"a newer release line exists ({'.'.join(map(str, top_line))}); upstream normally patches only the latest lines")
        if out["status"] == "ok": out.update(status="newer_line", severity="low")
    return out


# ---------------- sources ----------------
def gh(path):
    with urllib.request.urlopen(urllib.request.Request("https://api.github.com" + path, headers=UA), timeout=25) as r:
        return json.load(r)


def releases(repo, strip):
    out = []
    for r in gh(f"/repos/{repo}/releases?per_page=40"):
        if r.get("prerelease") or r.get("draft"): continue
        v = vtuple(r["tag_name"].replace(strip, ""))
        if v: out.append((v, r["tag_name"].replace(strip, ""), (r.get("published_at") or "")[:10], r.get("body") or ""))
    return out


def advisories(repo):
    try:
        data = gh(f"/repos/{repo}/security-advisories?per_page=50")
    except Exception:
        return None
    return [{"id": a["ghsa_id"], "severity": a.get("severity") or "?", "ranges": [v.get("vulnerable_version_range") for x in a.get("vulnerabilities", []) for v in [x] if v.get("vulnerable_version_range")]}
            for a in data if a.get("state", "published") == "published"]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()


SERVICES = [
    {"name": "cloudflared (Cloudflare tunnel)", "repo": "cloudflare/cloudflared", "strip": "", "line_len": 2,
     "installed": lambda: (re.search(r"version\s+(\S+)", run(["/usr/bin/cloudflared", "--version"])) or [None, None])[1],
     "hint": "apt-managed: see your upgrade runbook for cloudflared (ask first: restarting cloudflared drops every public site for a moment)"},
    {"name": "Authentik (SSO)", "repo": "goauthentik/authentik", "strip": "version/", "line_len": 2,
     "installed": lambda: (re.search(r":(\d+\.\d+\.\d+)", run(["docker", "inspect", "authentik-server", "--format", "{{.Config.Image}}"])) or [None, None])[1],
     "hint": "image tag in ~/authentik/docker-compose.yml (take a DB dump first, see the vault note Authentik; ask first)"},
]


def check_all():
    res = []
    for s in SERVICES:
        row = {"name": s["name"], "hint": s["hint"], "checked": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        try:
            inst = s["installed"]()
            rel = releases(s["repo"], s["strip"])
            adv = advisories(s["repo"])
            row.update(classify(inst, rel, adv or [], s["line_len"]))
            if adv is None: row["details"].append("advisory list could not be fetched this time (release notes were still checked)")
        except Exception as e:
            row.update(status="unknown", severity="none", installed=None, details=[f"check failed: {type(e).__name__}"])
        res.append(row)
    return res


def signature(res):
    return "|".join(sorted(f"{r['name']}:{r.get('latest')}:{r['status']}" for r in res if SEV_RANK.get(r["severity"], 0) >= 2))


def write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f)
    os.replace(tmp, path)


def main():
    os.makedirs(OUT, exist_ok=True)
    try:
        st = json.load(open(PCSTATE))
        on = st.get("ai_mode") == "ON" and not st.get("gaming") and time.time() - float(st.get("ts", 0)) < 600
    except (OSError, ValueError):
        on = False
    prev = {}
    try: prev = json.load(open(os.path.join(OUT, "latest.json")))
    except (OSError, ValueError): pass
    if not on:
        prev.update(last_skipped=time.time(), skip_reason="AI Mode is off, the PC is gaming, or its state is stale"); write(os.path.join(OUT, "latest.json"), prev)
        print("skipped: AI Mode not ON"); return 0
    res = check_all()
    sig = signature(res)
    latest = {"ts": time.time(), "results": res, "last_alert": prev.get("last_alert"), "alerted_signature": prev.get("alerted_signature", "")}
    if sig and sig != latest["alerted_signature"]:
        try:
            sys.path.insert(0, "/path/to/bin"); from alert_mail import send_alert
            body = "Security patch watch found something that needs attention:\n" + "\n".join(f"- {r['name']}: installed {r.get('installed')}, latest {r.get('latest')}: {'; '.join(r['details'])}" for r in res if SEV_RANK.get(r['severity'], 0) >= 2)
            send_alert(body, subject="Patch watch: security update for a homelab service")
            latest["last_alert"] = time.time(); latest["alerted_signature"] = sig
        except Exception as e:
            print("alert not sent:", type(e).__name__)
    elif not sig: latest["alerted_signature"] = ""
    write(os.path.join(OUT, "latest.json"), latest)
    with open(os.path.join(OUT, "history.jsonl"), "a") as f: f.write(json.dumps({"ts": latest["ts"], "summary": [(r["name"], r.get("installed"), r.get("latest"), r["status"]) for r in res]}) + "\n")
    print(json.dumps([(r["name"], r.get("installed"), r.get("latest"), r["status"], r["severity"]) for r in res]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
