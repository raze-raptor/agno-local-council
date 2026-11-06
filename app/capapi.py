"""Read models for the capability sections (Agents, Tools, Skills, Workflows, Knowledge, Evaluations, Traces, PC access, Browser access) and the
Agno capability matrix. Everything shown is read from the live code or state files, not typed in, except the matrix text which is audited by hand."""
import json
import os
import time

import evals as EV
import knowledge as K
import permissions as P
import workflows as WF

AGENT_SKILLS = {}                                  # set by council.py at start-up
STATE = os.environ.get("AGNO_STATE_DIR", "/var/lib/agno")
SKILLS_DIR = os.environ.get("AGNO_SKILLS", "/opt/agno/skills")
I, PA, MI, NA, BL, DI = "IMPLEMENTED", "PARTIAL", "MISSING", "NOT APPLICABLE", "BLOCKED", "DISABLED"
# (area, feature, status, evidence / why)
MATRIX = [
    ("Agents", "Agents with roles, instructions, tools", I, "app/council.py build_*; 5 specialists + Orchestrator"),
    ("Agents", "Team coordinate mode", I, "TeamMode.coordinate; delegation events mapped in app/aguimap.py (44 tests)"),
    ("Agents", "Team broadcast mode", DI, "would run several model requests at once; the hardware allows one (global gate)"),
    ("Agents", "Team route / tasks modes", NA, "coordinate also answers directly and synthesises; tasks mode adds model calls for planning on a 16K model"),
    ("Agents", "Dynamic tool selection / callable factories", NA, "tools are static per role on purpose: small prompts, auditable lists (permissions.REGISTRY)"),
    ("Agents", "Workflows (sequential steps)", I, "app/workflows.py: 3 workflows on agno.workflow, step retries off, one at a time"),
    ("Agents", "Workflow conditions / loops / routers", PA, "available in agno.workflow, not used: every extra iteration is another model request"),
    ("Agents", "Agent Skills (progressive loading)", I, "/opt/agno/skills (5), agno.skills.LocalSkills; agents load full text only on demand (audited)"),
    ("Memory", "Session persistence and history", I, "SqliteDb agno.db + app/convo.py conversations; add_history_to_context, 2 runs"),
    ("Memory", "Session state", NA, "conversation store covers it; nothing needs shared mutable state"),
    ("Memory", "User memory / agentic memory", DI, "each memory update is an extra model request per run on one slow model; Memory section shows what exists"),
    ("Memory", "Learning stores / learned knowledge", DI, "agno.learn needs extra model calls to curate; revisit with a faster model"),
    ("Knowledge", "Knowledge base / RAG over the vault", PA, "app/knowledge.py SQLite FTS5 BM25 over 175 notes (no embeddings: that needs a second resident model)"),
    ("Knowledge", "Agno Knowledge + vector DB classes", MI, "not used; chromadb/lancedb are installed but an embedder would need a model load"),
    ("Knowledge", "Context providers", MI, "not in agno 3.1.12 package layout we use; vault tools cover the need"),
    ("Knowledge", "Context compression", PA, "compress_tool_results off (extra model call); tool outputs are size-capped in code instead"),
    ("Tools", "Custom Python tools", I, "app/tools.py, webtools.py, opstools.py (+ permission hook on all)"),
    ("Tools", "Built-in Agno toolkits", PA, "none adopted: custom tools are smaller and size-capped"),
    ("Tools", "Web search + extraction + source checking", I, "app/webtools.py (SearXNG), citecheck.py, 62 + 9 tests"),
    ("Tools", "System administration (read-only) + proposals", I, "app/opstools.py broker, 25 tests; changes only as proposals"),
    ("Tools", "Approved-command execution", MI, "proposals can be approved but nothing executes yet (decision for the owner)"),
    ("Tools", "MCP client / server", MI, "no MCP server needed yet; each one widens the tool surface"),
    ("Tools", "Code execution / isolated workspace / Git", MI, "Engineer cannot run code or write files; needs an approval design first"),
    ("Tools", "Browser control (Brave) / desktop control", DI, "prepared, not enabled: see PC access and Browser access"),
    ("Runtime", "REST API + SSE streaming", I, "app/council.py /v1/*, uigateway whitelist"),
    ("Runtime", "AG-UI", I, "custom adapter app/aguimap.py (SUBAGENT/STEP events); official router = 7 more packages and no sub-agent events"),
    ("Runtime", "A2A / MCP exposure", MI, "no consumer"),
    ("Runtime", "Run cancellation", I, "uiapi.cancel + Stop button; workflows cancel via Workflow.cancel_run"),
    ("Runtime", "Background execution + durable queue", I, "own job store (jobs.py, council.db) and scheduler; survives restarts"),
    ("Runtime", "Resume / reconnect", PA, "finished runs replay from the stored event log; a run in progress shows 'still working'"),
    ("Runtime", "Human approvals", I, "proposals + approvals page + audit (own implementation, not agno.hitl)"),
    ("Runtime", "Scheduling (03:00-09:00 London, gaming first)", I, "scheduler.py + policy.py (29 + 15 tests)"),
    ("Reliability", "Tool-level permissions + audit", I, "app/permissions.py guard_hook on every agent and the team (32 tests)"),
    ("Reliability", "Guardrails", PA, "untrusted tool output flagged in code; Agno PromptInjectionGuardrail is a keyword list on the user's own input, not adopted"),
    ("Reliability", "Pre/post run hooks", PA, "tool_hooks used; run-level hooks not needed yet"),
    ("Reliability", "Tracing / OpenTelemetry", PA, "agno.tracing needs openinference + OTel upgrade; per-run event log, tool audit and model-call log used instead"),
    ("Reliability", "Evaluations", I, "app/evals.py (Agno ReliabilityEval, 5 cases) + prompt_eval harness"),
    ("Reliability", "Telemetry off", I, "AGNO_TELEMETRY=false and telemetry=False on every agent"),
    ("Models", "LM Studio, one request at a time", I, "council_model.py gate (Semaphore 1) + LM Studio --parallel 1"),
    ("Models", "Structured outputs", PA, "not used; untested with this model through LM Studio"),
    ("Models", "Multimodal", BL, "Mellum is text-only"),
    ("Models", "Model routing / fallback", BL, "one resident model in 16 GB RAM; switching must be deliberate"),
    ("Models", "Hidden reasoning kept out of the UI", I, "aguimap.py never forwards reasoning text"),
]


def agents():
    from uiapi import roster
    sk = _skills()
    reg = P.registry_public()
    out = []
    for a in roster():
        nm = a.get("agent_name") or a["name"]
        mine = [r for r in reg if nm in r["agents"] or a["id"] in ("screen_operator",) and "ScreenOperator" in r["agents"]]
        a = dict(a); a["permitted_tools"] = [{"name": r["name"], "scope": r["scope"], "state": r["state"]} for r in mine]
        a["skills"] = [s["name"] for s in sk if nm in s["agents"]]
        a["state"] = "disabled" if a["id"] == "screen_operator" else "active"
        out.append(a)
    return {"agents": out}


def tools():
    reg = P.registry_public()
    groups = {}
    for r in reg: groups.setdefault(r["group"], []).append(r)
    return {"tools": reg, "bridge_flag": P.bridge_on(), "counts": {"active": sum(r["state"] == "active" for r in reg), "disabled": sum(r["state"] == "disabled" for r in reg)}}


def _skills():
    out = []
    for n in sorted(os.listdir(SKILLS_DIR)) if os.path.isdir(SKILLS_DIR) else []:
        try:
            txt = open(os.path.join(SKILLS_DIR, n, "SKILL.md")).read()
            desc = next((l.split(":", 1)[1].strip().strip('"') for l in txt.splitlines() if l.startswith("description:")), "")
        except OSError: continue
        out.append({"name": n, "description": desc, "agents": [a for a, ss in AGENT_SKILLS.items() if n in ss], "chars": len(txt)})
    return out


def skills():
    return {"skills": _skills(), "note": "Agents see only each skill's name and description; the full text is loaded on demand and every load is audited."}


def workflows():
    return {"workflows": WF.list_defs(), "runs": WF.runs(10)}


def knowledge():
    return {"index": K.stats(), "sources": ["Obsidian vault (read-only mirror /var/lib/agno-vault)"], "note": "Keyword (BM25) search. Semantic search would need an embedding model, which means a second resident model."}


def evaluations():
    d = EV.load()
    return {"cases": EV.cases(), "runs": d.get("runs", [])[:5]}


def traces(n=60):
    rows = []
    try:
        with open(P.AUDIT) as f: lines = f.readlines()[-400:]
        rows = [json.loads(l) for l in lines]
    except OSError: pass
    by = {}
    for r in rows: by[r["tool"]] = by.get(r["tool"], 0) + 1
    return {"recent": rows[-n:][::-1], "by_tool": by, "refused": sum(r["decision"] == "refused" for r in rows),
            "agno_tracing": {"enabled": False, "why": "This table is the trace: every tool call and refusal, with who asked. Agno's own OpenTelemetry tracing is off because it needs 8 more packages; each run's event log is stored with its conversation."}}


def _pc(prefixes, label):
    reg = [r for r in P.registry_public() if r["name"].startswith(prefixes)]
    cfg = os.path.join(STATE, "bridge", "bridge-config.json")
    return {"label": label, "state": "enabled" if P.bridge_on() else "prepared, disabled", "connected": False, "flag_file": P.BRIDGE_FLAG, "flag_present": P.bridge_on(),
            "tools": reg, "scopes": {"read": "looks only", "user": "ordinary user-level changes, every call needs approval", "admin": "not available (would need a UAC prompt on the PC)"},
            "config_present": os.path.exists(cfg)}


def bridge():
    import screenop as SO
    rows = []
    try:
        for ln in open(P.AUDIT).read().splitlines()[-400:]:
            r = json.loads(ln)
            if str(r.get("tool", "")).startswith(("browser_", "pc_", "bridge")): rows.append({k: r.get(k) for k in ("ts", "tool", "actor", "decision", "reason", "args")})
    except (OSError, ValueError): pass
    return {**SO.state(), "recent": rows[-25:][::-1], "tools": [r for r in P.registry_public() if r["name"].startswith(("browser_", "pc_"))]}


def pc(): return _pc(("pc_", "desktop_"), "PC access")
def browser(): return _pc(("browser_",), "Browser access")


def matrix():
    c = {}
    for _, _, s, _ in MATRIX: c[s] = c.get(s, 0) + 1
    return {"matrix": [{"area": a, "feature": f, "status": s, "evidence": e} for a, f, s, e in MATRIX], "counts": c}


def patches():
    """latest result of the hourly patch watch (written by patchwatch.py; it only runs while AI Mode is ON)"""
    try:
        d = json.load(open(os.path.join(os.environ.get("PATCHWATCH_DIR", "/var/lib/agno-patchwatch"), "latest.json")))
    except (OSError, ValueError):
        return {"results": [], "ts": None, "note": "no result yet: the watch runs hourly while AI Mode is ON"}
    return {"results": d.get("results", []), "ts": d.get("ts"), "last_skipped": d.get("last_skipped"), "skip_reason": d.get("skip_reason"), "last_alert": d.get("last_alert")}
