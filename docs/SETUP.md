# Setup guide

> AI-generated project built on Agno; see the disclosure in the [README](../README.md). Enable **one layer at a time** and verify each before the next. Every layer is optional except Layer 0-1.

Placeholders you must replace everywhere (`grep -rnE "YOUR_USER|GPU_PC_HOST|SERVER1_LAN_IP|SERVER2_(LAN_IP|HOST)" .`):

| Placeholder | Meaning |
|---|---|
| `YOUR_USER` | Unix/Windows account that owns the SSH keys and runs the tunnel/poller |
| `GPU_PC_HOST` | hostname/IP of the machine running LM Studio (skip if the model runs on the server) |
| `SERVER1_LAN_IP` | the council server's LAN address (UI bind + systemd IP allow-list) |
| `SERVER2_HOST` / `SERVER2_LAN_IP` | an optional second server the SysAdmin may run read-only checks on |
| `/path/to/your/notes` | an Obsidian/markdown folder used as the knowledge base (optional) |
| `server1` / `server2` | just names; rename freely (they appear in prompts, `known.py` and the ops broker host table) |

## Layer 0: prerequisites
- Linux server with systemd, Python 3.12-ish, `ssh`, `sqlite3` (FTS5 is used for note search).
- An OpenAI-compatible endpoint serving a **tool-calling** model. The reference used LM Studio with `mellum2.1-12b-a2.5b-thinking` at **16 384 context**, `--parallel 1`, ~8 GB VRAM. Any model works if it can call tools; small ones need the recovery layer in `council_model.py` (already on).
- No cloud keys are needed anywhere.

## Layer 1: core council (server)
```bash
sudo ./scripts/install-core.sh
sudoedit /etc/agno/agno.env            # LMSTUDIO_BASE_URL (must end in /v1), LMSTUDIO_MODEL, LMSTUDIO_CONTEXT
sudo systemctl daemon-reload && sudo systemctl enable --now agno-council
curl -s 127.0.0.1:7777/health ; council status
```
`council status` should say the model is loaded and at what context. `council chat "hello"` returns an answer. If `/chat` returns **503** the message explains why (model not loaded, wrong context, ...): the gate refuses rather than guessing.
Safe to restart any time: running jobs re-queue, sessions persist in `/var/lib/agno/agno.db`.

> The unit's `IPAddressDeny=any` blocks everything except the allow-list. The installer allows only loopback. If your model server is on another host, add its IP to `IPAddressAllow=` (or keep using a loopback tunnel, below).

## Layer 2: remote GPU over an SSH tunnel (recommended topology)
1. On the GPU machine: install LM Studio, enable its server on `127.0.0.1:1234`, and an SSH server. Load the model **with the same context you configured**: `lms load <model> --gpu max -c 16384 --parallel 1 --identifier <model>`.
2. Create a dedicated key on the server for the tunnel user, add its public half to the GPU PC's `authorized_keys` (on Windows OpenSSH admin accounts use `C:\ProgramData\ssh\administrators_authorized_keys`).
3. Edit `systemd/council-gpu-tunnel.service` (`User=`, `YOUR_USER@GPU_PC_HOST`), then:
   ```bash
   sudo cp systemd/council-gpu-tunnel.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now council-gpu-tunnel
   curl -s 127.0.0.1:11235/v1/models        # should list your model
   ```
   The council then talks to `http://127.0.0.1:11235/v1`, the default in `agno.env.example`.
Security note: the reference accepted an unauthenticated loopback model endpoint and a broadly-trusted key between the two boxes. Tighten both if your threat model is stricter (restrict the key with `permitopen="127.0.0.1:1234"`).

## Layer 3: facts + notes (feeds SysAdmin / Researcher / overnight jobs)
- `lib/collect-facts.py` (root, no model, no network) writes `/var/lib/agno-facts/health.json`; edit its paths first (the backup log path is a placeholder). Install the `agno-facts` service+timer from `systemd/`, copying `lib/*` to `/usr/local/lib/agno/`.
- `lib/mirror-vault.sh` mirrors a **markdown-only** copy of your notes to `/var/lib/agno-vault/` every 10 min (`agno-vault-mirror` timer). Edit `SRC` first. The council reads only the mirror.
- `lib/ops/` is the read-only check broker. Fixed catalogue (`ops_core.CHECKS`), check IDs plus at most one validated argument, redacted and capped output, hourly rate limit. For a second host the broker runs `ssh <key> "<check> [arg]"`. **The remote wrapper is not in this repo**: install `ops_core.py` there and write a ~10-line script that reads `SSH_ORIGINAL_COMMAND`, splits `check [arg]` and prints `ops_core.run_check(check, arg)`; pin it in `authorized_keys` as `command="/path/wrapper",restrict ssh-ed25519 ...` so that key can run nothing else. Skip this and use only `server1` (local) if you have one host.

## Layer 4: web research (the only internet-facing process)
1. Run SearXNG (Docker), JSON output enabled. Note its container IP.
2. Set `AGNO_SEARX` in `systemd/agno-web.service`, install it (`User=agno-web`).
3. The council reaches it through a unix socket only. The helper refuses internal addresses, secret-looking queries, credentials in URLs, non-HTTPS, and flags injection phrases; results are tiered A/B/C and the answer needs `[S#]` citations (`citecheck.py`).

## Layer 5: web UI
```bash
sudo python3 scripts/set-ui-password.py                       # scrypt hash, never stored in clear
sudo mkdir -p /opt/agno-ui && sudo cp -r ui /opt/agno-ui/static
# edit systemd/agno-ui.service: UI_BIND=SERVER1_LAN_IP, IPAddressAllow=your LAN CIDR
sudo cp systemd/agno-ui.service /etc/systemd/system/ && sudo systemctl enable --now agno-ui
```
Open `http://SERVER1_LAN_IP:8975/`. The gateway is the only LAN-facing piece: login cookie is HttpOnly+SameSite=Strict, and it proxies an explicit allow-list of routes to the loopback API. **Plain HTTP on a LAN is a known gap**: PWA install needs HTTPS (`UI_TLS_PORT`/`UI_TLS_DIR`; supply your own cert, none is shipped). Do not expose it to the internet without putting real auth (SSO/VPN) in front.

## Layer 6: unattended overnight work
Needs: the PC-state poller + AI Mode on the GPU PC.
- Windows: copy `windows/` to `C:\Users\YOU\AIMode\`, edit `config.json` (`lms_path`, model key, game lists), run `AIMode.ps1` in your interactive session (tray icon). Default state at start is **OFF**. Its `selftest.ps1` checks the game-detection rules (launchers open ≠ gaming).
- Server: `lib/pcstate-poller.py` copies the PC's `state.json` over a fixed SSH command every 30 s into `/run/agno-pcstate/state.json`. Install `agno-pcstate.service` (runs as your user, which holds the SSH key).
- Work starts only when **all** hold: inside 03:00-09:00 Europe/London, PC state fresh (judged by the PC's own clock), AI Mode ON, not gaming, Windows logged in, LM Studio up, expected model loaded at the expected context, RAM/commit/VRAM headroom. Re-checked before **every** model request; a refused request pauses the job and it resumes next window. Verify with `council status` ("unattended work: ALLOWED / blocked - why").
- Optional: `agno-digest.timer` emails new reports at 09:10; `lib/agno-digest.py` imports `send_alert` from a mail helper that is **not in this repo** (put your own `alert_mail.py` on the path and set the `sys.path.insert` line; `patchwatch.py` uses it too).

## Layer 7: browser/PC bridge (disabled; do not enable casually)
`screenop.py` + `bridge_broker.py` implement a permission-gated "Screen Operator". They need a Windows companion service and a browser extension **that are not in this repo**. Tools stay refused while `/var/lib/agno/bridge/ENABLED` is absent, every action asks you in chat, and passwords/card numbers/codes are never typed by the council. Treat this layer as a design reference.

## Verify
```bash
PYTHON=/opt/agno/venv/bin/python tests/run_all.sh      # 9 suites, expect all ok
council status
sudo systemd-analyze security agno-council             # reference build scored 3.1 "OK"
```
Also confirm the sandbox yourself: `sudo -u agno cat /root/.ssh/id_* ` must fail, and the unit must not be able to reach the internet.

## Rollback
Stop and disable the units, delete `/opt/agno /etc/agno /usr/local/lib/agno`, `userdel agno`. State in `/var/lib/agno*` is kept until you delete it.
