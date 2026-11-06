# Architecture

> AI-generated project built on Agno; see the [README](../README.md) disclosure. This describes the reference build. Anything marked *(not shipped)* depends on code that is not in the repo.

## Processes and trust boundaries

| Unit | User | Network | Job |
|---|---|---|---|
| `agno-council` | `agno` | loopback + allow-list only | Agno Team + HTTP API on `127.0.0.1:7777`, unauthenticated, **never exposed** |
| `agno-ui` | `agno-ui` | LAN only | The only LAN-facing piece: login, static frontend, whitelisted proxy to the API |
| `agno-web` | `agno-web` | internet (public IPs only) | Search (SearXNG) + safe single-page fetch, over a unix socket |
| `agno-ops` | `agno-ops` | one SSH destination | Read-only check broker, check IDs only, unix socket |
| `agno-pcstate` | your user | SSH to the GPU PC | Copies validated AI Mode state; also relays on/off requests via a fixed command |
| `agno-facts` / `agno-vault-mirror` / `agno-digest` | root (timers) | none | Deterministic facts, markdown mirror, morning mail |
| `council-gpu-tunnel` | your user | SSH | `127.0.0.1:11235` -> LM Studio `:1234` on the GPU PC |

Every unit uses `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `CapabilityBoundingSet=` (empty), `SystemCallFilter=@system-service`, `UMask=0077`, memory/CPU/task caps, and `IPAddressDeny=any` + minimal `IPAddressAllow`. The council cannot read your `.ssh` or secrets, has no SSH key, and cannot reach the internet or the GPU PC directly, only the tunnel's loopback port.

## Request path (chat)
1. UI/CLI -> `POST /chat` or the AG-UI SSE stream (`uiapi.py`, event mapping in `aguimap.py`).
2. A **fresh** Agno `Team(mode=coordinate)` is built per request: leader = Orchestrator, delegates to at most one member at a time. Memory is **explicit only** ("remember ..." in chat, visible and deletable); Agno's automatic/agentic memory is off.
3. Every tool call goes through `permissions.guard_hook`: registry, per-agent allow-list, argument checks (paths, URLs, sizes), and an audit line in `tool-audit.jsonl`. Unknown tools are refused.
4. Every model request goes through `council_model.py`.

## The model gate (`council_model.py`)
Wraps the OpenAI client Agno uses.
- Process-global `threading.Semaphore(1)` around **every** request (sync, async, streaming; held until a stream ends; cancel-safe). Combined with `--parallel 1` on the model server this guarantees strictly sequential GPU use.
- Interactive requests need only "model loaded at the right context". **Unattended** requests (a job context) re-evaluate the policy before each request.
- Recovery: empty reply -> retry with a nudge (max 2); `<tool_call>` leaked into reasoning -> promoted to a real tool call; unknown tool / bad args -> one corrective retry; else HTTP 502 (`EmptyReply`). `max_tokens=6000` (3000 truncated ~20 % of thinking turns in testing). Logging can never break a request. Per-call log: `audit/model-calls.jsonl`.

## Unattended work
`window.py` (03:00-09:00 Europe/London via `zoneinfo`, DST-tested) + `policy.py` (all conditions must hold, see SETUP Layer 6) + `scheduler.py` (seeds useful jobs only: one `health_summary` and one `vault_review` per window, only if the deterministic lint digest changed; max 6 jobs/window, one at a time) + `jobs.py` (SQLite queue; jobs go `queued -> running -> done`, pause back to `queued` with the Agno session kept, max 3 attempts). `proactive.py` defines the job kinds; **severity is computed by deterministic code (`known.py`), the model only phrases it** so an old exited container is never "high".

## Research pipeline
`webtools.py` -> `agno-web` helper: SearXNG search, source tiers A/B/C by domain, single-page fetch that blocks private ranges, credentials in URLs, non-HTTPS, oversized/odd content types; flags prompt-injection phrases; queries containing internal addresses or secret-looking strings are refused. `citecheck.py` checks that factual claims carry `[S#]` cites. Researcher labels claims SOURCED / ASSUMED / RECOMMENDED.

## Knowledge
`knowledge.py`: SQLite FTS5 over the markdown mirror (no embeddings, no model call). Tools: `vault_search`, `vault_read` (path-traversal refused). Skills in `skills/*/SKILL.md` are loaded progressively by Agno.

## SysAdmin: look, never touch
Tools: `read_health_facts`, `list_checks`, `run_check` (ops broker), `propose_change`. A proposal is a database row. `council approve <id>` records a decision and **executes nothing**. Adding execution is deliberately left as a human decision (needs an approvals UI and a per-command allow-list).

## Windows "AI Mode" (`windows/`)
PowerShell tray app. States: OFF (default after every start, never auto-ON), ON, GAMING. Loads/unloads the model with `lms`, nudges process priorities (never realtime, never touches clocks/voltage/BIOS/drivers), and **gaming always wins**: it detects an actual game process (path prefixes, known exe names, Minecraft-Java command-line regex) and ignores launchers (Steam, Discord, Lunar, ...) which are listed in `never_games`. `selftest.ps1` encodes those rules.

## Known gaps (inherited from the reference build)
UI over plain HTTP on the LAN; unauthenticated loopback endpoints (`:7777`, `:11235`); critic runs on the same model family as the author (no diversity); approved-command execution not implemented; bridge companion + extension *(not shipped)*; digest/patch-watch mail helper *(not shipped)*; no CI.
