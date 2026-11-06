import sys, time; sys.path.insert(0, "app")
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import window as W, policy as P
L = ZoneInfo("Europe/London"); U = timezone.utc
ok = []
def check(n, c, x=""): ok.append(bool(c)); print(("PASS " if c else "FAIL ") + n, x)
d = lambda *a: datetime(*a, tzinfo=L)
# boundaries (winter, GMT)
check("02:59:59 outside", not W.in_window(d(2026,11,4,2,59,59)))
check("03:00:00 inside",  W.in_window(d(2026,11,4,3,0,0)))
check("08:59:59 inside",  W.in_window(d(2026,11,4,8,59,59)))
check("09:00:00 outside", not W.in_window(d(2026,11,4,9,0,0)))
# summer: 03:00 BST = 02:00 UTC, so a fixed UTC+0 window would be wrong
check("BST 03:00 local == 02:00 UTC inside", W.in_window(datetime(2026,7,1,2,0,tzinfo=U)))
check("BST 02:30 UTC (=03:30 local) inside", W.in_window(datetime(2026,7,1,2,30,tzinfo=U)))
check("BST 08:30 UTC (=09:30 local) outside", not W.in_window(datetime(2026,7,1,8,30,tzinfo=U)))
check("BST 07:59 UTC (=08:59 local) inside", W.in_window(datetime(2026,7,1,7,59,tzinfo=U)))
check("GMT 03:00 UTC inside / 02:59 UTC outside (winter)", W.in_window(datetime(2026,12,1,3,0,tzinfo=U)) and not W.in_window(datetime(2026,12,1,2,59,tzinfo=U)))
# DST changeover days: 2026-03-29 (spring forward 01:00 UTC) and 2026-10-25 (fall back 01:00 UTC)
for day in [(2026,3,29),(2026,10,25),(2027,3,28),(2027,10,31)]:
    inside = [h for h in range(24) if W.in_window(d(*day,h,30))]
    check(f"DST day {day}: window hours 3..8", inside == [3,4,5,6,7,8], inside)
check("window_date is local date", W.window_date(datetime(2026,7,1,23,30,tzinfo=U)) == "2026-07-02")
n = W.next_window_start(d(2026,11,4,10,0)); check("next start after 09:00 is tomorrow 03:00", (n.year,n.month,n.day,n.hour)==(2026,11,5,3))
n = W.next_window_start(d(2026,11,4,1,0)); check("next start before 03:00 is today", (n.day,n.hour)==(4,3))
# policy
good = dict(ai_mode="ON", gaming=False, logged_in=True, lmstudio_up=True, ram_available_mb=1500, commit_pct=85, vram_free_mib=700, age_s=20)
inf = {"ok": True, "reason": "loaded"}; inwin = d(2026,11,4,4,0)
r, why = P.proactive_decision(inf, good, inwin); check("all conditions met -> ok", r and not why)
for k, v, frag in [("ai_mode","OFF","AI Mode"),("gaming",True,"Gaming"),("logged_in",False,"logged in"),("lmstudio_up",False,"LM Studio"),
                   ("ram_available_mb",300,"RAM"),("commit_pct",95,"committed"),("vram_free_mib",100,"VRAM"),("age_s",300,"stale")]:
    r, why = P.proactive_decision(inf, dict(good, **{k:v}), inwin); check(f"blocked by {k}={v}", (not r) and any(frag in w for w in why), why)
r, why = P.proactive_decision(inf, good, d(2026,11,4,12,0)); check("outside window blocked", not r and "outside" in why[0])
r, why = P.proactive_decision(inf, None, inwin); check("no PC state blocked (never assumes)", not r)
r, why = P.proactive_decision({"ok":False,"reason":"model not loaded (state=not-loaded)"}, good, inwin); check("model not loaded blocked", not r)
r, why = P.proactive_decision(inf, dict(good, reachable=False), inwin); check("unreachable blocked", not r)
print(f"\n{sum(ok)}/{len(ok)} passed"); raise SystemExit(0 if all(ok) else 1)
