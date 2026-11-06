#!/usr/bin/env python3
"""Copy the AI Mode state file from the Windows PC to /run/agno-pcstate/state.json every 30 s (runs as the owner's Unix user: uses that user's existing SSH
access, FIXED command only, nothing from the PC is ever executed). The council service reads the result; it has no SSH key itself.
The JSON is validated against an allow-list of keys/types so a bad state file cannot inject anything."""
import json, os, re, subprocess, sys, time

OUT = "/run/agno-pcstate/state.json"
CMD = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
       "YOUR_USER@GPU_PC_HOST", r"type C:\Users\YOU\AIMode\state.json"]
ALLOWED = {"ts": (int, float), "ai_mode": str, "gaming": bool, "game": (str, type(None)), "logged_in": bool, "lmstudio_up": bool,
           "model_loaded": bool, "model": (str, type(None)), "context": (int, type(None)), "ram_available_mb": (int, float, type(None)),
           "commit_pct": (int, float, type(None)), "vram_used_mib": (int, float, type(None)), "vram_free_mib": (int, float, type(None)),
           "unload_pending": bool, "note": (str, type(None)), "app_uptime_s": (int, float), "power_plan": (str, type(None))}


def poll():
    now = time.time()
    try:
        r = subprocess.run(CMD, capture_output=True, text=True, timeout=15)
        raw = r.stdout.lstrip("\ufeff")
        if r.returncode != 0 or len(raw) > 20000:
            raise ValueError("ssh failed or oversized")
        d = json.loads(raw)
        out = {k: v for k, v in d.items() if k in ALLOWED and isinstance(v, ALLOWED[k])}
        for k in ("note", "game", "model", "power_plan"):
            if isinstance(out.get(k), str): out[k] = out[k][:120]
        if out.get("ai_mode") not in ("ON", "OFF"): raise ValueError("bad ai_mode")
        out["reachable"] = True; out["poll_ts"] = now
        out["state_age_s"] = max(0.0, now - float(out.get("ts", 0)))
        return out
    except Exception as e:
        return {"reachable": False, "poll_ts": now, "error": type(e).__name__}


CTL_DIR = "/run/agno-pcstate/control"
CONF_OUT = "/run/agno-pcstate/config.json"
CONF_CMD = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "YOUR_USER@GPU_PC_HOST", r"type C:\Users\YOU\AIMode\config.json"]
# the ONLY command the control channel can ever run: fixed text, the argument is one of two literals
CTL_CMD = {"on": r"powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\YOU\AIMode\aimode-ctl.ps1 on",
           "off": r"powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\YOU\AIMode\aimode-ctl.ps1 off",
           "rogue": r"powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\YOU\AIMode\aimode-ctl.ps1 model rogue",
           "main": r"powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\YOU\AIMode\aimode-ctl.ps1 model main"}
CONF_KEEP = {"night": dict, "ram_unsafe_available_mb": (int, float), "commit_unsafe_pct": (int, float), "load_min_available_mb": (int, float), "model": dict, "rogue_model": dict, "power_plan": str,
             "game_check_interval_s": (int, float), "state_interval_s": (int, float)}


def poll_config():
    try:
        r = subprocess.run(CONF_CMD, capture_output=True, text=True, timeout=15)
        d = json.loads(r.stdout.lstrip("\ufeff"))
        out = {k: v for k, v in d.items() if k in CONF_KEEP and isinstance(v, CONF_KEEP[k])}
        out["poll_ts"] = time.time()
        tmp = CONF_OUT + ".tmp"
        with open(tmp, "w") as f: json.dump(out, f)
        os.replace(tmp, CONF_OUT)
    except Exception:
        pass


def _ctl_log(row):
    row["ts"] = time.time()
    print("control " + json.dumps(row), flush=True)                       # journal
    with open(os.path.join(CTL_DIR, "audit.jsonl"), "a") as f: f.write(json.dumps(row) + "\n")


def handle_control(state, last_run=[0.0]):
    """council writes ONE request file; every rule is re-checked here (this is the last gate). the website has the same authority as the tray: ON and OFF both work (validation, expiry, rate limit and audit remain)."""
    req_path = os.path.join(CTL_DIR, "request.json")
    if not os.path.exists(req_path):
        return
    try:
        with open(req_path) as f: req = json.loads(f.read(2000))
        os.remove(req_path)
    except (OSError, ValueError) as e:
        _ctl_log({"event": "aimode_unreadable", "ok": False, "msg": "could not read the request: " + type(e).__name__})
        try: os.remove(req_path)
        except OSError: pass
        return
    rid, cmd = str(req.get("id", ""))[:40], req.get("cmd")
    res = {"id": rid if re.fullmatch(r"[0-9a-f-]{6,40}", rid) else "invalid", "cmd": cmd if cmd in CTL_CMD else "invalid", "ok": False}
    if not re.fullmatch(r"[0-9a-f-]{6,40}", rid) or cmd not in CTL_CMD or abs(time.time() - float(req.get("ts", 0))) > 30:
        res["msg"] = "invalid or expired request"
    elif time.time() - last_run[0] < 5:
        res["msg"] = "too many requests; wait a few seconds"
    else:
        last_run[0] = time.time()
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "YOUR_USER@GPU_PC_HOST", CTL_CMD[cmd]], capture_output=True, text=True, timeout=20)
            res["ok"] = r.returncode == 0
            res["msg"] = "sent; the tray app applies it within about 5 seconds" if res["ok"] else "ssh to the PC failed"
        except Exception as e:
            res["msg"] = "ssh error " + type(e).__name__
    res["ts"] = time.time()
    handle_control.applied = bool(res["ok"])
    tmp = os.path.join(CTL_DIR, "result.tmp")
    with open(tmp, "w") as f: json.dump(res, f)
    os.replace(tmp, os.path.join(CTL_DIR, "result.json"))
    _ctl_log({"event": "aimode_" + (cmd if cmd in CTL_CMD else "invalid"), "id": rid if re.fullmatch(r"[0-9a-f-]{6,40}", rid) else "invalid", "ok": res["ok"], "msg": res["msg"]})


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    os.makedirs(CTL_DIR, exist_ok=True); os.chmod(CTL_DIR, 0o770)
    last_poll, last_conf, d = 0.0, 0.0, {}
    while True:
        if time.time() - last_poll >= 30:
            d = poll(); last_poll = time.time()
            tmp = OUT + ".tmp"
            with open(tmp, "w") as f: json.dump(d, f)
            os.replace(tmp, OUT)
        if time.time() - last_conf >= 600:
            poll_config(); last_conf = time.time()
        handle_control.applied = False
        handle_control(d)
        if handle_control.applied: last_poll = time.time() - 23          # re-read the PC state in ~7 s so the website shows the change quickly
        time.sleep(1.5)


if __name__ == "__main__":
    main()
