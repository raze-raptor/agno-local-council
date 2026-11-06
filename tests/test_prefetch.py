import os, sys
E = os.path.abspath("prompt-eval/state"); os.makedirs(E + "/audit", exist_ok=True)
os.environ.update(AGNO_STATE_DIR=E, AGNO_JOBS_DB=f"{E}/c.db", AGNO_FACTS="/nonexistent", AGNO_VAULT_MIRROR="/path/to/your/notes", AGNO_SCHEDULER="0")
sys.path.insert(0, "app")
import council as C
ok = []
def check(n, c): ok.append(bool(c)); print(("PASS " if c else "FAIL ") + n)
calls = []
def te(name, args, fn):
    calls.append(name); return "result"
C.PERM.guard_hook = lambda name, func, args, agent=None: "result"
def what(p): calls.clear(); C.prefetch(p, te); return list(calls)
check('prefetch reports what it fetched (research drives the direct path)', C.prefetch('Search the web for the newest nginx release', te)[1] == {'research'} and C.prefetch('How much disk is free on server1?', te)[1] == {'facts'})
check("research request fetches the web first", what("Search the web: what is the latest stable version of cloudflared?") == ["web_research"])
check("'look up' and 'release notes' count", what("Look up the release notes for Authentik 2026.8") == ["web_research"])
check("server question reads the health facts", what("How much disk space is free on server1?") == ["read_health_facts"])
check("both when it asks both", what("Research the newest Docker release and tell me which containers are running on server1") == ["web_research", "read_health_facts"])
check("explicit delegation is respected (no prefetch)", what("Ask the Engineer to write a palindrome function and have the Critic review it") == [])
check("a code request is never prefetched", what("Write a python function that reads the server load and searches for errors") == [])
check("small talk is never prefetched", what("hello, how are you?") == [] and what("what is a process vs a thread?") == [])
check("empty prompt", what("") == [])
print(f"{sum(ok)}/{len(ok)}"); sys.exit(0 if all(ok) else 1)
