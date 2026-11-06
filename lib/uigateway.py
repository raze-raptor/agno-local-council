#!/usr/bin/env python3
"""Council UI gateway: the ONLY LAN-facing piece. Serves the static frontend, authenticates, and forwards a WHITELIST of API routes to the
council service on loopback (which stays private, unauthenticated and sandboxed). stdlib only.

 - login: password (scrypt hash in /etc/agno/ui-auth.json) -> random session token in an HttpOnly, SameSite=Strict cookie;
   only the SHA-256 of the token is stored server-side; 30-day sliding expiry; logout deletes it
 - throttle: 5 failed logins per client IP -> 5 minute lockout; constant-time compare; same error for every failure
 - CSRF: state-changing requests need the custom header X-Council: 1 AND (if present) an Origin/Referer matching the Host
 - headers: CSP default-src 'self', no framing, no sniffing, no referrer, no caching of API responses
 - the gateway never forwards cookies/auth headers upstream and never reaches the model, the tunnel or any non-whitelisted route
"""
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = os.environ.get("UI_BIND", "SERVER1_LAN_IP")
PORT = int(os.environ.get("UI_PORT", "8975"))
UPSTREAM = (os.environ.get("UI_UPSTREAM_HOST", "127.0.0.1"), int(os.environ.get("UI_UPSTREAM_PORT", "7777")))
STATIC = os.environ.get("UI_STATIC", "/opt/agno-ui/static")
AUTH_FILE = os.environ.get("UI_AUTH_FILE", "/etc/agno-ui/auth.json")
SESS_FILE = os.environ.get("UI_SESSIONS", "/var/lib/agno-ui/sessions.json")
COOKIE = "council_session"
TLS_PORT = int(os.environ.get("UI_TLS_PORT", "0"))               # 0 = no HTTPS listener; a real install (PWA) needs a secure origin
TLS_DIR = os.environ.get("UI_TLS_DIR", "/etc/agno-ui/tls")
TTL = 30 * 86400
MAX_BODY = 256 * 1024
_L = threading.Lock()
_FAILS = {}                                   # ip -> [count, locked_until]

# (method, path regex) -> upstream path builder. Everything else is 404.
ROUTES = [
    ("GET", r"^/api/cap/(agents|tools|skills|workflows|knowledge|evaluations|traces|pc|browser|matrix|patches)$", lambda m: "/v1/cap/" + m.group(1)),
    ("POST", r"^/api/workflows/run$", lambda m: "/v1/workflows/run"),
    ("GET", r"^/api/workflows/([0-9a-f]{12})$", lambda m: "/v1/workflows/" + m.group(1)),
    ("POST", r"^/api/workflows/([0-9a-f]{12})/cancel$", lambda m: "/v1/workflows/" + m.group(1) + "/cancel"),
    ("POST", r"^/api/evals/run$", lambda m: "/v1/evals/run"),
    ("GET", r"^/api/runs/([0-9a-zA-Z_-]{6,64})/events/(\d{1,6})$", lambda m: "/v1/runs/" + m.group(1) + "/events/" + m.group(2)),
    ("GET", r"^/api/shots/([0-9a-f]{32}\.png)$", lambda m: "/v1/shots/" + m.group(1)),
    ("GET", r"^/api/bridge$", lambda m: "/v1/bridge"),
    ("POST", r"^/api/bridge$", lambda m: "/v1/bridge"),
    ("POST", r"^/api/grants/([0-9a-f]{10})$", lambda m: "/v1/grants/" + m.group(1)),
    ("POST", r"^/api/export$", lambda m: "/v1/export"),
    ("GET", r"^/api/memories$", lambda m: "/v1/memories"),
    ("POST", r"^/api/memories/(\d{1,9})$", lambda m: "/v1/memories/" + m.group(1)),
    ("GET", r"^/api/status$", lambda m: "/status"),
    ("GET", r"^/api/agents$", lambda m: "/v1/agents"),
    ("GET", r"^/api/jobs$", lambda m: "/jobs"),
    ("GET", r"^/api/conversations$", lambda m: "/v1/conversations"),
    ("POST", r"^/api/conversations$", lambda m: "/v1/conversations"),
    ("GET", r"^/api/conversations/([A-Za-z0-9_-]{1,40})$", lambda m: f"/v1/conversations/{m.group(1)}"),
    ("PATCH", r"^/api/conversations/([A-Za-z0-9_-]{1,40})$", lambda m: f"/v1/conversations/{m.group(1)}"),
    ("DELETE", r"^/api/conversations/([A-Za-z0-9_-]{1,40})$", lambda m: f"/v1/conversations/{m.group(1)}"),
    ("POST", r"^/api/agui$", lambda m: "/v1/agui"),
    ("POST", r"^/api/agui/cancel$", lambda m: "/v1/agui/cancel"),
    ("GET", r"^/api/tasks$", lambda m: "/v1/tasks"),
    ("POST", r"^/api/tasks$", lambda m: "/v1/tasks"),
    ("GET", r"^/api/tasks/(\d{1,9})$", lambda m: f"/v1/tasks/{m.group(1)}"),
    ("POST", r"^/api/tasks/(\d{1,9})/cancel$", lambda m: f"/v1/tasks/{m.group(1)}/cancel"),
    ("GET", r"^/api/proactive$", lambda m: "/v1/proactive"),
    ("PATCH", r"^/api/proactive$", lambda m: "/v1/proactive"),
    ("GET", r"^/api/approvals$", lambda m: "/v1/approvals"),
    ("POST", r"^/api/approvals/(\d{1,9})/(approve|reject)$", lambda m: f"/v1/approvals/{m.group(1)}/{m.group(2)}"),
    ("GET", r"^/api/schedule$", lambda m: "/v1/schedule"),
    ("GET", r"^/api/aimode$", lambda m: "/v1/aimode"),
    ("POST", r"^/api/aimode$", lambda m: "/v1/aimode"),
    ("GET", r"^/api/models$", lambda m: "/v1/models"),
    ("GET", r"^/api/memory$", lambda m: "/v1/memory"),
    ("GET", r"^/api/system$", lambda m: "/v1/system"),
]
STREAMING = {"/v1/agui"}
is_stream = lambda up: up in STREAMING or up.startswith("/v1/runs/")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json", ".png": "image/png", ".crt": "application/x-x509-ca-cert"}


# ---------------- auth ----------------
def load_auth():
    return json.load(open(AUTH_FILE))


def verify_password(pw):
    try:
        a = load_auth()
        h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(a["salt"]), n=a["n"], r=a["r"], p=a["p"], dklen=32, maxmem=2 ** 26)
        return hmac.compare_digest(h.hex(), a["hash"])
    except Exception:
        return False


def _sessions():
    try:
        return json.load(open(SESS_FILE))
    except (OSError, ValueError):
        return {}


def _save(s):
    os.makedirs(os.path.dirname(SESS_FILE), exist_ok=True)
    tmp = SESS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o600); os.replace(tmp, SESS_FILE)


def new_session():
    tok = secrets.token_urlsafe(32)
    with _L:
        s = {k: v for k, v in _sessions().items() if v > time.time()}
        s[hashlib.sha256(tok.encode()).hexdigest()] = time.time() + TTL
        _save(s)
    return tok


def valid_session(tok):
    if not tok:
        return False
    h = hashlib.sha256(tok.encode()).hexdigest()
    with _L:
        s = _sessions()
        exp = s.get(h)
        if not exp or exp < time.time():
            return False
        if exp - time.time() < TTL - 3600:         # sliding expiry, written at most hourly
            s[h] = time.time() + TTL; _save(s)
    return True


def drop_session(tok):
    if tok:
        with _L:
            s = _sessions(); s.pop(hashlib.sha256(tok.encode()).hexdigest(), None); _save(s)


def locked(ip):
    with _L:
        c = _FAILS.get(ip)
        return bool(c and c[1] > time.time())


def note_fail(ip):
    with _L:
        c = _FAILS.setdefault(ip, [0, 0])
        c[0] += 1
        if c[0] >= 5:
            c[1] = time.time() + 300; c[0] = 0


def clear_fail(ip):
    with _L:
        _FAILS.pop(ip, None)


# ---------------- http ----------------
class H(BaseHTTPRequestHandler):
    server_version = "council-ui"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *a):
        print(f"{self.client_address[0]} {self.command} {self.path.split('?')[0]} {fmt % a}", flush=True)     # no cookies, no bodies

    def _headers(self, ctype=None, extra=None, cache="no-store"):
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if ctype: self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items(): self.send_header(k, v)

    def _json(self, code, obj, extra=None):
        b = json.dumps(obj).encode()
        self.send_response(code); self._headers("application/json", extra); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _cookie(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE: return v
        return None

    def _authed(self):
        return valid_session(self._cookie())

    def _csrf_ok(self):
        if self.headers.get("X-Council") != "1":
            return False
        for h in ("Origin", "Referer"):
            v = self.headers.get(h)
            if v:
                host = re.sub(r"^https?://", "", v).split("/")[0]
                if host not in (self.headers.get("Host"), self._fwd("X-Forwarded-Host")): return False
        return True

    def _from_proxy(self):
        # the Authentik outpost runs in docker; only its bridge range may speak for the real client/host/scheme
        return self.client_address[0].startswith("172.") and 16 <= int(self.client_address[0].split(".")[1]) <= 31

    def _fwd(self, h):
        return self.headers.get(h) if self._from_proxy() else None

    def _secure(self):
        return self.server.is_tls or self._fwd("X-Forwarded-Proto") == "https"

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: return None
        return self.rfile.read(n) if n else b""

    # ----- verbs -----
    def do_GET(self): self._dispatch("GET")
    def do_POST(self): self._dispatch("POST")
    def do_PATCH(self): self._dispatch("PATCH")
    def do_DELETE(self): self._dispatch("DELETE")

    def _dispatch(self, method):
        path = self.path.split("?")[0]
        if path == "/auth/login" and method == "POST": return self._login()
        if path == "/auth/logout" and method == "POST":
            if not self._csrf_ok(): return self._json(403, {"error": "forbidden"})
            drop_session(self._cookie()); return self._json(200, {"ok": True}, {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})
        if path == "/auth/me": return self._json(200 if self._authed() else 401, {"authenticated": self._authed()})
        if path.startswith("/api/"):
            if not self._authed(): return self._json(401, {"error": "login required"})
            if method != "GET" and not self._csrf_ok(): return self._json(403, {"error": "forbidden"})
            return self._proxy(method, path)
        if method == "GET": return self._static(path)
        self._json(404, {"error": "not found"})

    def _login(self):
        ip = (self._fwd("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()[:45]
        if not self._csrf_ok(): return self._json(403, {"error": "forbidden"})
        if locked(ip): return self._json(429, {"error": "too many attempts; wait a few minutes"})
        raw = self._body()
        try: pw = str(json.loads(raw or b"{}").get("password", ""))
        except ValueError: pw = ""
        if pw and len(pw) < 200 and verify_password(pw):
            clear_fail(ip); tok = new_session()
            return self._json(200, {"ok": True}, {"Set-Cookie": f"{COOKIE}={tok}; Path=/; Max-Age={TTL}; HttpOnly; SameSite=Strict" + ("; Secure" if self._secure() else "")})
        note_fail(ip); time.sleep(0.6)
        self._json(401, {"error": "incorrect password"})

    def _static(self, path):
        if path in ("/", ""): path = "/index.html"
        name = os.path.normpath(path).lstrip("/")
        full = os.path.join(STATIC, name)
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self._json(404, {"error": "not found"})
        ext = os.path.splitext(full)[1]
        data = open(full, "rb").read()
        self.send_response(200); self._headers(TYPES.get(ext, "application/octet-stream"), cache="no-cache"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _proxy(self, method, path):
        up = None
        for m, rx, build in ROUTES:
            mt = re.match(rx, path)
            if m == method and mt:
                up = build(mt); break
        if up is None: return self._json(404, {"error": "not found"})
        body = None
        if method in ("POST", "PATCH"):
            body = self._body()
            if body is None: return self._json(413, {"error": "too large"})
        try:
            c = http.client.HTTPConnection(*UPSTREAM, timeout=40 if not is_stream(up) else 3600)
            c.request(method, up, body=body, headers={"Content-Type": "application/json"} if body is not None else {})
            r = c.getresponse()
        except OSError:
            return self._json(502, {"error": "the council service is not reachable"})
        ctype = r.getheader("Content-Type") or "application/json"
        self.send_response(r.status); self._headers(ctype)
        if is_stream(up) and ctype.startswith("text/event-stream"):
            self.send_header("Connection", "close"); self.end_headers()
            try:
                while True:
                    line = r.fp.readline()                      # SSE is line based: forward each line the moment it arrives
                    if not line: break
                    self.wfile.write(line); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                c.close()
            return
        data = r.read()
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); c.close()


class Srv(ThreadingHTTPServer):
    daemon_threads = True
    is_tls = False


def main():
    srv = Srv((BIND, PORT), H)
    print(f"council ui gateway on http://{BIND}:{PORT}  upstream {UPSTREAM[0]}:{UPSTREAM[1]}", flush=True)
    if TLS_PORT:
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(os.path.join(TLS_DIR, "server.crt"), os.path.join(TLS_DIR, "server.key"))
            t = Srv((BIND, TLS_PORT), H); t.is_tls = True
            t.socket = ctx.wrap_socket(t.socket, server_side=True)
            threading.Thread(target=t.serve_forever, daemon=True).start()
            print(f"council ui gateway on https://{BIND}:{TLS_PORT} (local CA)", flush=True)
        except Exception as e:                    # a bad certificate must never take the plain listener down
            print(f"https listener NOT started: {e}", flush=True)
    if os.environ.get("UI_BIND2"):                  # docker0 address, so the Authentik outpost container can reach us (same pattern as tempmail)
        threading.Thread(target=Srv((os.environ["UI_BIND2"], PORT), H).serve_forever, daemon=True).start()
        print(f"council ui gateway also on http://{os.environ['UI_BIND2']}:{PORT} (Authentik outpost)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
