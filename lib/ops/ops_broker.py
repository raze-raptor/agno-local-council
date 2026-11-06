#!/usr/bin/env python3
"""agno-ops broker (server1). Unix socket in, check id out. The council can ask for a check on host 'server1' (run locally) or
'server2' (run over a dedicated forced-command SSH key that can only run the same catalogue). It cannot run anything else:
there is no code path from the socket to an arbitrary command. Request: {"host","check","arg"} -> {"rc","output"}."""
import json, os, socketserver, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops_core as C

SOCK = os.environ.get("AGNO_OPS_SOCK", "/run/agno-ops/ops.sock")
AUDIT = os.environ.get("AGNO_OPS_AUDIT", "/var/lib/agno-ops/audit.jsonl")
KEY = os.environ.get("AGNO_OPS_KEY", "/var/lib/agno-ops/.ssh/id_ed25519")
KNOWN = os.environ.get("AGNO_OPS_KNOWN", "/var/lib/agno-ops/.ssh/known_hosts")
HOSTS = {"server1": None, "server2": "YOUR_USER@SERVER2_HOST"}
HOURLY = 120
_L = threading.Lock(); _T = []


def audit(**kw):
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        with _L, open(AUDIT, "a") as f: f.write(json.dumps(kw) + "\n")
    except OSError: pass


def handle(req):
    host, check, arg = str(req.get("host", "")), str(req.get("check", "")), str(req.get("arg", ""))
    now = time.time()
    with _L:
        _T[:] = [t for t in _T if now - t < 3600]
        if len(_T) >= HOURLY: return {"rc": 429, "output": "hourly check limit reached"}
        _T.append(now)
    if host not in HOSTS:
        return {"rc": 2, "output": "unknown host; available: server1, server2"}
    if host == "server1":
        rc, out = C.run_check(check, arg)
    else:
        if check not in C.CHECKS or len(arg) > 80 or any(ch in arg for ch in " \t\n;|&$`<>\\\"'"):
            rc, out = 2, "bad check or argument"
        else:
            cmd = ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KNOWN}",
                   "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=5", HOSTS[host], f"{check} {arg}".strip()]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
                rc, out = p.returncode, (p.stdout + (("\n[stderr] " + p.stderr[-300:]) if p.returncode else "")).strip()
            except subprocess.TimeoutExpired:
                rc, out = 124, "timed out"
            out = C.redact(out)[:C.MAX_OUT]
    audit(host=host, check=check, arg=arg[:80], rc=rc, bytes=len(out))
    return {"rc": rc, "output": out, "host": host, "check": check}


class H(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(4096)
            req = json.loads(line) if line else {}
            resp = handle(req) if req.get("op") != "catalogue" else {"rc": 0, "catalogue": C.catalogue(), "hosts": sorted(HOSTS)}
        except Exception as e:
            resp = {"rc": 500, "output": type(e).__name__}
        self.wfile.write((json.dumps(resp) + "\n").encode())


class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer): daemon_threads = True


if __name__ == "__main__":
    os.makedirs(os.path.dirname(SOCK), exist_ok=True)
    if os.path.exists(SOCK): os.unlink(SOCK)
    s = S(SOCK, H); os.chmod(SOCK, 0o660); audit(event="start"); s.serve_forever()
