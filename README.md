# Local AI Council (built on Agno)

> ## 🛑 NOT A PORTFOLIO PIECE: employers, recruiters, reviewers, please read
>
> **This project is not evidence of the repo owner's programming skill.** It was **entirely vibe-coded**: an AI (Claude) wrote all of the code, docs and tests from plain-English instructions. The owner did not write it and does not claim to have. **Please do not judge the owner's abilities by it, and do not treat it as their work.**
>
> **Everyone else: it's free for the taking.** Fork it, change it, ship it, and claim it as your own if you like. It's MIT-licensed and I don't want credit. (The MIT notice stays in the license file; that's the only requirement.)

> ## ⚠️ Read this first: this is AI-written, and it is not the maintainer's own work
>
> **This entire project was written by an AI** (Claude, made by Anthropic, driven through Claude Code), working from instructions given by a hobbyist homelab owner. **The person who runs the repository did not write this code** and is not claiming to have. Treat it as what it is: **AI-generated code, open-sourced as a worked example.**
>
> - It is **built on top of [Agno](https://github.com/agno-agi/agno)** (an open-source agent framework, Apache-2.0). The agent, team and tool machinery is Agno's. This repo is the *glue, safety layers and UI* that were generated around it. It is **not affiliated with or endorsed by** Agno, Anthropic, LM Studio or anyone else named here.
> - It was built for **one specific home setup** and tested there. It has **not been independently audited**. Some parts were never run for real (see [What is and isn't verified](#what-is-and-isnt-verified)). Read the code before you run it, especially anything that touches SSH, your browser, or your servers.
> - Provided **as is, with no warranty and no support promise**. See [LICENSE](LICENSE).
> - Names like `server1`, `server2` and `YOUR_USER` are placeholders from the original setup. Personal hostnames, IPs, accounts, passwords and tokens were stripped before publishing. If you spot one that slipped through, please open an issue.

## What it is

A **"council" of cooperating local AI agents** that runs on your own hardware, with **no cloud API keys**:

- An **Orchestrator** you talk to, which delegates to specialists: **Engineer** (code), **Critic** (review), **SysAdmin** (read-only server diagnosis), **Researcher** (notes + sandboxed web search with citations), **Strategist** (business/option analysis).
- A **small server (server1) runs the agents**; **all LLM inference happens on a separate GPU machine** reached over an SSH tunnel to **LM Studio**. The server never needs a GPU.
- It can do **unattended overnight work** (health briefings, vault review, research digests) but only inside a time window and only when a strict policy says the GPU PC is idle, not gaming, and has RAM/VRAM to spare.
- It is **safety-first by construction**: no agent has a shell, nothing it proposes is executed, every tool call passes an allow-list guard, and the one internet-facing piece is a separate sandboxed process.
- Optional extras: a password-protected **web UI / PWA**, a **Windows tray app ("AI Mode")** that loads/unloads the model and yields to games, and a *disabled-by-default* **browser/PC control bridge**.

```
 you ──► Web UI / CLI ──► Orchestrator ─┬─► Engineer ─┐
         (LAN, password)   (Agno Team)  ├─► Critic    │ every model call goes through ONE gate
                                        ├─► SysAdmin ─┤  (1 request at a time, policy re-checked)
                                        ├─► Researcher│            │
                                        └─► Strategist┘            ▼
   server1 (server, no GPU)                               SSH tunnel :11235
   ├ agno-council  :7777 (loopback, sandboxed)                     │
   ├ agno-web      (only thing with internet; unix socket)         ▼
   ├ agno-ops      (read-only check catalogue; unix socket)   GPU PC: LM Studio :1234
   └ agno-ui       :8975 (the only LAN-facing piece)          + AI Mode tray app (Windows)
```

## Repository map

| Path | What it is |
|---|---|
| `app/` | The council service (runs as the unprivileged `agno` user): `council.py` (agents, HTTP API), `council_model.py` (one-request-at-a-time model gate + reply recovery), `policy.py`/`window.py`/`scheduler.py`/`jobs.py` (unattended work), `permissions.py` (tool allow-list guard), `proactive.py` (overnight job kinds), `webtools.py`/`citecheck.py` (research), `opstools.py`, `uiapi.py`/`aguimap.py` (UI streaming), `screenop.py`/`bridge_broker.py` (browser/PC bridge, disabled) |
| `lib/` | Companion processes: `uigateway.py` (login + whitelisted proxy), `webhelper.py` (sandboxed search/fetch), `ops/` (read-only diagnostic broker), `pcstate-poller.py`, `collect-facts.py`, `patchwatch.py`, `agno-digest.py`, `mirror-vault.sh` |
| `ui/` | Static web frontend (vanilla JS PWA) |
| `windows/` | "AI Mode" PowerShell tray app + config for the GPU PC |
| `systemd/` | Hardened unit files for every service/timer |
| `skills/` | Agno skills (markdown) loaded progressively by the agents |
| `bin/council` | CLI: `status`, `chat`, `job`, `jobs`, `report`, `proposals`, `approve/reject` |
| `config/agno.env.example` | The service's (secret-free) environment |
| `scripts/` | `install-core.sh`, `set-ui-password.py` |
| `tests/` | 9 self-contained suites (191 checks), `tests/run_all.sh` |
| `docs/` | [SETUP.md](docs/SETUP.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CUSTOMIZING.md](docs/CUSTOMIZING.md) |
| [AGENTS.md](AGENTS.md) | **Instructions for an AI agent** setting this up for its user: it asks for the details it needs, and the user can answer now, say "later", or skip a layer |
| `config/setup-answers.example.md` | Template the agent fills in (no secrets) so setup can pause and resume |

## Quick start (core council only)

You need: a Linux server with `systemd` + Python 3 (3.12 on Ubuntu 24.04 in the reference install), and an OpenAI-compatible model endpoint (LM Studio, llama.cpp server, Ollama's `/v1`...). The model can be on the same machine.

```bash
git clone <this repo> && cd <this repo>
sudo ./scripts/install-core.sh                 # user, venv, hardened systemd unit, `council` CLI
sudoedit /etc/agno/agno.env                     # point LMSTUDIO_BASE_URL / LMSTUDIO_MODEL at your model
sudo systemctl daemon-reload && sudo systemctl enable --now agno-council
council status
council chat "Say hello and list the specialists you can delegate to."
```

Run the tests first if you like: `PYTHON=/opt/agno/venv/bin/python tests/run_all.sh`.

Everything beyond that (SSH tunnel to a GPU box, the web UI, web search, ops broker, overnight jobs, Windows AI Mode) is optional and layered on. **Follow [docs/SETUP.md](docs/SETUP.md)** and enable one piece at a time.

**Using an AI agent?** Just tell it: *"Set up the council from this repo."* It will read [AGENTS.md](AGENTS.md), ask you for the details it needs (model, GPU machine, LAN range...), let you answer now or later, set up everything it can straight away, and pick up where it left off when you return.

## Design principles (why it looks the way it does)

1. **Small local models are unreliable, so wrap them.** The model gate retries empty replies, rescues tool calls that leaked into the reasoning text, corrects bad arguments once, then fails loudly instead of looping.
2. **One GPU request at a time.** A process-wide semaphore plus `--parallel 1` on the model server. Two agents never fight over VRAM.
3. **Agents can look, not touch.** No shell, no file write, no code execution. `propose_change` only *records* a proposal; approving it does not run it (execution is deliberately not implemented).
4. **Untrusted by default.** Anything from the web, a note or a page is treated as data; the web helper blocks internal addresses, secret-looking queries and prompt-injection phrases, and answers must cite sources with tiers.
5. **Least privilege per process.** Each piece is its own user with `ProtectSystem=strict`, empty capability set, syscall filter and `IPAddressDeny=any` plus a tiny allow-list.
6. **Fail closed, stay quiet.** If the GPU PC's state is stale, unknown or unhealthy, unattended jobs simply stay queued. Nothing wakes machines, logs in, or errors.
7. **The model's output is labelled "unverified".** Deterministic scripts decide severity; the model only writes it up.

## What is and isn't verified

Honest status, from the original build (not re-verified by you until you run it):

- ✅ **Unit tests**: 9 suites / 191 checks pass (window/DST maths, policy gate, scheduler, permission guard, model-gate recovery, refusal detection, research pipeline, proactive jobs).
- ✅ Ran live in the original setup: chat, delegation, the one-at-a-time gate, pause/resume of jobs when AI Mode flips, the UI, web research.
- ⚠️ **Never observed live in the original setup**: a full unattended overnight run followed by the morning email digest; the empty-reply recovery path (only unit-tested); real-device UI performance.
- ⚠️ **Browser/PC control bridge** (`screenop.py`, `bridge_broker.py`) depends on a Windows companion and a browser extension that are **not included** in this repo. Treat it as an architecture sketch.
- ⚠️ Two of the original tests asserted the bridge was *off* / user memory was *off*; the build later turned those on, so those two assertions were removed rather than left failing.
- ⚠️ Hostnames, user names, IPs and Windows paths are baked in as placeholders (`YOUR_USER`, `GPU_PC_HOST`, `SERVER1_LAN_IP`, `SERVER2_HOST`...). You must replace them. `grep -rnE "YOUR_USER|GPU_PC_HOST|SERVER1_LAN_IP|SERVER2_(LAN_IP|HOST)" .` lists every spot.
- The unattended window is hard-coded to **03:00-09:00 Europe/London** in `app/window.py`.
- Tested only on Ubuntu 24.04 (server) + Windows (GPU PC; exact version not recorded) with LM Studio and one model. Other models may need prompt tuning (see [CUSTOMIZING.md](docs/CUSTOMIZING.md)).

## Credits

- **[Agno](https://github.com/agno-agi/agno)**: the agent framework this is built on (Apache-2.0).
- **[LM Studio](https://lmstudio.ai/)**: local model serving on the GPU PC. **[SearXNG](https://github.com/searxng/searxng)**: self-hosted metasearch for the Researcher.
- Written by **Claude (Anthropic) via Claude Code**, directed by a homelab hobbyist who did not author the code.

## License

MIT, see [LICENSE](LICENSE). Agno and other dependencies keep their own licenses.
