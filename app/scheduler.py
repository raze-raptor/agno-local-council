"""Unattended-work scheduler. One tick every TICK seconds; at most ONE job runs at a time; nothing starts outside the window.

Eligibility is decided by policy.proactive_decision (window, PC state, AI Mode, gaming, login, LM Studio, model, RAM/VRAM).
If not eligible the job simply stays queued (never an error). Mid-job, the gate re-checks the same policy before every model
request (council_model.JOB_CTX); the request in flight always completes, the job then goes back to 'queued' with its session
kept so the next eligible window continues it.
"""
import json
import os
import threading
import time

import council_model as CM
import jobs
import policy
import proactive as PRO
import opstools as OT
import webtools as WT
import citecheck as CC
import window as W

TICK = int(os.environ.get("AGNO_SCHED_TICK", "30"))
FACTS = os.environ.get("AGNO_FACTS", "/var/lib/agno-facts/health.json")
REPORT_DIR = os.environ.get("AGNO_REPORT_DIR", "/var/lib/agno/reports")
KV = {}  # tiny in-memory cache; the durable copy of 'last reviewed digest' lives in jobs.kv


class Scheduler(threading.Thread):
    def __init__(self, build_team, inference_state, audit):
        super().__init__(daemon=True, name="scheduler")
        self.build_team, self.inference_state, self.audit = build_team, inference_state, audit
        self.running_job = None
        self.last_decision = {"ok": False, "why": ["not evaluated yet"], "ts": 0}
        self.runs_in_window = {}
        self._stop = threading.Event()

    # ---- decisions ----
    def decision(self, now=None):
        pc = policy.read_pc_state()
        ok, why = policy.proactive_decision(self.inference_state(), pc, now)
        self.last_decision = {"ok": ok, "why": why, "ts": time.time(), "pc_age_s": (pc or {}).get("age_s")}
        return ok, why

    # ---- seeding: useful work only (collectors in proactive.py decide; per-kind enable + per-night cap come from the Tasks view) ----
    def seed(self, now=None):
        return PRO.seed(now, ask=OT._ask)

    # ---- one tick ----
    def tick(self, now=None):
        if self.running_job:
            return "busy"
        if W.in_window(now):
            self.seed(now)
        job = jobs.next_queued()
        if not job:
            return "idle"
        ok, why = self.decision(now)
        if not ok:
            return "blocked: " + "; ".join(why)
        wd = W.window_date(now)
        if self.runs_in_window.get(wd, 0) >= PRO.get_config()["max_per_night"]:
            return "window run cap reached"
        self.runs_in_window[wd] = self.runs_in_window.get(wd, 0) + 1
        self.running_job = job["id"]
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"job-{job['id']}").start()
        return f"started job {job['id']}"

    def _run(self, job):
        jid = job["id"]
        ctx = {"policy": lambda: policy.proactive_decision(self.inference_state(), policy.read_pc_state()), "paused": None}
        tok = CM.JOB_CTX.set(ctx)
        wctx = WT.begin_run()
        t0 = time.time()
        try:
            j = jobs.start(jid)
            base = PRO.refresh_prompt(j, ask=OT._ask)
            prompt = base if j["attempts"] <= 1 else "Continue this task from where you left off, without repeating finished parts. Original task:\n" + base
            out = self.build_team(j["session_id"]).run(prompt)
            status = str(getattr(out, "status", "")).split(".")[-1].lower()
            text = out.content if isinstance(out.content, str) else json.dumps(out.content, default=str)
            if ctx["paused"]:
                jobs.mark(jid, "queued", note="paused: " + ctx["paused"][:200])
                jobs.event(jid, "paused", ctx["paused"])
                self.audit(event="job_paused", job=jid, reason=ctx["paused"][:200])
            elif status == "error" or not (text or "").strip():
                if j["attempts"] >= jobs.MAX_ATTEMPTS:
                    jobs.mark(jid, "failed", finished_at=time.time(), note=(text or "empty")[:200]); jobs.event(jid, "failed", (text or "")[:200])
                else:
                    jobs.mark(jid, "queued", note="retry after error: " + (text or "")[:120]); jobs.event(jid, "retry", (text or "")[:120])
            else:
                ver = CC.check(text, wctx["sources"])
                text = (text or "") + CC.footer(ver)
                path = self._report(j, text, time.time() - t0)
                jobs.mark(jid, "done", finished_at=time.time(), report_path=path, note=None)
                jobs.event(jid, "done", f"{time.time() - t0:.0f}s")
                PRO.on_done(j)
                self.audit(event="job_done", job=jid, kind=j["kind"], secs=round(time.time() - t0))
        except CM.PolicyPause as e:
            jobs.mark(jid, "queued", note="paused: " + str(e)[:200]); jobs.event(jid, "paused", str(e))
        except Exception as e:
            if ctx["paused"]:
                jobs.mark(jid, "queued", note="paused: " + ctx["paused"][:200]); jobs.event(jid, "paused", ctx["paused"])
            else:
                jobs.mark(jid, "queued", note=f"error {type(e).__name__}"); jobs.event(jid, "error", type(e).__name__)
                self.audit(event="job_error", job=jid, error=type(e).__name__)
        finally:
            CM.JOB_CTX.reset(tok)
            self.running_job = None

    def _report(self, j, text, secs):
        os.makedirs(REPORT_DIR, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d", time.localtime())
        path = os.path.join(REPORT_DIR, f"{stamp}-{j['kind']}-{j['id']}.md")
        with open(path, "w") as f:
            f.write(f"# {j['title']}\n\n- job {j['id']} ({j['kind']}), attempts {j['attempts']}, ran {secs:.0f}s, generated by the local council (Mellum). "
                    f"Unverified model output.\n\n{text}\n")
        return path

    def run(self):
        while not self._stop.wait(TICK):
            try:
                self.tick()
            except Exception as e:
                self.audit(event="scheduler_error", error=type(e).__name__)
