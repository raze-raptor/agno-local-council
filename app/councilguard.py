"""Response guard for local reasoning models behind LM Studio (Mellum2.1).

Pure functions, no I/O, so the same code is unit-tested here and imported by the Agno model wrapper.
Failure modes seen in the Caesar CTF (raw/caesar-transcripts): the model ends a turn with
finish_reason=stop, empty `content`, no `tool_calls`, and its last tool call written as
<tool_call>{json}</tool_call> text inside `reasoning_content`.
"""
import ast
import json
import re

TC_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)

NUDGE_EMPTY = ("Your last reply was empty. Reply now with either one tool call using the tool interface, "
               "or your final answer as plain text. Do not put the tool call inside your reasoning.")
NUDGE_BAD_ARGS = "Your tool call could not be used: {err}. Call the tool again with valid arguments."


def raw_tool_calls(text):
    """Tool calls written as <tool_call>{json}</tool_call> text. Only well-formed JSON with a name is returned."""
    out = []
    for m in TC_RE.finditer(text or ""):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("name"), str):
            args = d.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    continue
            if isinstance(args, dict):
                out.append({"name": d["name"], "arguments": args})
    return out


def classify(msg, finish):
    """-> 'tool_calls' | 'text' | 'leaked_tool_call' | 'empty' | 'truncated'.
    msg is an OpenAI-style assistant message dict (content, reasoning_content, tool_calls)."""
    if msg.get("tool_calls"):
        return "tool_calls"
    if (msg.get("content") or "").strip():
        return "truncated" if finish == "length" else "text"
    if raw_tool_calls(msg.get("reasoning_content") or ""):
        return "leaked_tool_call"
    return "truncated" if finish == "length" else "empty"


def validate_call(name, args_json, schemas):
    """schemas: {tool_name: parameters-json-schema}. -> (args_dict | None, error | None)."""
    if name not in schemas:
        return None, f"unknown tool '{name}' (available: {', '.join(sorted(schemas))})"
    try:
        args = json.loads(args_json) if isinstance(args_json, str) else dict(args_json)
    except (ValueError, TypeError):
        return None, "arguments are not valid JSON"
    if not isinstance(args, dict):
        return None, "arguments must be a JSON object"
    sch = schemas[name] or {}
    missing = [k for k in sch.get("required", []) if k not in args]
    if missing:
        return None, "missing required argument(s): " + ", ".join(missing)
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
    for k, v in args.items():
        want = (sch.get("properties", {}).get(k) or {}).get("type")
        if want in types and not isinstance(v, types[want]):
            return None, f"argument '{k}' should be {want}"
        if want in ("integer", "number") and isinstance(v, bool):
            return None, f"argument '{k}' should be {want}"
    return args, None


def check_python(code):
    """Syntax-check model-written Python before it runs. -> None if fine, else a message the model can act on."""
    try:
        ast.parse(code)
    except SyntaxError as e:
        hint = ""
        first = (code.splitlines() or [""])[0]
        if ";" in first and re.search(r";\s*(def|for|while|if|class|with|try)\b", first):
            hint = " A compound statement (def/for/if/...) cannot follow ';' on one line; write the code on separate lines."
        return f"SyntaxError line {e.lineno}: {e.msg}.{hint}"
    return None
