"""Memory that works without a model call: the owner says "remember ..." and it is stored (council.db, table memories); every later run, in any chat, gets the saved
notes in its context. "forget ..." removes one, "what do you remember" lists them. Agno's automatic user-memory is NOT used: it costs an extra model request per run
and decides by itself what to keep. Here only what the owner asked to keep is kept, and can see and delete it (Machine > Memory)."""
import re
import threading
import time

import jobs

_L = threading.Lock()
MAX_NOTES, MAX_LEN, CTX_CHARS = 60, 400, 1500
REMEMBER = re.compile(r"^\s*(?:please\s+)?(?:remember|note|keep in mind|save)(?:\s+that|\s+this)?\s*[:,-]?\s+(.{3,})$", re.I | re.S)
FORGET = re.compile(r"^\s*(?:please\s+)?forget(?:\s+(?:that|about))?\s*[:,-]?\s+(.{2,})$", re.I | re.S)
LIST = re.compile(r"^\s*(?:what|which)\s+(?:do you|things do you|have you)\s+(?:remember|know|saved|kept)|^\s*(?:list|show)\s+(?:your\s+|my\s+)?(?:memor(?:y|ies)|notes)\b", re.I)


def init():
    with _L, jobs._con() as c:
        c.execute("CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, ts REAL NOT NULL)")


def items():
    with _L, jobs._con() as c:
        return [{"id": r[0], "text": r[1], "ts": r[2]} for r in c.execute("SELECT id,text,ts FROM memories ORDER BY id DESC LIMIT ?", (MAX_NOTES,))]


def add(text):
    text = " ".join(text.split())[:MAX_LEN]
    with _L, jobs._con() as c:
        if c.execute("SELECT 1 FROM memories WHERE lower(text)=lower(?)", (text,)).fetchone(): return False
        c.execute("INSERT INTO memories(text,ts) VALUES(?,?)", (text, time.time()))
        c.execute("DELETE FROM memories WHERE id NOT IN (SELECT id FROM memories ORDER BY id DESC LIMIT ?)", (MAX_NOTES,))
    return True


def delete(mid=None, match=None):
    with _L, jobs._con() as c:
        if mid is not None: return c.execute("DELETE FROM memories WHERE id=?", (int(mid),)).rowcount
        row = c.execute("SELECT id FROM memories WHERE lower(text) LIKE ? ORDER BY id DESC LIMIT 1", ("%" + match.lower().strip(" .") + "%",)).fetchone()
        return c.execute("DELETE FROM memories WHERE id=?", (row[0],)).rowcount if row else 0


def context():
    """Injected into every run. Notes are data the owner wrote, shown newest first, capped so they never crowd the 16K context."""
    out, n = [], 0
    for m in items():
        if n + len(m["text"]) > CTX_CHARS: break
        out.append("- " + m["text"]); n += len(m["text"])
    return ("THINGS THE OWNER ASKED YOU TO REMEMBER (use them when relevant; do not recite them):\n" + "\n".join(out)) if out else ""


def command(prompt):
    """-> reply text if the message is a memory command, else None. No model request."""
    if len(prompt) > 600: return None
    if LIST.search(prompt):
        its = items()
        return ("Here is what I have saved:\n\n" + "\n".join(f"- {m['text']}" for m in its) + "\n\nSay \"forget\" and a few words from one to remove it.") if its else "I have nothing saved yet. Say \"remember\" and what to keep."
    m = FORGET.match(prompt)
    if m: return ("Forgotten." if delete(match=m.group(1)) else "I could not find a saved note matching that. Say \"what do you remember\" to see them.")
    m = REMEMBER.match(prompt)
    if m: return ("Saved. I will keep that in mind in every chat: " + " ".join(m.group(1).split())[:MAX_LEN]) if add(m.group(1)) else "I already had that saved."
    return None
