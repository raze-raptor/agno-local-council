# Customizing

> AI-generated project built on Agno; see the [README](../README.md) disclosure.

| I want to... | Edit |
|---|---|
| Use another model / server | `config/agno.env.example` -> `LMSTUDIO_*`. The model must support tool calls. Re-check `max_tokens` in `council.py` and `COUNCIL_TOOL_ROUNDS` (env, default 10) in `council_model.py` |
| Change the unattended window / timezone | `app/window.py` (`LON`, `START`, `END`), `systemd/agno-digest.timer`, `TZ` |
| Rename servers (`server1`, `server2`) | `lib/ops/ops_broker.py` `HOSTS`, `app/known.py`, `app/opstools.py`, `app/proactive.py`, prompts in `app/council.py` |
| Add or change an agent | `app/council.py` (`Agent(...)` factories and the Orchestrator prompt), then register its tools in `app/permissions.py` with an allow-list |
| Add a tool | write it in `app/tools.py` (or a new module), add a `Spec(...)` in `app/permissions.py` (kind `read`/`user`, which agents, argument checks), add a test in `tests/test_permissions.py`. A tool without a Spec is refused |
| Add an overnight job | `app/proactive.py` (kind table + prompt builder + deterministic input), seed it in `app/scheduler.py` |
| Add a check the SysAdmin may run | `lib/ops/ops_core.py` `CHECKS` (fixed argv list, no shell). Keep it read-only |
| Add a skill | `skills/<name>/SKILL.md` with `name` + `description` frontmatter, assign in `app/council.py` `skills_for` |
| Change what counts as "gaming" | `windows/config.json` (`exe_names`, `path_prefixes`, `never_games`) then `windows/selftest.ps1` |
| Change the personality | the `STYLE` constant and Orchestrator prompt in `app/council.py` |

Rules the original build followed (keep them): measure prompt changes against real outputs rather than trusting a prompt that merely reads well; never let a model decide severity; never add a write or exec tool without an approvals path; redact by field name rather than by string length.
