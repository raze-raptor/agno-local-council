"""Reliability evaluations: does the right agent call the right tool? Uses Agno's ReliabilityEval (expected tool calls, no LLM judge, so it costs only
the runs themselves). Manual only: each case is a real model run through the global gate, one case at a time. Results are kept in evals.json."""
import json
import os
import threading
import time

from agno.eval.reliability import ReliabilityEval

PATH = os.path.join(os.environ.get("AGNO_STATE_DIR", "/var/lib/agno"), "evals.json")
_RUN = threading.Lock()
# (id, who, prompt, tools it must call, allow other tool calls)
CASES = [
    ("time_tool", "orchestrator", "What time is it in London right now?", ["get_time"], True),
    ("sysadmin_facts", "sysadmin", "Which systemd units are currently failed on server1?", ["read_health_facts", "run_check"], True),
    ("strategist_vault", "strategist", "In one paragraph: what is the council's hardware limit I should plan around? Check my notes.", ["vault_search", "get_skill_instructions"], True),
    ("vague_no_delegate", "orchestrator", "Fix it.", [], False),
    ("engineer_syntax", "engineer", "Write a python function is_palindrome(s) and check its syntax with your tool.", ["check_python_syntax"], True),
]


def load():
    try: return json.load(open(PATH))
    except (OSError, ValueError): return {"runs": []}


def cases():
    return [{"id": c[0], "agent": c[1], "prompt": c[2], "expects": c[3]} for c in CASES]


def run(builders, only=None):
    """builders: {'orchestrator': fn, 'sysadmin': fn, ...}. Blocking; call from a thread."""
    if not _RUN.acquire(blocking=False): return {"error": "an evaluation is already running"}
    res = {"started": time.time(), "results": [], "status": "running"}
    try:
        d = load(); d["runs"] = ([res] + d["runs"])[:10]; json.dump(d, open(PATH, "w"))
        for cid, who, prompt, exp, extra in CASES:
            if only and cid not in only: continue
            t0 = time.time(); row = {"id": cid, "agent": who, "expects": exp}
            try:
                out = builders[who]().run(prompt)
                kw = dict(allow_additional_tool_calls=extra, expected_tool_calls=exp)
                ev = ReliabilityEval(name=cid, **({"team_response": out} if who == "orchestrator" else {"agent_response": out}), **kw)
                r = ev.run(print_results=False)
                names = [getattr(t, "tool_name", "?") for t in (getattr(out, "tools", None) or [])]
                if exp == []:                                    # "ask, don't act": no tool or delegation may have been used
                    passed = not names
                else:
                    passed = bool(r and r.eval_status == "PASSED")
                    if len(exp) > 1 and who != "orchestrator": passed = passed or any(n in names for n in exp)   # any one of the listed tools is enough
                row.update(passed=passed, tools=names[:8], secs=round(time.time() - t0, 1), answer=str(getattr(out, "content", ""))[:160])
            except Exception as e:
                row.update(passed=False, error=f"{type(e).__name__}: {e}"[:160], secs=round(time.time() - t0, 1))
            res["results"].append(row); _flush(res)
        res["status"] = "done"
    finally:
        res["ended"] = time.time(); _flush(res); _RUN.release()
    return res


def _flush(res):
    d = load()
    d["runs"] = [res if r.get("started") == res["started"] else r for r in d.get("runs", [])]
    json.dump(d, open(PATH, "w"))
