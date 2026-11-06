#!/usr/bin/env python3
"""agno-web: the ONLY component that touches the internet for the council. stdlib only, own user, own sandbox.

Listens on a unix socket (no IP exposure). Does two things: search (via the local SearXNG container) and fetch (one HTTPS page ->
cleaned text). Every request is validated here; the systemd unit ALSO forbids private/LAN/loopback destinations at the cgroup level,
so a bug in this file still cannot reach the LAN (see agno-web.service).

Safety rules (each is unit-tested in test_webhelper.py):
 - https only, port 443, hostname with a dot, no credentials, no IP literals
 - every resolved address must be globally routable; the connection is PINNED to the validated address (no DNS rebinding)
 - redirects (max 3) are re-validated hop by hop
 - body limit 1.5 MB, 20 s total, identity encoding (no decompression bombs), text-like content types only, no attachments
 - HTML -> text: scripts/styles/templates/iframes/forms/nav/aside and visually hidden subtrees dropped; zero-width/bidi characters removed
 - lines that look like instructions aimed at an AI are removed and counted (never passed on)
 - search queries containing internal addresses/hostnames or secret-looking strings are refused (they would leave the house)
 - hourly request caps; full JSONL audit trail
"""
import hashlib
import html.parser
import http.client
import ipaddress
import json
import os
import re
import socket
import socketserver
import ssl
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

SOCK = os.environ.get("AGNO_WEB_SOCK", "/run/agno-web/web.sock")
SEARX = os.environ.get("AGNO_SEARX", "http://172.17.0.3:8080")      # container IP on the docker default bridge (allowed /32 in the unit)
AUDIT = os.environ.get("AGNO_WEB_AUDIT", "/var/lib/agno-web/audit.jsonl")
MAX_BYTES = 1_500_000
TOTAL_TIMEOUT = 20.0
MAX_REDIRECTS = 3
PAGE_CHARS = 6000
HOURLY = {"search": 40, "fetch": 60}
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json", "application/xml", "text/xml",
              "application/atom+xml", "application/rss+xml")

TIER_A = ("gov", "gov.uk", "edu", "ac.uk", "readthedocs.io", "github.com", "gitlab.com", "pypi.org", "python.org", "kernel.org", "ubuntu.com",
          "debian.org", "microsoft.com", "mozilla.org", "arxiv.org", "huggingface.co", "rfc-editor.org", "w3.org", "ietf.org", "nist.gov",
          "docker.com", "nvidia.com", "amd.com", "lmstudio.ai", "agno.com", "cloudflare.com", "openssh.com", "sqlite.org", "postgresql.org",
          "kubernetes.io", "nginx.org", "pi-hole.net", "syncthing.net", "restic.net", "llama.cpp", "ggml.ai", "searxng.org", "openssh.org", "openbsd.org", "man7.org", "kernel.org", "gnu.org", "freedesktop.org", "systemd.io", "freebsd.org", "netfilter.org", "wireguard.com", "apache.org", "iana.org", "rfc-editor.org", "owasp.org", "mitre.org", "cve.org")
TIER_B = ("wikipedia.org", "stackoverflow.com", "stackexchange.com", "serverfault.com", "superuser.com", "askubuntu.com", "bbc.co.uk",
          "reuters.com", "apnews.com", "arstechnica.com", "theregister.com", "lwn.net", "nature.com", "science.org", "archlinux.org")
BLOCKED = ("bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "pastebin.com", "hastebin.com", "paste.ee", "ghostbin.com", "onion")

INJECTION = [re.compile(p, re.I) for p in (
    r"ignore (all |any |the |your )?(previous|prior|above|earlier) (instructions|prompts?|rules|messages)",
    r"disregard (all |any |the |your )?(previous|prior|above|earlier)",
    r"\byou are now\b", r"\bnew (system )?instructions?\s*:", r"\bsystem prompt\b", r"reveal (your|the) (system|hidden|secret)",
    r"</?(system|assistant|tool_call|im_start|im_end|function_call)>", r"\[/?INST\]", r"<\|(im_start|im_end|system|assistant)\|>",
    r"do not (tell|inform|mention (this )?to) the user", r"(execute|run) the following (command|code|script)", r"curl[^\n|]{0,200}\|\s*(ba)?sh",
    r"\b(forward|send|email|post) (this|the|all|your) .{0,40}(to|at) \S+@\S+", r"as an ai (language )?model,? you (must|should|will)",
)]
INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­]")
SECRETISH = re.compile(r"(?i)(password|passwd|secret|api[_ -]?key|private key|BEGIN [A-Z ]*KEY|bearer\s+\S{8,}|token\s*[=:]\s*\S{6,})")
INTERNAL = re.compile(r"(?i)(\b(10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b|\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b|example\.com|\.home\b|\.lan\b|\.local\b|server1|server2)")
LONGTOKEN = re.compile(r"[A-Za-z0-9+/_=-]{28,}")

_LOCK = threading.Lock()
_COUNT = {"search": [], "fetch": []}


class Refused(Exception):
    def __init__(self, reason, code=400):
        super().__init__(reason); self.reason, self.code = reason, code


# ---------------- policy helpers (pure, unit-tested) ----------------
def domain_tier(host):
    h = host.lower().rstrip(".")
    for s in BLOCKED:
        if h == s or h.endswith("." + s):
            return "blocked"
    if h.startswith("docs.") or h.startswith("developer.") or h.startswith("learn."):
        return "A"
    for s in TIER_A:
        if h == s or h.endswith("." + s):
            return "A"
    for s in TIER_B:
        if h == s or h.endswith("." + s):
            return "B"
    return "C"


def query_problem(q):
    if not q or not q.strip():
        return "empty query"
    if len(q) > 200:
        return "query too long"
    if INTERNAL.search(q):
        return "query mentions internal addresses/hostnames; it would leave the house"
    if SECRETISH.search(q) or LONGTOKEN.search(q):
        return "query looks like it contains a secret or token"
    return None


def validate_url(url, resolver=None):
    """-> (host, [validated ipv4 addresses]) or raises Refused. `resolver(host)` -> list of address strings (injectable for tests)."""
    if len(url) > 2000:
        raise Refused("url too long")
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise Refused("only https is allowed")
    if p.username or p.password or "@" in p.netloc:
        raise Refused("credentials in url are not allowed")
    host = (p.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        raise Refused("hostname needs a dot")
    if p.port not in (None, 443):
        raise Refused("only port 443 is allowed")
    if re.fullmatch(r"[0-9.]+|0x[0-9a-f.]+|\[.*\]", host) or ":" in host:
        raise Refused("ip literals are not allowed")
    if re.search(r"\.(local|lan|home|internal|localhost|corp|intranet|arpa)$", host):
        raise Refused("internal hostname")
    if domain_tier(host) == "blocked":
        raise Refused("domain is on the block list")
    try:
        addrs = resolver(host) if resolver else sorted({a[4][0] for a in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)})
    except OSError:
        raise Refused("could not resolve host", 502)
    if not addrs:
        raise Refused("no addresses", 502)
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if not ip.is_global or ip.is_multicast:
            raise Refused(f"host resolves to a non-public address ({a})")
    return host, addrs


class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "iframe", "svg", "canvas", "form", "nav", "aside", "footer", "object", "embed", "head"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "section", "article", "hr", "dt", "dd"}
    VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "col", "base", "embed", "param", "track"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.title, self.desc, self.hidden_dropped, self._in_title, self.links = [], [], "", "", 0, False, []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta" and a.get("name", "").lower() in ("description", "og:description") and not self.desc:
            self.desc = a.get("content", "")[:300]
        if tag == "title":
            self._in_title = True
        if tag in self.VOID:
            if tag in self.BLOCK and not self.stack: self.out.append("\n")
            return
        style = re.sub(r"\s+", "", a.get("style", "").lower())
        hidden = ("hidden" in a or a.get("aria-hidden") == "true" or "display:none" in style or "visibility:hidden" in style
                  or "font-size:0" in style or "opacity:0" in style or "left:-9999" in style or "text-indent:-9999" in style)
        skip = tag in self.SKIP or hidden
        if tag == "a" and not skip and not any(s for _, s in self.stack) and a.get("href", "").startswith(("https://", "/")) and len(self.links) < 200:
            self.links.append(a["href"])
        if hidden and tag not in self.SKIP:
            self.hidden_dropped += 1
        self.stack.append((tag, skip))
        if tag in self.BLOCK and not any(s for _, s in self.stack):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in self.BLOCK and not any(s for _, s in self.stack):
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not any(s for _, s in self.stack):
            self.out.append(data)


def clean_text(raw, content_type):
    """-> (title, description, text, stats). Text has invisible chars removed and instruction-like lines dropped."""
    stats = {"hidden_dropped": 0, "injection_lines_removed": 0, "invisible_chars_removed": 0}
    title = desc = ""; links = []
    if "html" in content_type or "xml" in content_type:
        p = _Text()
        try:
            p.feed(raw); p.close()
        except Exception:
            pass
        title, desc, text, stats["hidden_dropped"] = p.title.strip()[:200], p.desc, "".join(p.out), p.hidden_dropped; links = p.links
    else:
        text = raw
    n_before = len(text); text = INVISIBLE.sub("", text); stats["invisible_chars_removed"] = n_before - len(text)
    lines, kept = [re.sub(r"[ \t\r\f\v]+", " ", l).strip() for l in text.split("\n")], []
    for l in lines:
        if not l:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if any(rx.search(l) for rx in INJECTION):
            stats["injection_lines_removed"] += 1
            kept.append("[line removed: looks like instructions aimed at an AI]")
        else:
            kept.append(l)
    stats["links"] = links
    return title, desc, "\n".join(kept).strip(), stats


# ---------------- network ----------------
def _connect_pinned(host, ip, timeout):
    sock = socket.create_connection((ip, 443), timeout=timeout)
    ctx = ssl.create_default_context(); ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx.wrap_socket(sock, server_hostname=host)


def safe_get(url, deadline, resolver=None, connector=_connect_pinned):
    """-> dict(final_url, status, content_type, body(bytes)). Raises Refused."""
    for hop in range(MAX_REDIRECTS + 1):
        host, addrs = validate_url(url, resolver)
        left = deadline - time.time()
        if left <= 1:
            raise Refused("timeout", 504)
        ssock = connector(host, addrs[0], min(10, left))
        conn = http.client.HTTPSConnection(host, 443, timeout=min(10, left))
        conn.sock = ssock
        p = urllib.parse.urlsplit(url)
        conn.request("GET", (p.path or "/") + ("?" + p.query if p.query else ""), headers={
            "Host": host, "User-Agent": "Mozilla/5.0 (compatible; HomelabCouncil/1.0; +read-only research)", "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity", "Accept-Language": "en", "Connection": "close"})
        r = conn.getresponse()
        if r.status in (301, 302, 303, 307, 308):
            loc = r.getheader("Location") or ""; conn.close()
            if hop == MAX_REDIRECTS or not loc:
                raise Refused("too many redirects", 502)
            url = urllib.parse.urljoin(url, loc); continue
        ctype = (r.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if "attachment" in (r.getheader("Content-Disposition") or "").lower():
            conn.close(); raise Refused("download/attachment refused")
        if ctype and not any(ctype == t for t in TEXT_TYPES):
            conn.close(); raise Refused(f"content type {ctype!r} not allowed")
        body, total = [], 0
        while True:
            if time.time() > deadline:
                conn.close(); raise Refused("timeout while reading", 504)
            chunk = r.read(65536)
            if not chunk:
                break
            total += len(chunk); body.append(chunk)
            if total > MAX_BYTES:
                break
        enc = r.headers.get_content_charset() or "utf-8"
        conn.close()
        return {"final_url": url, "status": r.status, "content_type": ctype or "text/html", "body": b"".join(body)[:MAX_BYTES], "charset": enc, "truncated_download": total > MAX_BYTES}
    raise Refused("redirect loop", 502)


def _rate(kind):
    now = time.time()
    with _LOCK:
        _COUNT[kind] = [t for t in _COUNT[kind] if now - t < 3600]
        if len(_COUNT[kind]) >= HOURLY[kind]:
            raise Refused(f"hourly {kind} limit reached ({HOURLY[kind]})", 429)
        _COUNT[kind].append(now)


def audit(**kw):
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        with _LOCK, open(AUDIT, "a") as f:
            f.write(json.dumps(kw) + "\n")
    except OSError:
        pass


def do_search(q, n):
    bad = query_problem(q)
    if bad:
        audit(action="search_refused", reason=bad); raise Refused(bad)
    _rate("search")
    url = SEARX + "/search?" + urllib.parse.urlencode({"q": q, "format": "json", "safesearch": "1", "language": "en"})
    t0 = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "council"}), timeout=15) as r:
        data = json.load(r)
    out, seen = [], set()
    for x in data.get("results", []):
        u = x.get("url", ""); h = (urllib.parse.urlsplit(u).hostname or "").lower()
        tier = domain_tier(h) if h else "blocked"
        if tier == "blocked" or u in seen:
            continue
        seen.add(u)
        out.append({"title": (x.get("title") or "")[:140], "url": u, "snippet": re.sub(r"\s+", " ", INVISIBLE.sub("", x.get("content") or ""))[:240],
                    "domain": h, "tier": tier, "fetchable": u.startswith("https://"), "engines": x.get("engines", [])})
    out.sort(key=lambda d: {"A": 0, "B": 1, "C": 2}[d["tier"]])      # stable: search order kept inside a tier
    audit(action="search", q=q[:200], results=len(out), ms=round((time.time() - t0) * 1000))
    return {"query": q, "results": out[:max(1, min(n, 10))], "note": "snippets are leads, not evidence: fetch a page before relying on it"}


def do_fetch(url, offset, max_chars, resolver=None, connector=_connect_pinned):
    _rate("fetch")
    t0 = time.time()
    try:
        g = safe_get(url, t0 + TOTAL_TIMEOUT, resolver, connector)
    except Refused as e:
        audit(action="fetch_refused", url=url[:300], reason=e.reason); raise
    raw = g["body"].decode(g["charset"], errors="replace") if g["charset"] else g["body"].decode("utf-8", errors="replace")
    title, desc, text, stats = clean_text(raw, g["content_type"])
    host = urllib.parse.urlsplit(g["final_url"]).hostname or ""
    links, seen_l = [], set()
    for h in stats.pop("links", []):
        u = urllib.parse.urljoin(g["final_url"], h).split("#")[0]
        if u.startswith("https://") and u not in seen_l and u != g["final_url"] and domain_tier(urllib.parse.urlsplit(u).hostname or "") != "blocked":
            seen_l.add(u); links.append(u)
    sha = hashlib.sha256(text.encode()).hexdigest()
    page = text[offset:offset + max_chars]
    res = {"final_url": g["final_url"], "status": g["status"], "domain": host, "tier": domain_tier(host), "title": title, "description": desc,
           "content_type": g["content_type"], "chars_total": len(text), "offset": offset, "text": page, "more": offset + max_chars < len(text),
           "sha256": sha, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cleaning": stats, "download_truncated": g["truncated_download"], "links": links[:40], "thin": len(text) < 300,
           "full_text_for_verification": text[:80000]}
    audit(action="fetch", url=url[:300], final=g["final_url"][:300], status=g["status"], chars=len(text), sha=sha[:16], tier=res["tier"], cleaning=stats, ms=round((time.time() - t0) * 1000))
    return res


# ---------------- unix-socket HTTP server ----------------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def address_string(self): return "unix"

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path == "/health":
                return self._send(200, {"status": "ok", "searx": SEARX})
            if u.path == "/search":
                return self._send(200, do_search(q.get("q", ""), int(q.get("n", 8))))
            if u.path == "/fetch":
                return self._send(200, do_fetch(q.get("url", ""), max(0, int(q.get("offset", 0))), min(int(q.get("max", PAGE_CHARS)), PAGE_CHARS)))
            self._send(404, {"error": "not found"})
        except Refused as e:
            self._send(e.code, {"error": "refused", "reason": e.reason})
        except Exception as e:
            audit(action="error", path=u.path, err=type(e).__name__)
            self._send(502, {"error": "failed", "reason": type(e).__name__})


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(os.path.dirname(SOCK), exist_ok=True)
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = UnixServer(SOCK, H)
    os.chmod(SOCK, 0o660)
    audit(action="start", searx=SEARX)
    srv.serve_forever()


if __name__ == "__main__":
    main()
