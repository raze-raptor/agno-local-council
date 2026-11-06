# AGENTS.md: instructions for an AI agent setting this up for its user

> **Disclosure to relay to your user:** this repo is AI-generated (Claude via Claude Code), built on the open-source Agno framework, written for one person's homelab and not independently audited. Its maintainer did not write the code. Say so before you start, and again if you fork or republish it.

You are helping a human stand up their own local multi-agent council. Be a careful installer, not an improviser.

## Ground rules
1. **Read before running.** Skim `README.md`, `docs/SETUP.md`, `docs/ARCHITECTURE.md`. Read any script before executing it, especially `scripts/`, `systemd/`, `lib/`, `windows/`.
2. **Ask before** firewall/router/DNS changes, opening any port beyond loopback/LAN, installing packages system-wide, creating system users, enabling services, anything on a machine that is not the one you were pointed at, and anything public-facing. Present each as a short list and wait.
3. **Never handle secrets in the clear.** There are none to configure: LM Studio needs no key. The only credential you create is the UI password; use `scripts/set-ui-password.py` and let the human type it. Never echo, log, commit or put one in argv. If the user pastes a key anyway, store it with mode 600 outside the repo and say so.
4. **Do not enable the browser/PC bridge** (`bridge/ENABLED`, `screenop.py`) or approved-command execution. They are out of scope for a first install.
5. **Add one layer at a time** (SETUP layers 0-7) and verify each with the command given there before continuing. "The command exited 0" is not verification: read the output (`council status`, a real `council chat`).
6. Do not weaken sandbox settings to make something work. If the unit cannot reach something, add the narrowest `IPAddressAllow` entry or path and tell the user.
7. Do not invent values. If you don't know the GPU host, model name or LAN range, ask.

## Collect details: now or later
Setup needs facts only the user has. Never guess them and never make the user answer everything before you start.
1. Copy `config/setup-answers.example.md` to `setup-answers.md` in the repo root (it is gitignored). It lists every detail, which layer needs it, and a sensible default.
2. Ask the questions in **one message**, grouped by layer, starting with Layer 1 (the model). For each, tell the user they can: **answer now**, say **"later"**, or say **"skip"** (they don't want that layer). Offer the default where there is one. Don't ask for anything outside the layers they want.
3. Record each answer in the file with its status (`given` / `later` / `skip`). **No secrets in that file.** Passwords are typed by the user into the script, never relayed through you.
4. Start immediately on every layer whose details are `given`. A `later` item only blocks the layers listed next to it; say which ones, and move on.
5. End every session with a short status: done and verified / waiting on you (the exact `later` items) / skipped. Append what you verified to the progress log in the file.
6. **Resuming:** when the user comes back ("continue the council setup", "here's the GPU IP"), read `setup-answers.md` first, update the answered rows, re-run the placeholder grep for the newly unblocked layer, and continue from there. Don't re-ask anything marked `given`.

## Procedure
1. `git clone`, then `PYTHON=python3 tests/run_all.sh` needs the venv, so install first: `sudo ./scripts/install-core.sh` (read it first; it creates the `agno` user, `/opt/agno`, `/etc/agno`, `/var/lib/agno`, a venv, the unit and `/usr/local/bin/council`).
2. Fill `/etc/agno/agno.env` from `config/agno.env.example`.
3. Replace placeholders: `grep -rnE "YOUR_USER|GPU_PC_HOST|SERVER1_LAN_IP|SERVER2_(LAN_IP|HOST)" .` and fix every hit that applies to the layers you are enabling (leave the rest alone and tell the user which files still carry placeholders).
4. Start the model server and (if remote) the tunnel, confirm `curl 127.0.0.1:11235/v1/models` lists the model.
5. `systemctl enable --now agno-council`; `council status` must show the model loaded at the configured context; `council chat "hello"` must answer. A 503 from `/chat` carries a reason string, so read it.
6. Run `PYTHON=/opt/agno/venv/bin/python tests/run_all.sh`. Expect 9 suites ok. If anything fails, report it verbatim; do not delete assertions to turn it green.
7. Optional layers in order: facts/notes -> web research -> UI -> overnight work. After each, run its verification from SETUP.md.
8. Confirm the sandbox actually holds: the `agno` user cannot read the human's `~/.ssh`, and `systemd-analyze security agno-council` is not worse than the reference (3.1).

## Definition of done
- `council status` shows the model loaded and the right unattended-work state ("blocked - why" is a valid, healthy answer if the PC isn't idle).
- A real delegated question works (e.g. `council chat "Ask the critic to review: def f(x): return x/0"`).
- Tests pass; no placeholders remain in enabled layers; the user knows how to stop it (`systemctl disable --now agno-council`) and roll back (SETUP: Rollback).
- You have told the user, plainly, what you did **not** verify (e.g. an overnight run, TLS, the bridge).

## Editing the code
Follow `docs/CUSTOMIZING.md`. A new tool needs a `Spec` in `app/permissions.py` or it is refused. Keep severity decisions deterministic. Add a check to `tests/` for new logic. Keep the AI-generated disclosure in the README.
