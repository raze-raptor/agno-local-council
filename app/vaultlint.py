"""Deterministic Obsidian-vault lint on the read-only markdown mirror. No model involved; the model only interprets the result.
Checks: missing frontmatter, missing/old last_verified (older than 30 days), wikilinks whose target note does not exist."""
import hashlib
import json
import os
import re
from datetime import date

MIRROR = os.environ.get("AGNO_VAULT_MIRROR", "/var/lib/agno-vault")
SKIP_DIRS = {"Templates", ".stversions", ".obsidian", "raw"}      # raw = archived evidence (reports, logs, code), not notes
LINK = re.compile(r"\[\[([^\]|#]+)")
STALE_DAYS = 30


def lint(root=MIRROR, today=None):
    today = today or date.today()
    notes = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".md"):
                notes[f[:-3]] = os.path.join(dp, f)
    missing_fm, stale, broken = [], [], []
    for name, path in sorted(notes.items()):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(path, root)
        if not text.startswith("---\n"):
            missing_fm.append(rel)
        else:
            fm = text.split("\n---", 1)[0]
            m = re.search(r"^last_verified:\s*(\d{4}-\d{2}-\d{2})", fm, re.M)
            if m:
                age = (today - date.fromisoformat(m.group(1))).days
                if age > STALE_DAYS:
                    stale.append((age, rel))
            elif "type:" in fm and "type: meta" not in fm:
                stale.append((9999, rel + " (no last_verified)"))
        for target in set(LINK.findall(text)):
            t = target.strip()
            if t and t not in notes and "/" not in t and not t.lower().endswith((".png", ".jpg", ".pdf", ".canvas", ".csv", ".json", ".txt", ".py", ".ps1", ".sh")):
                broken.append((rel, t))
    stale.sort(reverse=True)
    res = {"notes": len(notes), "missing_frontmatter": missing_fm[:15], "missing_frontmatter_total": len(missing_fm),
           "stale_over_30d_total": len(stale), "stalest": [f"{a}d {r}" for a, r in stale[:12]],
           "broken_links_total": len(broken), "broken_links": [f"{r} -> [[{t}]]" for r, t in broken[:15]]}
    res["digest"] = hashlib.sha1(json.dumps([res["missing_frontmatter_total"], res["stalest"], res["broken_links"]], sort_keys=True).encode()).hexdigest()[:12]
    res["issue_count"] = res["missing_frontmatter_total"] + res["stale_over_30d_total"] + res["broken_links_total"]
    return res


if __name__ == "__main__":
    print(json.dumps(lint(), indent=1))
