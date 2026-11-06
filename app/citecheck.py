"""Deterministic accuracy checks on a research answer. No model involved.
Evidence = pages actually FETCHED in this run (search snippets are leads only). An answer is checked for:
 - citations [S#] that don't exist in this run's fetched sources
 - long quotes that don't appear in any fetched page text
 - how many independent domains the cited sources span, and their tiers (A official/primary, B reputable secondary, C unvetted)
 - web sources used but nothing cited"""
import re
import unicodedata

CITE = re.compile(r"\[S(\d+)\]")
QUOTE = re.compile(r'"([^"\n]{30,300})"|“([^”\n]{30,300})”')


def _norm(s):
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def check(answer, sources):
    """sources: {id(int): {"url","domain","tier","text"}}. -> dict, or None if no web source was fetched."""
    if not sources:
        return None
    ans = answer or ""
    cited = {int(m) for m in CITE.findall(ans)}
    invalid = sorted(cited - set(sources))
    valid = sorted(cited & set(sources))
    quotes = [a or b for a, b in QUOTE.findall(ans)]
    bodies = {i: _norm(s["text"]) for i, s in sources.items()}
    unverified = [q for q in quotes if not any(_norm(q) in b for b in bodies.values())]
    domains = sorted({sources[i]["domain"] for i in valid})
    tiers = {t: sum(1 for i in valid if sources[i]["tier"] == t) for t in "ABC"}
    warn = []
    if invalid:
        warn.append("INVALID CITATION " + ", ".join(f"[S{i}]" for i in invalid) + " - not fetched in this run; ignore those claims")
    if unverified:
        warn.append(f"{len(unverified)} quote(s) not found in any fetched page; treat as unverified")
    if not cited:
        warn.append("web pages were fetched but the answer cites none; do not rely on its web claims")
    elif len(domains) < 2:
        warn.append("only one independent domain cited; treat key claims as UNCONFIRMED")
    elif tiers["A"] == 0 and tiers["B"] == 0:
        warn.append("no official/primary or reputable-secondary source among the citations")
    return {"fetched": len(sources), "cited": valid, "invalid_citations": invalid, "quotes": len(quotes), "quotes_unverified": len(unverified),
            "domains": domains, "tiers_cited": tiers, "warnings": warn, "ok": not warn}


def footer(v):
    if v is None:
        return ""
    line = (f"Verification (automatic, not AI): {v['fetched']} page(s) fetched; cited {', '.join(f'[S{i}]' for i in v['cited']) or 'none'} "
            f"across {len(v['domains'])} domain(s) (tier A:{v['tiers_cited']['A']} B:{v['tiers_cited']['B']} C:{v['tiers_cited']['C']}); "
            f"quotes verified {v['quotes'] - v['quotes_unverified']}/{v['quotes']}.")
    return "\n\n---\n" + line + ("\nWARNINGS: " + " | ".join(v["warnings"]) if v["warnings"] else "\nNo automatic warnings. Still verify anything important.")
