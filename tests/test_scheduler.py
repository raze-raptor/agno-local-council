import json, os, sys, tempfile, time, types
T = tempfile.mkdtemp(prefix="schedtest_")
os.environ.update(AGNO_JOBS_DB=f"{T}/c.db", AGNO_PC_STATE=f"{T}/pc.json", AGNO_FACTS=f"{T}/facts.json", AGNO_REPORT_DIR=f"{T}/reports",
                  AGNO_VAULT_MIRROR=f"{T}/vault", AGNO_SCHED_TICK="1")
sys.path.insert(0, "app"); sys.path.insert(0, ".")
from datetime import datetime
from zoneinfo import ZoneInfo
import council_model as CM, jobs, policy, window as W, scheduler as SCH, vaultlint
from fakes import Fake, comp  # reuse the scripted upstream
L = ZoneInfo("Europe/London")
CLOCK = [datetime(2026, 11, 4, 4, 0, tzinfo=L)]
W.now_london = lambda: CLOCK[0]
def setclock(h, m=0): CLOCK[0] = datetime(2026, 11, 4, h, m, tzinfo=L)
def pc(**kw):
    d = dict(ai_mode="ON", gaming=False, logged_in=True, lmstudio_up=True, ram_available_mb=1500, commit_pct=85, vram_free_mib=700, poll_ts=time.time())
    d.update(kw); json.dump(d, open(f"{T}/pc.json", "w"))
ok = []
def check(n, c, x=""): ok.append(bool(c)); print(("PASS " if c else "FAIL ") + n, x)
os.makedirs(f"{T}/vault"); open(f"{T}/vault/A.md", "w").write("---\ntype: x\nlast_verified: 2020-01-01\n---\n# A [[Nope]]\n")
json.dump({"ts": time.time(), "alerts": ["1 container(s) unhealthy"]}, open(f"{T}/facts.json", "w"))
jobs.init()

# fake team: two model requests per run, through the real gate + policy hook
up = Fake([comp("part one"), comp("final report text")]); up.is_async = False
wrapped = CM._Wrapped(up, False, 2)
prompts = []
class FakeTeam:
    def __init__(self, sid): self.sid = sid
    def run(self, prompt):
        prompts.append(prompt)
        wrapped.chat.completions.create(model="m", messages=[{"role": "user", "content": prompt}])
        if hooks.get("between"): hooks["between"]()
        r = wrapped.chat.completions.create(model="m", messages=[{"role": "user", "content": "step 2"}])
        return types.SimpleNamespace(status="RunStatus.completed", content=r.choices[0].message.content)
hooks = {}
INF = {"ok": True, "reason": "loaded"}
audits = []
S = SCH.Scheduler(lambda sid: FakeTeam(sid), lambda: INF, lambda **k: audits.append(k))

# 1 outside window: seeds nothing, runs nothing
setclock(12); pc(); jobs.enqueue("custom", "manual", "do a thing", source="manual")
r = S.tick(); check("outside window -> blocked, job stays queued", r.startswith("blocked") and "outside" in r and jobs.counts().get("queued") == 1 and up.n == 0, r)
# 2 inside window, AI Mode OFF
setclock(4); pc(ai_mode="OFF"); r = S.tick(); check("AI Mode OFF -> blocked", r.startswith("blocked") and "AI Mode" in r, r)
check("seed made health_summary once (not vault_review? digest unseen -> yes)", jobs.exists_for_window("health_summary", "2026-11-04"))
n_before = sum(jobs.counts().values()); S.seed(); S.seed(); check("seeding is idempotent per window", sum(jobs.counts().values()) == n_before)
# 3 gaming / stale
pc(gaming=True); check("gaming -> blocked", "Gaming" in S.tick())
pc(poll_ts=time.time() - 600); check("stale PC state -> blocked", "stale" in S.tick())
# 4 all good -> highest priority first (health_summary prio 80) runs to completion
pc(); up.script[:] = [comp("part one"), comp("HEALTH REPORT")]; r = S.tick(); check("eligible -> job started", r.startswith("started"), r)
while S.running_job: time.sleep(0.05)
j = jobs.get(1) or {}; hs = [x for x in jobs.recent() if x["kind"] == "health_summary"][0]
check("job done + report written", hs["status"] == "done" and os.path.exists(hs["report_path"]) and "HEALTH REPORT" in open(hs["report_path"]).read(), hs["report_path"])
# 5 pause mid-job: AI Mode flips OFF after the first request completed
up.n = 0; up.script[:] = [comp("part one"), comp("never reached")]
pc(); hooks["between"] = lambda: pc(ai_mode="OFF")
r = S.tick(); check("second job started", r.startswith("started"), r)
while S.running_job: time.sleep(0.05)
nxt = [x for x in jobs.recent() if x["status"] == "queued"]
paused = [x for x in nxt if x["note"] and x["note"].startswith("paused")]
check("in-flight request finished, 2nd request refused, job re-queued with note", up.n == 1 and len(paused) == 1 and "AI Mode" in paused[0]["note"], paused[0]["note"] if paused else nxt)
pid = paused[0]["id"]; sess = jobs.get(pid)["session_id"]
# 6 resume later with 'Continue' prompt and same session
hooks.clear(); pc(); up.n = 0; up.script[:] = [comp("resumed"), comp("FINAL AFTER RESUME")]; prompts.clear()
S.tick(); 
while S.running_job: time.sleep(0.05)
j = jobs.get(pid); check("resumed with Continue prompt, same session, attempts=2, done", prompts and prompts[0].startswith("Continue this task") and j["session_id"] == sess and j["attempts"] == 2 and j["status"] == "done", (j["status"], j["attempts"]))
# 7 window closes mid-job (clock moves to 09:00 between requests)
jid = jobs.enqueue("custom", "late", "long task", source="manual"); up.n = 0; up.script[:] = [comp("p1"), comp("p2")]
hooks["between"] = lambda: setclock(9, 0)
setclock(8, 58); pc(); S.runs_in_window.clear()
# make earlier auto jobs not interfere
for x in jobs.recent():
    if x["status"] == "queued" and x["id"] != jid: jobs.cancel(x["id"])
S.tick()
while S.running_job: time.sleep(0.05)
j = jobs.get(jid); check("window closing mid-job pauses it (no request after 09:00)", j["status"] == "queued" and "outside" in (j["note"] or "") and up.n == 1, (j["status"], j["note"], up.n))
# 8 restart recovery
jobs.mark(jid, "running"); n = jobs.init(); check("restart recovers 'running' jobs to queued", n >= 1 and jobs.get(jid)["status"] == "queued")
# 9 cancel; never a second job at once
check("cancel works", jobs.cancel(jid) and jobs.get(jid)["status"] == "cancelled")
# 10 vault_review digest rule: after a done review of the same findings, not re-seeded next window
res = vaultlint.lint(); jobs.kv_set("vault_review_digest", res["digest"]); CLOCK[0] = datetime(2026, 11, 5, 4, 0, tzinfo=L)
S.seed(); check("same lint digest -> no repeat vault_review next window", not jobs.exists_for_window("vault_review", "2026-11-05"))
print(f"\n{sum(ok)}/{len(ok)} passed"); raise SystemExit(0 if all(ok) else 1)
