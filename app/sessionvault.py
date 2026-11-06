"""Encrypted storage for saved browser sessions (cookies + local/session storage). Session data is credential-equivalent (a leaked
file resumes the owner as logged in), so it is never written in the clear and never appears in the audit log (only the session's name and
site are ever recorded). Key is generated once on first use and lives next to the bridge's other secrets, root/agno only, mode 600."""
import json
import os
import re

from cryptography.fernet import Fernet

DIR = "/var/lib/agno/bridge/sessions"
KEY_FILE = "/var/lib/agno/bridge/sessions.key"


def _fernet():
    if not os.path.exists(KEY_FILE):
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        open(KEY_FILE, "wb").write(Fernet.generate_key())
        os.chmod(KEY_FILE, 0o600)
    return Fernet(open(KEY_FILE, "rb").read())


_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,60}$")


def save(name, data):
    """data: {"url":..., "cookies": [...], "storage": {...}}. Raises ValueError on a bad name."""
    if not _NAME.match(name): raise ValueError("session name must be short, letters/digits/-/_ only")
    os.makedirs(DIR, exist_ok=True)
    blob = _fernet().encrypt(json.dumps(data).encode())
    path = os.path.join(DIR, name + ".enc")
    with open(path, "wb") as f: f.write(blob)
    os.chmod(path, 0o600)
    return {"site": data.get("url", ""), "cookies": len(data.get("cookies", []))}


def load(name):
    if not _NAME.match(name): raise ValueError("session name must be short, letters/digits/-/_ only")
    path = os.path.join(DIR, name + ".enc")
    if not os.path.exists(path): return None
    return json.loads(_fernet().decrypt(open(path, "rb").read()))


def list_names():
    if not os.path.isdir(DIR): return []
    return sorted(n[:-4] for n in os.listdir(DIR) if n.endswith(".enc"))
