"""Read-only tools for the council. They read pre-collected data (facts JSON, markdown mirror of the vault); none can run a
command, write a file, or reach the network. Output sizes are capped so a tool result can never swamp the 16K context."""
import json
import os
import re

from agno.tools import tool

import knowledge
from toollog import tl

MIRROR = os.environ.get("AGNO_VAULT_MIRROR", "/var/lib/agno-vault")
FACTS = os.environ.get("AGNO_FACTS", "/var/lib/agno-facts/health.json")
SKIP = {"Templates", ".stversions", ".obsidian"}
_WORD = re.compile(r"[a-z0-9][a-z0-9+._-]{1,}")


def _notes():
    for dp, dn, fn in os.walk(MIRROR):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fn:
            if f.endswith(".md"):
                yield os.path.join(dp, f)


def _read_health_facts_impl() -> str:
    try:
        d = json.load(open(FACTS))
    except (OSError, ValueError):
        return "health facts are not available right now"
    age_min = round((__import__("time").time() - d.get("ts", 0)) / 60)
    d["facts_age_minutes"] = age_min
    return json.dumps(d, indent=0)[:3500]


@tool
def read_health_facts() -> str:
    """Latest deterministic health facts about the server1 server (failed services, containers, disk, memory, backup status, alerts), collected by a script every 10 minutes."""
    return tl("read_health_facts", _read_health_facts_impl())



def _vault_search_impl(query: str, max_results: int = 5) -> str:
    try:
        hits = knowledge.search(query, min(max_results, 6))
    except Exception:
        hits = None                                     # index problem: fall back to the plain scan below
    if hits is not None:
        out = [f"{n}  ({rel})\n   ...{snip}..." for n, rel, snip, _ in hits]
        return ("\n".join(out) if out else "no matching notes")[:2800]
    return _vault_scan(query, max_results)


def _vault_scan(query: str, max_results: int = 5) -> str:
    words = [w for w in _WORD.findall(query.lower()) if len(w) > 2][:8]
    if not words:
        return "give a more specific query"
    scored = []
    for p in _notes():
        name = os.path.basename(p)[:-3]
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        low = txt.lower(); lname = name.lower()
        score = sum(10 for w in words if w in lname) + sum(min(low.count(w), 5) for w in words)
        if score:
            first = next((w for w in words if w in low), words[0])
            i = max(low.find(first) - 60, 0)
            scored.append((score, name, os.path.relpath(p, MIRROR), re.sub(r"\s+", " ", txt[i:i + 220])))
    scored.sort(reverse=True)
    out = [f"{n}  ({rel})\n   ...{snip}..." for _, n, rel, snip in scored[:min(max_results, 6)]]
    return ("\n".join(out) if out else "no matching notes")[:2800]


@tool
def vault_search(query: str, max_results: int = 5) -> str:
    """Search the owner's Obsidian infrastructure notes (titles and text) and return the best matching note names with a short snippet. Use vault_read to open one."""
    return tl("vault_search", _vault_search_impl(query, max_results), query=query, max_results=max_results)



def _vault_read_impl(note: str, max_chars: int = 3500) -> str:
    want = note.strip().removesuffix(".md").lower()
    for p in _notes():
        if os.path.basename(p)[:-3].lower() == want:
            try:
                return open(p, encoding="utf-8", errors="replace").read()[:min(max_chars, 4500)]
            except OSError:
                return "could not read that note"
    return "no note with that exact name; use vault_search first"



@tool
def vault_read(note: str, max_chars: int = 3500) -> str:
    """Read one Obsidian note by its name (e.g. 'Port Map'). Returns the start of the note (frontmatter first), limited in size."""
    return tl("vault_read", _vault_read_impl(note, max_chars), note=note, max_chars=max_chars)
