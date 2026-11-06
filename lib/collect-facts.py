#!/usr/bin/env python3
"""Deterministic health facts for the council (root, systemd timer, no model, no network). Writes /var/lib/agno-facts/health.json
(root:agno 0640). The council only ever READS this file; it cannot run these commands itself."""
import json, os, shutil, subprocess, time

OUT = "/var/lib/agno-facts/health.json"
BACKUP_LOG = "/home/YOUR_USER/backups/backup-core.log"


def run(cmd, t=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=t).stdout
    except Exception as e:
        return f"ERR {type(e).__name__}"


f = {"ts": time.time(), "host": os.uname().nodename}
f["uptime_h"] = round(float(open("/proc/uptime").read().split()[0]) / 3600, 1)
f["load_1_5_15"] = [round(x, 2) for x in os.getloadavg()]
mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
f["mem"] = {"total_gb": round(mi["MemTotal"] / 1048576, 1), "available_gb": round(mi["MemAvailable"] / 1048576, 1),
            "swap_used_gb": round((mi["SwapTotal"] - mi["SwapFree"]) / 1048576, 1)}
du = shutil.disk_usage("/")
f["disk_root"] = {"used_pct": round(100 * du.used / du.total, 1), "free_gb": round(du.free / 1e9)}
# documented as known/harmless in the vault (server1.md, Gaps and Priorities.md); keep this list in step with the vault
KNOWN_FAILED = {"network-powersave-disable.service", "pmie_daily.service", "pmlogger_daily.service", "systemd-networkd-wait-online.service", "fwupd-refresh.service"}
_failed = [l.split()[0] for l in run(["systemctl", "--failed", "--no-legend", "--plain"]).splitlines() if l.strip()]
f["failed_units_known_harmless"] = [u for u in _failed if u in KNOWN_FAILED]
f["failed_units"] = [u for u in _failed if u not in KNOWN_FAILED]
bad = []
for l in run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"]).splitlines():
    n, _, st = l.partition("|")
    if "unhealthy" in st or st.startswith("Restarting") or (st.startswith("Exited") and "(0)" not in st):
        bad.append(f"{n}: {st}")
f["containers_with_problems"] = bad[:15]
f["containers_total"] = len(run(["docker", "ps", "-a", "-q"]).split())
try:
    age_h = (time.time() - os.path.getmtime(BACKUP_LOG)) / 3600
    tail = open(BACKUP_LOG, errors="replace").read()[-3000:]
    f["backup"] = {"log_age_h": round(age_h, 1), "last_run_ok": "=== backup run OK ===" in tail,
                   "restic_check_clean": "no errors were found" in tail}
except OSError:
    f["backup"] = {"error": "backup log unreadable"}
f["agno_service"] = run(["systemctl", "is-active", "agno-council"]).strip()
f["tunnel_service"] = run(["systemctl", "is-active", "council-gpu-tunnel"]).strip()
# anything alarming, in one place, so the model does not have to hunt for it
alerts = []
if f["failed_units"]: alerts.append(f"{len(f['failed_units'])} failed systemd unit(s)")
if bad: alerts.append(f"{len(bad)} container(s) unhealthy/exited/restarting")
if f["disk_root"]["used_pct"] > 85: alerts.append(f"root disk {f['disk_root']['used_pct']}% full")
if f["mem"]["available_gb"] < 2: alerts.append("server1 available RAM under 2 GB")
b = f["backup"]
if b.get("log_age_h", 0) > 30 or b.get("last_run_ok") is False or "error" in b: alerts.append("backup stale or last run not OK")
f["alerts"] = alerts
os.makedirs(os.path.dirname(OUT), exist_ok=True)
tmp = OUT + ".tmp"
json.dump(f, open(tmp, "w"), indent=1)
os.chmod(tmp, 0o640); shutil.chown(tmp, "root", "agno"); os.replace(tmp, OUT)
