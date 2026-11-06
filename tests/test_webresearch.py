import sys, time, threading; sys.path.insert(0, "app")
import webtools as WT
ok = []
def check(n, c, x=""): ok.append(bool(c)); print(("PASS " if c else "FAIL ") + n, x)
active, peak, calls = [0], [0], []
L = threading.Lock()
def fake(path, timeout=40):
    calls.append(path)
    if path.startswith("/search"):
        return 200, {"results": [{"tier": t, "title": f"T{d}", "domain": d, "url": f"https://{d}/p", "snippet": "s", "fetchable": True} for t, d in (("A", "a.example.org"), ("B", "b.example.org"), ("C", "c.example.org"), ("A", "d.example.org"))]}
    with L: active[0] += 1; peak[0] = max(peak[0], active[0])
    time.sleep(0.3)
    with L: active[0] -= 1
    dom = path.split("%2F%2F")[1].split("%2F")[0]
    body = "Intro line that is long enough to count as a paragraph here.\n" + "Pricing for subscription plans is usually five pounds per month.\n" + "Unrelated filler paragraph about something else entirely, long enough.\n" * 3
    return 200, {"final_url": f"https://{dom}/p", "domain": dom, "tier": "A", "title": "Doc " + dom, "chars_total": len(body), "offset": 0, "sha256": "a" * 64, "fetched_at": "t", "cleaning": {"injection_lines_removed": 0, "hidden_dropped": 0},
                 "download_truncated": False, "text": body, "more": False, "links": [], "thin": False, "full_text_for_verification": body}
WT._get = fake
ctx = WT.begin_run()
t0 = time.time(); r = WT._web_research_impl("subscription pricing plans", 3); dt = time.time() - t0
check("one call returns three cited sources", r.count("<<<BEGIN>>>") == 3 and "[S1]" in r and "[S3]" in r, r[:100])
check("pages were downloaded in parallel (3 x 0.3 s took well under 0.9 s)", peak[0] >= 2 and dt < 0.8, f"peak={peak[0]} {dt:.2f}s")
check("tier C source is not picked while A/B exist", "c.example.org" not in r)
check("excerpt keeps the relevant paragraph and drops filler", "five pounds per month" in r and r.count("Unrelated filler") <= 1)
check("one search and three fetches were charged to the budget", ctx["searches"] == 1 and ctx["fetches"] == 3)
check("sources are registered for the citation checker", len(ctx["sources"]) == 3 and ctx["sources"][1]["text"].startswith("Intro"))
check("output is small (under 5000 chars)", len(r) < 5000, str(len(r)))
r2 = WT._web_research_impl("other angle", 3)
check("a second call respects the fetch budget", ctx["fetches"] <= WT.MAX_FETCHES)
print(f"{sum(ok)}/{len(ok)}"); sys.exit(0 if all(ok) else 1)
