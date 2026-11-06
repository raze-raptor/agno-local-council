# Council setup answers (template)

The setup agent copies this to `setup-answers.md` (gitignored) and fills it in with the user.
**Never put passwords, keys or tokens here.** Status is `given`, `later` (user will provide it) or `skip` (layer not wanted).
Each `later` item blocks only the layer in the "Needed for" column; everything else keeps going.

| Key | Question for the user | Needed for | Default if unsure | Status | Value |
|---|---|---|---|---|---|
| `server_name` | What do you call the machine that runs the council? | 1 | `server1` | | |
| `server_lan_ip` | That machine's LAN IP (for the UI bind and firewall allow-list)? | 5 | none, ask | | |
| `lan_cidr` | Your LAN range, e.g. `192.168.1.0/24`? | 5 | none, ask | | |
| `model_where` | Does the model run on the same machine or on a separate GPU machine? | 1, 2 | same machine | | |
| `model_server` | Which server: LM Studio, llama.cpp, Ollama (`/v1`)? | 1 | LM Studio | | |
| `model_url` | Its OpenAI-compatible URL (ends in `/v1`) | 1 | `http://127.0.0.1:11235/v1` via tunnel | | |
| `model_name` | Exact model id (must support tool calls) | 1 | none, ask | | |
| `model_context` | Context length the model is loaded with | 1 | 16384 | | |
| `gpu_host` | GPU machine hostname/IP | 2, 6 | skip if same machine | | |
| `gpu_user` | SSH username on the GPU machine | 2, 6 | none, ask | | |
| `gpu_os` | GPU machine OS (Windows is needed for AI Mode) | 6 | Windows | | |
| `ssh_key_ok` | Is the key-based SSH from server to GPU machine set up? | 2 | no, agent helps | | |
| `service_user` | Linux user that owns the tunnel/poller SSH key | 2, 6 | the user running setup | | |
| `want_ui` | Want the password-protected web UI? | 5 | yes | | |
| `want_research` | Want web research (needs Docker + SearXNG)? | 4 | no | | |
| `want_overnight` | Want unattended overnight jobs? | 6 | no | | |
| `timezone` | Your timezone | 6 | Europe/London | | |
| `window` | Overnight hours (code default 03:00-09:00) | 6 | 03:00-09:00 | | |
| `notes_path` | Folder of markdown/Obsidian notes to use as knowledge base | 3 | skip | | |
| `second_host` | A second server for read-only SysAdmin checks (name, IP, SSH user)? | 3 | skip | | |
| `mail_helper` | How should digests/alerts be sent (you must supply an `alert_mail.py`)? | 6 | skip | | |

UI password: not stored here. The user types it into `scripts/set-ui-password.py` when they are ready.

## Progress log (agent appends: date, layer, what was verified, how)
