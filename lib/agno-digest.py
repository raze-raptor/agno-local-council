#!/usr/bin/env python3
"""Morning digest (root, systemd timer 09:10 Europe/London): emails the owner the council reports produced since the last digest, using the
existing alert mailbox helper. Sends nothing if there is nothing new. Model output is labelled as unverified."""
import json, os, sys, time
sys.path.insert(0, "/path/to/bin")
REPORTS = "/var/lib/agno/reports"; STATE = "/var/lib/agno-digest/sent.json"
os.makedirs(os.path.dirname(STATE), exist_ok=True)
sent = set(json.load(open(STATE))) if os.path.exists(STATE) else set()
new = sorted(f for f in os.listdir(REPORTS) if f.endswith(".md") and f not in sent) if os.path.isdir(REPORTS) else []
if not new:
    sys.exit(0)
body = ["Overnight output from the local AI council (Mellum on the GPU PC). Unverified model output: check before acting.\n"]
for f in new:
    body.append("=" * 60 + f"\n{f}\n" + open(os.path.join(REPORTS, f)).read()[:4000])
from alert_mail import send_alert
send_alert("\n".join(body)[:20000], subject=f"Council morning digest {time.strftime('%Y-%m-%d')} ({len(new)} report{'s' if len(new) != 1 else ''})")
json.dump(sorted(sent | set(new)), open(STATE, "w"))
