import json, os, sys, tempfile, time
T = tempfile.mkdtemp(prefix="protest_")
os.environ.update(AGNO_JOBS_DB=f"{T}/c.db", AGNO_PC_STATE=f"{T}/pc.json", AGNO_FACTS=f"{T}/facts.json", AGNO_VAULT_MIRROR=f"{T}/vault", AGNO_STATE_DIR=f"{T}/state",
                  AGNO_DB=f"{T}/agno.db", AGNO_PCSTATE_DIR=f"{T}/pcs")
sys.path.insert(0, "app")
from datetime import datetime, date
from zoneinfo import ZoneInfo
import jobs, known, policy, proactive as P, window as W, dashapi as D
L = ZoneInfo("Europe/London")
NOW = datetime(2026, 11, 5, 4, 0, tzinfo=L)
ok = []
def check(n, c, x=""): ok.append(bool(c)); print(("PASS " if c else "FAIL ") + n, str(x)[:150])
jobs.init()

# ---- severity: old exited container is NOT high (the 2026-11-05 SysAdmin finding)
check("old exited known container -> info", known.classify_container("core-runner-app", "Exited (137) 3 weeks ago")[0] == "info")
check("old exited unknown container -> low", known.classify_container("mystery", "Exited (0) 2 months ago")[0] == "low")
check("container stopped 3 hours ago -> medium", known.classify_container("web", "Exited (1) 3 hours ago")[0] == "medium")
check("critical container stopped 2 minutes ago -> high", known.classify_container("cloudflared", "Exited (1) 2 minutes ago")[0] == "high")
check("unhealthy now -> medium", known.classify_container("web", "Up 2 hours (unhealthy)")[0] == "medium")
check("known harmless unit -> info, new unit -> medium, critical unit -> high", [known.classify_unit(u)[0] for u in ("pmie_daily.service", "foo.service", "cloudflared.service")] == ["info", "medium", "high"])
a = known.assess_facts({"failed_units_known_harmless": ["pmie_daily.service"], "failed_units": [], "containers_with_problems": ["core-runner-app: Exited (137) 3 weeks ago"], "disk_root": {"used_pct": 42}, "mem": {"available_gb": 21}, "backup": {"last_run_ok": True, "log_age_h": 5}})
check("the real 2026-11-05 facts assess as calm with 0 findings", a["overall"] == "calm" and not a["findings"] and len(a["known"]) == 2, a["overall"])
check("disk 93% -> urgent", known.assess_facts({"disk_root": {"used_pct": 93}})["overall"] == "urgent")
check("failed backup -> urgent", known.assess_facts({"backup": {"last_run_ok": False}})["overall"] == "urgent")
check("age parser: 'an hour ago', 'about a minute ago'", known.container_age_hours("Exited (1) an hour ago") == 1 and known.container_age_hours("Exited (1) About a minute ago") is not None)

# ---- parsers (real broker output shapes)
check("failed units parsed, header ignored", P.parse_units("  UNIT                          LOAD   ACTIVE SUB    DESCRIPTION\n● fwupd-refresh.service loaded failed failed Refresh fwupd\n\n1 loaded units listed.") == ["fwupd-refresh.service"])
dps = "CHECK server2/docker_ps [OK]\nNAMES\tSTATUS\tIMAGE\nopen-webui\tUp 3 days (healthy)\timg\nwg-easy\tExited (1) 3 months ago\timg2\nwg-easy-fallback\tCreated\timg2"
check("docker_ps: only non-running rows", P.parse_docker_ps(dps) == [("wg-easy", "Exited (1) 3 months ago"), ("wg-easy-fallback", "Created")], P.parse_docker_ps(dps))

# ---- config validation
check("defaults: all kinds enabled, no topics, cap 6", P.get_config()["kinds"]["docs_gaps"] and P.get_config()["topics"] == [] and P.get_config()["max_per_night"] == 6)
check("bad kind rejected", P.set_config({"kinds": {"rm_rf": True}})[0] is False)
check("non-bool kind rejected", P.set_config({"kinds": {"docs_gaps": "yes"}})[0] is False)
check("short/long topic rejected", not P.set_config({"topics": ["abc"]})[0] and not P.set_config({"topics": ["x" * 200]})[0])
check("too many topics rejected", not P.set_config({"topics": [f"topic number {i}" for i in range(9)]})[0])
check("cap bounds + bool rejected", not P.set_config({"max_per_night": 0})[0] and not P.set_config({"max_per_night": True})[0] and P.set_config({"max_per_night": 3})[0])
okk, cfg = P.set_config({"topics": ["OpenSSH security advisories", "openssh security advisories", "Ubuntu 24.04 kernel updates\n"]})
check("topics cleaned, de-duplicated (case-insensitive), control chars removed", okk and cfg["topics"] == ["OpenSSH security advisories", "Ubuntu 24.04 kernel updates"], cfg["topics"])
P.set_config({"max_per_night": 6})

# ---- seeding
json.dump({"ts": time.time(), "failed_units_known_harmless": ["pmie_daily.service"], "failed_units": [], "containers_with_problems": ["core-runner-app: Exited (137) 3 weeks ago"], "disk_root": {"used_pct": 42}, "mem": {"available_gb": 21}, "backup": {"last_run_ok": True, "log_age_h": 5}}, open(f"{T}/facts.json", "w"))
os.makedirs(f"{T}/vault/02 - Services"); os.makedirs(f"{T}/vault/07 - Projects")
open(f"{T}/vault/02 - Services/Old.md", "w").write("---\ntype: service\nlast_verified: 2026-01-01\n---\n# Old\n")
open(f"{T}/vault/02 - Services/Fresh.md", "w").write("---\ntype: service\nlast_verified: 2026-11-04\n---\n# Fresh\n")
open(f"{T}/vault/07 - Projects/Agno - Outstanding Decisions.md", "w").write("| # | Q |\n|---|---|\n| 1 | **RESOLVED 2026-11-05:** done |\n| 2 | Do you want TLS for [[Council Web UI]]? | no | x |\n")
bast = {"failed_units": "UNIT LOAD ACTIVE SUB\n● fwupd-refresh.service loaded failed failed x", "docker_ps": "NAMES\tSTATUS\tIMAGE\nwg-easy\tExited (1) 3 months ago\ti", "disk": "Filesystem 71%", "memory": "Mem: 7.5Gi"}
state = {"cit_units": "● foo.service loaded failed failed x"}
def ask(host, check, arg=""):
    if host == "server1" and check == "failed_units": return {"rc": 0, "output": state["cit_units"]}
    if host == "server1" and check == "docker_ps": return {"rc": 0, "output": "NAMES\tSTATUS\tIMAGE\ncore-runner-app\tExited (137) 3 weeks ago\ti"}
    return {"rc": 0, "output": bast.get(check, "")}
made = P.seed(NOW, ask=ask, now_ts=NOW.timestamp())
kinds = sorted(k for k, _ in made)
check("first night: health, docs_gaps, project_status, research (a topic is configured), vault_review queued; service_watch only baselines", kinds == ["docs_gaps", "health_summary", "project_status", "research_digest", "vault_review"], kinds)
check("seeding the same window again creates nothing (once per window)", P.seed(NOW, ask=ask, now_ts=NOW.timestamp()) == [])
hj = [j for j in jobs.recent(20) if j["kind"] == "health_summary"][0]
check("health prompt carries the script's severity verdict (CALM) and the never-high rule", "CALM" in jobs.get(hj["id"])["prompt"] and "never high" in jobs.get(hj["id"])["prompt"])
dj = jobs.get([j for j in jobs.recent(20) if j["kind"] == "project_status"][0]["id"])
check("project status lists only the unresolved item", "TLS" in dj["prompt"] and "RESOLVED" not in dj["prompt"].split("OPEN ITEMS")[1])
gj = jobs.get([j for j in jobs.recent(20) if j["kind"] == "docs_gaps"][0]["id"])
check("docs gaps lists the stale note and not the fresh one", "Old" in gj["prompt"] and "Fresh" not in gj["prompt"])
check("service_watch baseline stored, no job on the first night", jobs.kv_get("service_snapshot") and not [j for j in jobs.recent(30) if j["kind"] == "service_watch"])

# night 2: a NEW failed unit on server1 -> service_watch job with the diff
NOW2 = datetime(2026, 11, 6, 4, 0, tzinfo=L)
state["cit_units"] = "● foo.service loaded failed failed x\n● bar.service loaded failed failed y"
made2 = P.seed(NOW2, ask=ask, now_ts=NOW2.timestamp())
sw = [j for j in jobs.recent(40) if j["kind"] == "service_watch"]
check("night 2: service_watch queued because bar.service is NEW", len(sw) == 1 and "bar.service" in jobs.get(sw[0]["id"])["prompt"] and "NEW problem" in jobs.get(sw[0]["id"])["prompt"])
check("night 2: unfinished docs_gaps/project_status/research are carried over, not duplicated", not {"docs_gaps", "project_status", "research_digest", "vault_review"} & {k for k, _ in made2}, made2)
check("night 2: yesterday's unfinished health briefing was superseded by tonight's", any(j["kind"] == "health_summary" and j["status"] == "cancelled" and "superseded" in (j["note"] or "") for j in jobs.recent(60)))
# unchanged night 3 -> nothing
NOW3 = datetime(2026, 11, 7, 4, 0, tzinfo=L)
check("night 3 with no change: no service_watch", "service_watch" not in [k for k, _ in P.seed(NOW3, ask=ask, now_ts=NOW3.timestamp())])
# a host that cannot be read must not poison the baseline
bad = lambda host, check, arg="": {"rc": 503, "output": "down"}
NOW4 = datetime(2026, 11, 8, 4, 0, tzinfo=L); snap_before = jobs.kv_get("service_snapshot")
P.seed(NOW4, ask=bad, now_ts=NOW4.timestamp())
check("unreadable hosts leave the stored snapshot untouched", jobs.kv_get("service_snapshot") == snap_before)

# ---- per-kind disable + research topics + morning gating
for j in jobs.recent(100):                                     # the first nights' work finished; topic 1 was researched on night 1
    if j["status"] == "queued":
        jobs.mark(j["id"], "done", finished_at=time.time())
        if j["kind"] == "research_digest": P.on_done(jobs.get(j["id"]), NOW.timestamp())
P.set_config({"kinds": {"health_summary": False}})
NOW5 = datetime(2026, 11, 9, 4, 0, tzinfo=L); m5 = P.seed(NOW5, ask=ask, now_ts=NOW5.timestamp())
check("disabled kind is not seeded", "health_summary" not in [k for k, _ in m5])
check("research_digest seeds the next topic not researched in 6 days (topic 2)", "research_digest" in [k for k, _ in m5], m5)
rj = [j for j in jobs.recent(60) if j["kind"] == "research_digest"][0]
check("research prompt names topic 2, asks for sources and uses the Researcher", "Ubuntu" in jobs.get(rj["id"])["prompt"] and "Researcher" in jobs.get(rj["id"])["prompt"] and "[S#]" in jobs.get(rj["id"])["prompt"])
P.on_done(jobs.get(rj["id"]), NOW5.timestamp())
NOW6 = datetime(2026, 11, 10, 4, 0, tzinfo=L); m6 = P.seed(NOW6, ask=ask, now_ts=NOW6.timestamp())
rj6 = [j for j in jobs.recent(60) if j["kind"] == "research_digest" and j["window_date"] == "2026-11-10"]
check("both topics researched within 6 days -> no research job tonight (cap on web work)", len(rj6) == 0)
jobs.kv_set("seeded:2026-11-11:x", "1")
# morning briefing only after other work is done and built at run time
W11 = datetime(2026, 11, 11, 7, 0, tzinfo=L)
jid = jobs.enqueue("health_summary", "h", "p", window_date="2026-11-11")
check("morning briefing waits while other work is queued", P.collect_morning("2026-11-11", W11.timestamp()) is None)
jobs.mark(jid, "done", finished_at=time.time())
r = P.collect_morning("2026-11-11", W11.timestamp())
check("morning briefing queued after work is done, lowest priority", r and r[2] == 10, r and r[0])
check("morning briefing does not run at 03:00-06:59 even when other work is done", P.collect_morning("2026-11-11", datetime(2026, 11, 11, 3, 30, tzinfo=L).timestamp()) is None)
mb = P.build_morning("2026-11-11", W11.timestamp(), ask=ask)
check("morning prompt built at run time lists tonight's jobs and server2 facts", "health_summary" in mb and "server2_disk" in mb and "71%" in mb)
check("refresh_prompt rebuilds only the morning kind", P.refresh_prompt({"kind": "custom", "prompt": "hello"}) == "hello")

# ---- structured policy agrees with the decision function
pcs = dict(ai_mode="ON", gaming=False, logged_in=True, lmstudio_up=True, ram_available_mb=1500, commit_pct=85, vram_free_mib=700, poll_ts=time.time(), age_s=3, reachable=True)
for label, pc_, inf, now in [("all good", pcs, {"ok": True, "reason": "loaded"}, NOW), ("gaming", {**pcs, "gaming": True, "game": "x"}, {"ok": True, "reason": "loaded"}, NOW), ("ai off", {**pcs, "ai_mode": "OFF"}, {"ok": True, "reason": "x"}, NOW),
                              ("stale", {**pcs, "age_s": 500}, {"ok": True, "reason": "x"}, NOW), ("no pc", None, {"ok": True, "reason": "x"}, NOW), ("model down", pcs, {"ok": False, "reason": "unloaded"}, NOW),
                              ("outside", pcs, {"ok": True, "reason": "x"}, datetime(2026, 11, 5, 12, 0, tzinfo=L)), ("low ram", {**pcs, "ram_available_mb": 100}, {"ok": True, "reason": "x"}, NOW)]:
    okd, why = policy.proactive_decision(inf, pc_, now); rows = policy.evaluate(inf, pc_, now)
    check(f"evaluate() agrees with proactive_decision: {label}", (all(r["ok"] for r in rows) == okd) and (sum(1 for r in rows if not r["ok"]) >= (0 if okd else 1)), [r["id"] for r in rows if not r["ok"]])

# ---- redaction by field name
r = D.redact({"tool": "run_check", "password": "hunter2hunter2", "args": {"api_key": "abc", "host": "server1"}, "head": "x" * 10, "blob": "A" * 40})
check("redact by field name and long opaque strings", r["password"] == "[redacted]" and r["args"]["api_key"] == "[redacted]" and r["args"]["host"] == "server1" and r["blob"] == "[redacted-token]" and r["head"] == "x" * 10, r)

# ---- dashboard read models on real structures
t = D.tasks(None)
check("tasks(): jobs, counts, config, kinds, known state", t["jobs"] and "queued" in t["counts"] and t["config"]["max_per_night"] == 6 and set(t["kinds"]) == set(P.KINDS) and "core-runner-app" in t["known_state"]["containers"])
pid = jobs.add_proposal("server1", "restart x", "docker restart x", "because")
check("approvals(): pending proposal listed, decision recorded only", D.approvals()["counts"]["pending"] == 1 and jobs.decide_proposal(pid, "approved") and D.approvals()["counts"]["approved"] == 1)
sch = D.schedule(None, {"ok": True, "reason": "loaded"})
check("schedule(): window, checks, nights history, caps", sch["window"]["start_time"] == "03:00" and len(sch["checks"]) == 11 and sch["nights"] and sch["caps"]["max_per_night"] == 6)
check("aimode_set refuses without confirmation and for bad commands", D.aimode_set("on", None)[0] == 400 and D.aimode_set("rm", True)[0] == 400)
import types as _t
D.CTL = f"{T}/pcs/control"; os.makedirs(D.CTL, exist_ok=True)
import threading as _th
def _answer():
    for _ in range(40):                                   # stand-in for the poller: read the request, reply with the same id
        time.sleep(0.2)
        rq = os.path.join(D.CTL, "request.json")
        if os.path.exists(rq):
            r = json.load(open(rq)); mode = oct(os.stat(rq).st_mode & 0o777)
            json.dump({"id": r["id"], "cmd": r["cmd"], "ok": True, "msg": "sent", "mode": mode}, open(os.path.join(D.CTL, "result.json"), "w")); os.remove(rq); return
_th.Thread(target=_answer, daemon=True).start()
json.dump({**pcs, "gaming": True, "game": "Minecraft"}, open(f"{T}/pc.json", "w"))
c1 = D.aimode_set("on", True)
check("aimode ON from the website is accepted even while gaming (same authority as the tray)", c1[0] == 200 and c1[1]["ok"], c1)
check("the request file is group-readable so the poller (another user) can read it (the bug that made the button do nothing)", c1[1].get("mode") == "0o660", c1[1].get("mode"))
m = D.memory()
print(f"{sum(ok)}/{len(ok)} passed"); sys.exit(0 if all(ok) else 1)
