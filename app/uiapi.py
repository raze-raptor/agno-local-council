"""Browser-UI API on the council's private (loopback) listener: AG-UI event stream + conversations + roster.
Reuses the existing council pieces injected by council.py (team factory, inference check, run queue, audit, the one-request model gate
inside the model object). Nothing here talks to the model directly; it only runs the same Agno Team the rest of the council uses.
The browser never reaches this listener: only the authenticating gateway does."""
import json
import queue
import re
import threading
import time
import uuid

from ag_ui.core import EventType, RunAgentInput, RunErrorEvent, RunFinishedEvent, RunStartedEvent, TextMessageContentEvent, TextMessageEndEvent, TextMessageStartEvent, ToolCallArgsEvent, ToolCallEndEvent, ToolCallResultEvent, ToolCallStartEvent
from ag_ui.encoder import EventEncoder

import aguimap
import citecheck as CC
import refusal
import permissions as PERM
import convo
import webtools as WT
import opstools as OT

DISPLAY = {"Engineer": "Software Engineer", "Critic": "Critic (code review)", "SysAdmin": "Systems Administrator", "Researcher": "Research Analyst", "Strategist": "Business Strategist", "RogueCouncil": "Rogue Orchestrator", "RogueEngineer": "Rogue Engineer", "RogueCritic": "Rogue Critic", "RogueSysAdmin": "Rogue SysAdmin", "RogueResearcher": "Rogue Researcher", "RogueStrategist": "Rogue Strategist"}
ENC = EventEncoder()
RUNS = {}                      # run_id -> {"conv","started","cancel_requested"}
_RL = threading.Lock()
C = {}                         # injected: build_team, inference_state, queue, audit, Team, roster


def setup(**kw):
    C.update(kw); convo.init()


def _sse(ev):
    return ENC.encode(ev)


def roster():
    """real data: the members of the live Team (name, role, tool names), plus what is deliberately not built"""
    team = C["build_team"]("roster")
    out = [{"id": "orchestrator", "name": "Orchestrator", "role": "Main assistant: answers directly, plans and delegates, one specialist at a time", "tools": [getattr(t, "name", str(t)) for t in (team.tools or [])], "state": "available"}]
    for m in team.members:
        out.append({"id": m.name.lower(), "name": DISPLAY.get(m.name, m.name), "agent_name": m.name, "role": m.role, "tools": [getattr(t, "name", getattr(t, "__name__", str(t))) for t in _flat(m.tools)], "state": "available"})
    rs = C["rogue_state"]() if C.get("rogue_state") else {}
    for rid, nm, role in [("rogue_orch", "RogueCouncil", "Leads Rogue's council"), ("rogue_eng", "RogueEngineer", "Code, unfiltered"), ("rogue_crit", "RogueCritic", "Review, unfiltered"),
                          ("rogue_sys", "RogueSysAdmin", "Server facts and proposals, read-only"), ("rogue_res", "RogueResearcher", "Web and vault research, unfiltered"), ("rogue_str", "RogueStrategist", "Decisions, unfiltered")]:
        out.append({"id": rid, "name": DISPLAY.get(nm, nm), "agent_name": nm, "role": role + ". Launches only after the main council declined a request; own model; read tools only, nothing executes.", "tools": [],
                    "state": "standby (model not loaded)" if not rs.get("ok") else "model loaded"})
    out.append({"id": "screen_operator", "name": "Screen Operator", "role": "Controls your Brave browser through a restricted bridge", "tools": [], "state": "not built yet"})
    out.append({"id": "proactive_ops", "name": "Proactive Operations", "role": "Scheduled checks 03:00-09:00; runs as scripts plus the job queue, not a chat agent", "tools": [], "state": "scheduler running"})
    return out


def _flat(tools):
    out = []
    for t in tools or []:
        fns = getattr(t, "functions", None)
        out.extend(list(fns.values()) if isinstance(fns, dict) else [t])
    return out


def _run_in_thread(conv, run_id, prompt, q, mapper, wctx_holder):
    """producer: runs the Team, maps Agno events to AG-UI, pushes them to q, and persists the run whatever the browser does"""
    events, final_text, status = [], "", "failed"
    parts = []
    cond = wctx_holder["cond"]; wctx_holder["events"] = events; kind = wctx_holder.get("agent")          # a reloaded page re-attaches to this list (see attach)

    def emit(evs):
        for e in evs:
            if e.timestamp is None:
                e.timestamp = int(time.time() * 1000)          # AG-UI timestamp (ms): real server time, so replayed runs keep true durations
            events.append(e.model_dump(by_alias=True, exclude_none=True, mode="json"))
            q.put(e)
        with cond:
            cond.notify_all()

    try:
        wctx = WT.begin_run(); OT.begin_run(); wctx_holder["w"] = wctx
        emit(mapper.start())
        if kind in ("rogue_team", "rogue_direct"):
            if kind == "rogue_team":
                emit([aguimap.custom("agent", "Rogue Orchestrator"), aguimap.custom("handoff_from", wctx_holder.get("src"))])
            else:
                emit([aguimap.custom("agent", "Rogue Orchestrator")])
            if not C["rogue_state"]()["ok"]:
                emit([aguimap.custom("note", "Switching to Rogue's model (about a minute): the council goes offline until this finishes.")])
                code, res = C["swap_model"]("rogue")
                if code != 200:
                    emit(mapper.close("HANDOFF_FAILED", (res or {}).get("msg") or (res or {}).get("error") or "The model could not be switched.")); return
                for _ in range(45):
                    time.sleep(4)
                    if C["rogue_state"]()["ok"]: break
                else:
                    emit(mapper.close("HANDOFF_FAILED", "Rogue's model did not load in time; check the PC.")); return
                wctx_holder["swapped"] = True
            emit([aguimap.custom("note", "")])
            team = C["build_rogue_team"](conv)
        else:
            if C["rogue_state"]()["ok"]:                       # the toggle was switched back to Council while Rogue's model was still loaded: swap back first
                emit([aguimap.custom("note", "Switching back to the council's model (about a minute): it comes back online shortly.")])
                code, res = C["swap_model"]("main")
                if code != 200:
                    emit(mapper.close("HANDOFF_FAILED", (res or {}).get("msg") or (res or {}).get("error") or "The model could not be switched.")); return
                for _ in range(45):
                    time.sleep(4)
                    if not C["rogue_state"]()["ok"]: break
                else:
                    emit(mapper.close("HANDOFF_FAILED", "The council's model did not reload in time; check the PC.")); return
                emit([aguimap.custom("note", "")])
            def tool_event(name, args, fn):
                tid = "pre" + uuid.uuid4().hex[:10]
                emit([ToolCallStartEvent(type=EventType.TOOL_CALL_START, tool_call_id=tid, tool_call_name=name), ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS, tool_call_id=tid, delta=json.dumps(args)),
                      ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tid)])
                res = fn()
                emit([ToolCallResultEvent(type=EventType.TOOL_CALL_RESULT, message_id="m" + tid, tool_call_id=tid, content=str(res)[:4000], role="tool")])
                return res
            reply = C["memory"].command(prompt) if C.get("memory") else None
            if reply:
                final_text = _say(emit, mapper, conv, run_id, reply); parts.append(final_text); return
            if C.get("is_browser_task") and C["is_browser_task"](prompt):
                final_text = _operator_run(conv, run_id, prompt, emit, parts, wctx, mapper)
                return
            try:
                ctx, kinds = C["prefetch"](prompt, tool_event, convo.prior_user(conv)) if C.get("prefetch") else ("", set())
            except Exception as ex:
                C["audit"](event="prefetch_failed", run=run_id, error=type(ex).__name__); ctx, kinds = "", set()
            mc = C["memory"].context() if C.get("memory") else ""
            if mc: ctx = mc + ("\n\n" + ctx if ctx else "")
            if "research" in kinds:                                  # the code already did the research: ONE model request writes the answer
                _direct_run(conv, run_id, prompt, ctx, emit, parts, wctx, mapper); final_text = "".join(parts)
                return
            team = C["build_team"](conv, context=ctx)
        wctx_holder["team"] = team
        for ev in team.run(prompt, stream=True, stream_events=True, run_id=run_id):
            for e in mapper.feed(ev):
                if e.type == EventType.TEXT_MESSAGE_CONTENT and not e.subagent_run_id:
                    parts.append(e.delta)
                if e.type == EventType.RUN_FINISHED and e.outcome is None:
                    ver = CC.check("".join(parts), wctx["sources"])
                    if ver is not None:
                        emit([aguimap.custom("verification", ver)])      # lands immediately before RUN_FINISHED
                    final_text = e.result or "".join(parts)
                    if kind == "council":
                        why = refusal.detect(final_text)
                        if why and C["rogue_state"]().get("present"):
                            emit([aguimap.custom("rogue_offer", {"reason": why[:120]})])
                emit([e])
            if mapper.terminal:
                break
        if not mapper.terminal:
            emit(mapper.close("STREAM_ENDED"))
    except Exception as ex:
        C["audit"](event="ui_run_exception", run=run_id, error=type(ex).__name__)
        emit(mapper.close("RUN_FAILED", "The council hit an internal error (%s)." % type(ex).__name__))
    finally:
        last = events[-1] if events else {}
        if last.get("type") == "RUN_FINISHED":
            status = "cancelled" if (last.get("outcome") or {}).get("type") == "cancelled" else "completed"
        elif last.get("type") == "RUN_ERROR":
            status = "failed"
        convo.finish_run(conv, run_id, status, events, final_text if status == "completed" else "".join(parts))
        C["audit"](event="ui_run", conv=conv, run=run_id, status=status, events=len(events))
        with cond:
            wctx_holder["done"] = True; cond.notify_all()
        with _RL:
            RUNS.pop(run_id, None)
        C["queue"].release()
        if kind == "rogue_team":
            threading.Thread(target=lambda: C["swap_model"]("main"), daemon=True, name="rogue-swap-back").start()      # the council comes back by itself
        q.put(None)


def stream_agui(handler, body):
    """POST /v1/agui  (body = AG-UI RunAgentInput). Streams text/event-stream of official AG-UI events."""
    try:
        inp = RunAgentInput.model_validate(body)
    except Exception:
        return handler._send(400, {"error": "body must be an AG-UI RunAgentInput"})
    prompt = ""
    for m in reversed(inp.messages or []):
        if getattr(m, "role", None) == "user":
            c = getattr(m, "content", "")
            prompt = c if isinstance(c, str) else " ".join(getattr(p, "text", "") for p in c if getattr(p, "text", None))
            break
    prompt = prompt.strip()
    if not prompt or len(prompt) > 8000:
        return handler._send(400, {"error": "a user message of 1-8000 characters is required"})
    conv = inp.thread_id
    run_id = inp.run_id or uuid.uuid4().hex
    fp = inp.forwarded_props if isinstance(getattr(inp, "forwarded_props", None), dict) else {}
    agent_kind = fp.get("agent") if fp.get("agent") in ("rogue_team", "rogue_direct") else "council"
    src = str(fp.get("handoff_run", ""))[:64]
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream"); handler.send_header("Cache-Control", "no-cache, no-transform")
    handler.send_header("X-Accel-Buffering", "no"); handler.send_header("Connection", "close"); handler.end_headers()

    def write(ev):
        handler.wfile.write(_sse(ev).encode()); handler.wfile.flush()

    def refuse(code, message):
        write(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=conv, run_id=run_id))
        write(RunErrorEvent(type=EventType.RUN_ERROR, message=message, code=code))

    convo.create(conv)
    user_label = prompt
    if agent_kind == "rogue_team":
        if not convo.offered(conv, src):
            return refuse("ROGUE_NOT_OFFERED", "Rogue's team only runs after the council declined a request, and no declined request was found for this.")
        if convo.handoff_used(conv, src):
            return refuse("ROGUE_ALREADY_USED", "This request was already sent to Rogue's team.")
        prompt = convo.user_prompt(conv, src) or prompt                 # the ORIGINAL request, taken from the stored conversation, not from the browser
        user_label = "Sent to Rogue's team: " + prompt
        st = {"ok": True, "reason": "handoff"}
        if not C["rogue_state"]().get("present"): return refuse("ROGUE_MODEL_MISSING", "Rogue's model is not installed in LM Studio.")
    elif agent_kind == "rogue_direct":
        # manual per-conversation switch (the composer toggle): no decline needed, runs the live prompt directly on Rogue's model.
        st = {"ok": True, "reason": "direct"}
        if not C["rogue_state"]().get("present"): return refuse("ROGUE_MODEL_MISSING", "Rogue's model is not installed in LM Studio.")
    else:
        st = C["inference_state"]()
    rogue_blocking = agent_kind == "council" and not st["ok"] and "Rogue model is loaded" in st["reason"]      # switching the toggle back: swap back in-thread instead of refusing
    if not st["ok"] and not rogue_blocking:
        return refuse("MODEL_UNAVAILABLE", "The model is not available: %s. Turn AI Mode on (Machine > AI Mode in this page, or the tray): that loads the model. With AI Mode off the model is unloaded so the PC is free." % st["reason"])
    if convo.active_run(conv):
        return refuse("CONVERSATION_BUSY", "This conversation already has a run in progress.")
    if not C["queue"].acquire(blocking=False):
        return refuse("BUSY", "The council is busy with other requests; try again shortly.")
    convo.begin_run(conv, run_id, user_label)
    q = queue.Queue(); holder = {"cond": threading.Condition(), "done": False, "agent": agent_kind, "src": src}
    mapper = aguimap.AguiMapper(conv, run_id, DISPLAY)
    with _RL:
        RUNS[run_id] = {"conv": conv, "started": time.time(), "holder": holder}
    threading.Thread(target=_run_in_thread, args=(conv, run_id, prompt, q, mapper, holder), daemon=True, name=f"ui-run-{run_id[:6]}").start()
    try:
        while True:
            try:
                ev = q.get(timeout=8)
            except queue.Empty:
                handler.wfile.write(b": keepalive\n\n"); handler.wfile.flush(); continue
            if ev is None:
                break
            write(ev)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass                                  # browser went away: the run continues and is persisted; reloading shows the result


def _say(emit, mapper, conv, run_id, text):
    mid = uuid.uuid4().hex
    emit([TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=mid, role="assistant", name="Orchestrator"), TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=mid, delta=text),
          TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=mid), RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=conv, run_id=run_id, result=text)])
    return text


OFF_TEXT = {"not_installed": "Browser and PC control is built but not installed on your PC yet, so I cannot open sites or fill forms. Tell Claude \"activate\" and it sets the companion and the Brave extension up; then you switch it on in Machine > Browser & PC.",
            "companion_off": "Browser and PC control is installed, but the companion on your PC is not running, so I cannot reach Brave. Start it (Machine > Browser & PC shows what is missing), then ask again.",
            "ready": "Browser and PC control is ready but switched off. Turn it on in Machine > Browser & PC, then ask again; I will ask your approval before the first thing I do."}


def _operator_run(conv, run_id, prompt, emit, parts, wctx, mapper):
    """Web tasks (sign up, fill a form, open a site) go to the Screen Operator, one agent with the bridge tools, and only while the bridge switch is on.
    With it off the reply is immediate and says exactly what is missing (no model call)."""
    SO = C["bridge"]; st = SO.state()
    if not st["armed"]:
        t = _say(emit, mapper, conv, run_id, OFF_TEXT.get(st["level"], OFF_TEXT["ready"])); parts.append(t); return t
    SO.CTX.set({"emit": emit, "run": run_id, "task_ok": False}); mapper.solo = True
    emit([aguimap.custom("agent", "Screen Operator")])
    agent = C["build_operator"](conv, C["memory"].context() if C.get("memory") else "")
    final = ""
    for ev in agent.run(prompt, session_id=conv, user_id="owner", stream=True, stream_events=True, run_id=run_id):
        for e in mapper.feed(ev):
            if e.type == EventType.TEXT_MESSAGE_CONTENT: parts.append(e.delta)
            if e.type == EventType.RUN_FINISHED and e.outcome is None: final = e.result or "".join(parts)
            emit([e])
        if mapper.terminal: break
    if not mapper.terminal: emit(mapper.close("STREAM_ENDED"))
    return final


_LEAK = re.compile(r"^\s*(?:<tool_call|`{0,3}\s*(?:web_research|web_fetch|web_search|vault_search|vault_read|read_health_facts|run_check|list_checks|delegate_task_to_member|check_python_syntax|get_skill_\w+)\s*\()", re.I)


def _direct_run(conv, run_id, prompt, ctx, emit, parts, wctx, mapper):
    """One tool-less model call. The thinking model sometimes tries to call a tool anyway and prints `<tool_call>` as text: that is held back, never shown,
    and the answer is retried once with a plain-prose reminder, then falls back to the research excerpts themselves."""
    mid = uuid.uuid4().hex
    emit([TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=mid, role="assistant", name="Orchestrator")])
    text = ""
    for attempt in (0, 1):
        agent = C["build_direct"](conv if attempt == 0 else conv + "-retry", ctx); buf = ""; shown = False; leaked = False      # the retry gets a clean session so the leaked attempt is not in its history
        msg = prompt if attempt == 0 else ("Using only the research in your instructions, answer this in plain prose (no tool calls, nothing to look up: it is all there):\n\n" + prompt)
        for ev in agent.run(msg, session_id=conv if attempt == 0 else conv + "-retry", user_id="owner", stream=True, run_id=run_id if attempt == 0 else None):
            kind, c = str(getattr(ev, "event", "")), getattr(ev, "content", None)
            if kind == "RunContent" and isinstance(c, str) and c:
                buf += c
                if shown: emit([TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=mid, delta=c)]); continue
                head = buf.lstrip()
                if _LEAK.match(head) or "<tool_call>" in buf: leaked = True; continue
                if len(head) >= 48:                                                       # enough to know it is not a tool call: release what was held
                    shown = True; emit([TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=mid, delta=buf)])
            elif kind == "RunError":
                emit([TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=mid)]); emit(mapper.close("RUN_FAILED", "The council hit an error while writing the answer.")); return
        if not shown and buf and not leaked and not _LEAK.match(buf):
            shown = True; emit([TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=mid, delta=buf)])
        if shown and "<tool_call>" not in buf: text = buf; break
        if shown: text = buf; break
    if not text:
        text = ("I fetched the pages but could not turn them into an answer this time. The sources are listed below; ask again or ask the Researcher to dig deeper.\n\n"
                + "\n".join(f"- [S{i}] {v.get('url', '')}" for i, v in list((wctx.get("sources") or {}).items())[:6]))
        emit([TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=mid, delta=text)])
    parts.append(text)
    emit([TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=mid)])
    ver = CC.check(text, wctx["sources"])
    if ver is not None: emit([aguimap.custom("verification", ver)])
    why = refusal.detect(text)
    if why and C["rogue_state"]().get("present"): emit([aguimap.custom("rogue_offer", {"reason": why[:120]})])
    emit([RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=conv, run_id=run_id, result=text[:20000] or None)])


def attach(handler, run_id, start):
    """GET /v1/runs/<id>/events/<n>: replay the run's events from index n, then follow it live until it ends. A refresh, a tab switch or a dropped phone
    connection re-attaches here and sees the same progress; the run itself never depended on the browser."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream"); handler.send_header("Cache-Control", "no-cache, no-transform")
    handler.send_header("X-Accel-Buffering", "no"); handler.send_header("Connection", "close"); handler.end_headers()
    with _RL:
        r = RUNS.get(run_id)
    try:
        if r is None:                                          # finished (or never existed): serve the stored events
            for e in (convo.run_events(run_id) or [])[start:]:
                handler.wfile.write(("data: " + json.dumps(e) + "\n\n").encode())
            handler.wfile.flush(); return
        h, i = r["holder"], start
        while True:
            with h["cond"]:
                evs = h.get("events") or []
                if i >= len(evs) and not h["done"]:
                    h["cond"].wait(timeout=8)
                evs = h.get("events") or []; batch = evs[i:]; done = h["done"]
            for e in batch:
                handler.wfile.write(("data: " + json.dumps(e) + "\n\n").encode())
            i += len(batch)
            if batch: handler.wfile.flush()
            elif done: break
            else: handler.wfile.write(b": keepalive\n\n"); handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass


def cancel(run_id):
    with _RL:
        r = RUNS.get(run_id)
    if not r:
        return False
    try:
        C["Team"].cancel_run(run_id)          # Agno stops between events; closing the model stream makes LM Studio stop generating
        try: C["Agent"].cancel_run(run_id)
        except Exception: pass
        return True
    except Exception:
        return False
