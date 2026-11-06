#!/usr/bin/env python3
"""Create the scrypt hash file the UI gateway reads (lib/uigateway.py: UI_AUTH_FILE, default /etc/agno-ui/auth.json).
Usage: sudo python3 scripts/set-ui-password.py   (prompts; the password is never echoed, stored in clear or put in argv)."""
import getpass, hashlib, json, os, secrets, sys
path = os.environ.get("UI_AUTH_FILE", "/etc/agno-ui/auth.json")
pw = getpass.getpass("New UI password (16+ chars): ")
if len(pw) < 16 or pw != getpass.getpass("Again: "):
    sys.exit("too short or not matching")
n, r, p = 2 ** 15, 8, 1
salt = secrets.token_bytes(16)
h = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=2 ** 26)
os.makedirs(os.path.dirname(path), exist_ok=True)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
with os.fdopen(fd, "w") as f:
    json.dump({"salt": salt.hex(), "n": n, "r": r, "p": p, "hash": h.hex()}, f)
print(f"wrote {path}; make it readable by the agno-ui user (chgrp agno-ui {path})")
