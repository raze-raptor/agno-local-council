"""Server1-side broker for the PC companion and Brave extension. INERT: no agent has these tools and council.py does not import this module.
It exists so activation later is a configuration step, with the permission checks already written and tested. A call goes out only if ALL hold:
the flag file exists, permissions.decide() allows it, user-scope calls carry an approval id, and the path arguments sit under the broker's own allow-list
(the companion checks again). Requests are HMAC-signed; the key lives in a root-only file that is created at activation."""
import hashlib
import hmac
import json
import os
import time
import uuid

import permissions as P

KEY_FILE = "/var/lib/agno/bridge/key"                    # created at activation (owned by the agno user, mode 600)
READ_ROOTS = ("C:\\Users\\YOU\\Documents\\council-share",)
WRITE_ROOTS = ("C:\\Users\\YOU\\Documents\\council-share\\out",)


def _under(path, roots):
    p = os.path.normpath(str(path).replace("/", "\\")).lower()
    return any(p == r.lower() or p.startswith(r.lower().rstrip("\\") + "\\") for r in map(os.path.normpath, roots)) and ".." not in str(path)


def sign(key, ts, nonce, body):
    return hmac.new(key, f"{ts}{nonce}{body}".encode(), hashlib.sha256).hexdigest()


def call(actor, tool, args, approval_id=None, transport=None, key=None):
    """transport(url_path, headers, body) -> dict. Returns {"ok":..} or {"refused": reason}."""
    ok, why = P.decide(actor, tool, args)
    if not ok: return {"refused": why}
    sp = P.REGISTRY[tool]
    if sp.approval == "per_call" and not approval_id: return {"refused": "this action needs the owner's approval first"}
    if tool == "pc_read_file" and not _under(args.get("path", ""), READ_ROOTS): return {"refused": "path outside the read folders"}
    if tool == "pc_write_file" and not _under(args.get("path", ""), WRITE_ROOTS): return {"refused": "path outside the write folders"}
    if transport is None: return {"refused": "no transport: the bridge is not connected"}
    body = json.dumps({"tool": tool, "args": args, "approval": approval_id}, sort_keys=True)
    ts, nonce = int(time.time()), uuid.uuid4().hex
    res = transport("/call", {"X-Ts": str(ts), "X-Nonce": nonce, "X-Sig": sign(key, ts, nonce, body)}, body)
    P._audit(actor=actor, tool=tool, scope=sp.scope, args=P._redact(args), decision="sent", approval=approval_id)
    if sp.untrusted: res["untrusted"] = True
    return res
