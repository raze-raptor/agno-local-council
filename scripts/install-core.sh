#!/bin/bash
# Installs ONLY the core council (council.py + CLI) as a hardened systemd service. Run as root from the repo root.
# It does not set up the model server, the SSH tunnel, the web helper, the ops broker or the UI (see docs/SETUP.md).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
cd "$(dirname "$0")/.."
id agno >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/agno --shell /usr/sbin/nologin agno
install -d -o root -g agno -m 0750 /opt/agno /opt/agno/app /opt/agno/skills /etc/agno
install -d -o agno -g agno -m 0700 /var/lib/agno /var/lib/agno/workspaces /var/lib/agno/audit /var/lib/agno/reports
python3 -m venv /opt/agno/venv
/opt/agno/venv/bin/pip install -q --upgrade pip
/opt/agno/venv/bin/pip install -q -r requirements.lock   # exact pins the reference install ran
cp -r app/. /opt/agno/app/ && cp -r skills/. /opt/agno/skills/
chown -R root:agno /opt/agno/app /opt/agno/skills && chmod -R o-rwx,g-w /opt/agno/app /opt/agno/skills
[ -f /etc/agno/agno.env ] || install -o root -g agno -m 0640 config/agno.env.example /etc/agno/agno.env
install -m 0755 bin/council /usr/local/bin/council
sed "s#IPAddressAllow=.*#IPAddressAllow=127.0.0.1/32#" systemd/agno-council.service > /etc/systemd/system/agno-council.service
echo "Installed. Edit /etc/agno/agno.env (LMSTUDIO_BASE_URL / LMSTUDIO_MODEL), start your model server, then:"
echo "  systemctl daemon-reload && systemctl enable --now agno-council && council status"
