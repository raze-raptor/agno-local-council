"""Repeatable multi-step work as Agno Workflows. Steps run strictly one after another (so the global one-request gate is never contended), each step is
a normal council specialist (same tool permission hook), step retries are off (a retry would be a second model request), and one workflow runs at a time.
Runs are stored in council.db (table workflow_runs) with the output of every step."""
import json
import os
import sqlite3
import threading
import time
import uuid

from agno.workflow import Step, StepOutput, Workflow

DB = os.path.join(os.environ.get("AGNO_STATE_DIR", "/var/lib/agno"), "council.db")
_RUN = threading.Lock()
_ACTIVE = {}                                    # run_id -> Workflow (for cancel)

DEFS = {
    "build_and_review": {"title": "Build, review, revise", "needs": "what to build",
                         "steps": [("Draft", "Engineer", "Write the code for this request. Keep it short and correct:\n\n{input}"),
                                   ("Review", "Critic", "Request:\n{input}\n\nCode to review:\n{Draft}\n\nReview the code against the request. List concrete problems first, most serious first; say plainly if it is sound."),
                                   ("Revise", "Engineer", "Request:\n{input}\n\nYour earlier code:\n{Draft}\n\nReview findings:\n{Review}\n\nRevise the code to fix the findings. Return the final code and one line on what changed.")]},
    "research_brief": {"title": "Research, then fact-check", "needs": "a question to research",
                       "steps": [("Research", "Researcher", "Research this with source checking and cite sources [S#]:\n\n{input}"),
                                 ("Fact-check", "Critic", "Question:\n{input}\n\nResearch answer:\n{Research}\n\nMark every claim that has no [S#] citation or looks invented, and say what is solid.")]},
    "server_review": {"title": "Server check, then second opinion", "needs": "what to look at on server1 or server2 (or 'general health')",
                      "steps": [("Diagnose", "SysAdmin", "Using read-only checks, look into: {input}"),
                                ("Second opinion", "Critic", "Task:\n{input}\n\nDiagnosis:\n{Diagnose}\n\nFlag conclusions not backed by a named check, and anything alarmist given the documented known state.")]},
}


def _fill(tpl, step_input):
    """Each step gets the original request AND the named earlier step outputs (Agno passes only the previous output by default)."""
    out = tpl.replace("{input}", str(step_input.input or "")[:2000])
    for name, so in (step_input.previous_step_outputs or {}).items():
        out = out.replace("{" + name + "}", str(getattr(so, "content", "") or "")[:3500])
    return out


def _executor(builder, tpl):
    def run(step_input):
        r = builder().run(_fill(tpl, step_input))
        return StepOutput(content=r.content if isinstance(r.content, str) else json.dumps(r.content, default=str))
    return run


def _con():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("create table if not exists workflow_runs(id text primary key, name text, input text, status text, started real, ended real, steps text, error text)")
    return c


def list_defs():
    return [{"name": k, "title": v["title"], "needs": v["needs"], "steps": [{"name": s[0], "agent": s[1]} for s in v["steps"]], "gated": True, "retries": 0} for k, v in DEFS.items()]


def runs(limit=15):
    c = _con()
    try:
        rows = c.execute("select id,name,input,status,started,ended,steps,error from workflow_runs order by started desc limit ?", (limit,)).fetchall()
    finally:
        c.close()
    return [{"id": r[0], "name": r[1], "input": r[2][:200], "status": r[3], "started": r[4], "ended": r[5], "steps": json.loads(r[6] or "[]"), "error": r[7]} for r in rows]


def get(run_id):
    return next((r for r in runs(200) if r["id"] == run_id), None)


def _save(run_id, **kw):
    c = _con()
    try:
        sets = ", ".join(f"{k}=?" for k in kw)
        c.execute(f"update workflow_runs set {sets} where id=?", (*[json.dumps(v) if k == "steps" else v for k, v in kw.items()], run_id)); c.commit()
    finally:
        c.close()


def build(name, builders):
    d = DEFS[name]
    steps = [Step(name=s[0], executor=_executor(builders[s[1]], s[2]), max_retries=0) for s in d["steps"]]
    return Workflow(name=name, description=d["title"], steps=steps)


def start(name, text, builders):
    if name not in DEFS: return {"error": "unknown workflow"}
    text = (text or "").strip()[:2000]
    if not text: return {"error": "give the workflow something to work on"}
    if not _RUN.acquire(blocking=False): return {"error": "a workflow is already running; one at a time on this hardware"}
    rid = uuid.uuid4().hex[:12]
    c = _con(); c.execute("insert into workflow_runs values(?,?,?,?,?,?,?,?)", (rid, name, text, "running", time.time(), None, "[]", None)); c.commit(); c.close()
    threading.Thread(target=_exec, args=(rid, name, text, builders), daemon=True).start()
    return {"id": rid, "status": "running"}


def _exec(rid, name, text, builders):
    steps, status, err = [], "done", None
    try:
        wf = build(name, builders); _ACTIVE[rid] = wf
        for ev in wf.run(input=text, run_id=rid, stream=True, stream_events=True):
            kind = getattr(ev, "event", "")
            if kind == "StepCompleted":
                steps.append({"name": getattr(ev, "step_name", "?"), "output": str(getattr(ev, "content", "") or "")[:6000]}); _save(rid, steps=steps)
            elif kind in ("WorkflowCancelled", "RunCancelled"): status = "cancelled"
            elif kind in ("WorkflowError", "RunError"): status, err = "failed", str(getattr(ev, "error", "") or getattr(ev, "content", ""))[:300]
    except Exception as e:
        status, err = "failed", f"{type(e).__name__}: {e}"[:300]
    finally:
        _ACTIVE.pop(rid, None); _save(rid, status=status, ended=time.time(), error=err); _RUN.release()


def cancel(rid):
    wf = _ACTIVE.get(rid)
    if not wf: return {"error": "not running"}
    try: ok = wf.cancel_run(rid)
    except Exception as e: return {"error": str(e)[:100]}
    return {"cancelled": bool(ok)}
