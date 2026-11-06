"""Best-effort audit of every tool call (name + short argument summary + outcome size). A logging problem never affects a run."""
import json
import os
import time

PATH = os.path.join(os.environ.get("AGNO_STATE_DIR", "/var/lib/agno"), "audit", "tool-calls.jsonl")


def tl(name, result=None, **kw):
    try:
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": name, **{k: str(v)[:120] for k, v in kw.items()}}
        if result is not None:
            row["result_chars"] = len(result); row["result_head"] = result[:80].replace("\n", " ")
        with open(PATH, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass
    return result
