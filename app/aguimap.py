"""Agno Team run events -> AG-UI protocol events (official `ag_ui.core` classes, ag-ui-protocol 1.0.0).

Every mapping below was written against REAL captured Agno 3.1.12 streams (tests/fixtures), not guessed:

  (adapter, at start)                                   RUN_STARTED
  TeamModelRequestStarted / ...Completed                STEP_STARTED / STEP_FINISHED   "Orchestrator is thinking"
  ModelRequestStarted / ...Completed   (member)         STEP_STARTED / STEP_FINISHED   attributed with subagentRunId
  TeamRunContent / RunContent with answer `content`     TEXT_MESSAGE_START / CONTENT / END   (+ subagentRunId for members)
  TeamToolCallStarted / ToolCallStarted                 TOOL_CALL_START + ARGS + END
  TeamToolCallCompleted / ToolCallCompleted             TOOL_CALL_RESULT
  member RunStarted   (after delegate_task_to_member)   SUBAGENT_STARTED  (parentToolCallId = the delegate call)
  member RunCompleted                                   SUBAGENT_FINISHED
  member RunError                                       SUBAGENT_ERROR
  TeamRunCompleted                                      RUN_FINISHED
  TeamRunError                                          RUN_ERROR
  TeamRunCancelled / RunCancelled                       RUN_FINISHED (outcome: cancelled)
  anything else (compression, memory, follow-ups...)    ignored - never invented

Private reasoning (`reasoning_content`) is NEVER forwarded; only that the agent is thinking (a STEP) is shown.
The mapper is a state machine that guarantees the protocol's ordering rules whatever the input does (duplicate starts from
run-level retries, missing completions, truncated streams, errors mid-message): see close().
"""
import json
import re
import uuid

from ag_ui.core import (
    EventType, RunErrorEvent, RunFinishedCancelledOutcome, RunFinishedEvent, RunStartedEvent, StepFinishedEvent, StepStartedEvent,
    SubagentErrorEvent, SubagentFinishedEvent, SubagentFinishedSuccessOutcome, SubagentStartedEvent, TextMessageContentEvent,
    TextMessageEndEvent, TextMessageStartEvent, ToolCallArgsEvent, ToolCallEndEvent, ToolCallResultEvent, ToolCallStartEvent, CustomEvent,
)

LEADER = "Orchestrator"
DELEGATE_TOOLS = ("delegate_task_to_member", "delegate_task_to_members", "transfer_task_to_member")
MAX_ARGS, MAX_RESULT, MAX_TASK = 4000, 4000, 400
_THINK = re.compile(r"<think>.*?</think>", re.S)


def _g(ev, name, default=None):
    """attribute access that also works on dict fixtures"""
    if isinstance(ev, dict):
        return ev.get(name, default)
    return getattr(ev, name, default)


def _tool(ev):
    t = _g(ev, "tool")
    if t is None:
        return None
    return {k: (t.get(k) if isinstance(t, dict) else getattr(t, k, None)) for k in ("tool_call_id", "tool_name", "tool_args", "result", "tool_call_error")}


def _args_json(a):
    if a is None:
        return "{}"
    if isinstance(a, str):
        return a[:MAX_ARGS]
    try:
        return json.dumps(a, default=str)[:MAX_ARGS]
    except (TypeError, ValueError):
        return json.dumps({"_": str(a)[:MAX_ARGS]})


def _parse_args(a):
    """tool args as a dict, if they are one (fixtures store str(dict) via Python repr)"""
    if isinstance(a, dict):
        return a
    if isinstance(a, str):
        try:
            return json.loads(a)
        except ValueError:
            try:
                import ast
                v = ast.literal_eval(a)
                return v if isinstance(v, dict) else {}
            except (ValueError, SyntaxError):
                return {}
    return {}


class AguiMapper:
    """feed(agno_event) -> [AG-UI events]; close(reason) -> terminal sequence if the stream ended without one."""

    def __init__(self, thread_id, run_id, display_names=None):
        self.thread_id, self.run_id = thread_id, run_id
        self.names = display_names or {}
        self.started = False
        self.terminal = False
        self.msg = None                    # {"id","owner"} current open text message
        self.subs = {}                     # member run_id -> {"name","open_steps":[...]}
        self.leader_steps = []             # stack of open leader step names
        self.pending_delegations = []      # [{"tool_call_id","member_id","task"}] waiting for the member's RunStarted
        self.open_tools = {}               # tool_call_id -> {"ended": bool}
        self._think_buf = ""

    # ---------- helpers ----------
    def _display(self, raw):
        return self.names.get(raw, raw) if raw else raw

    def _owner(self, ev):
        """-> (subagent_run_id or None, display name). Leader events carry team_*; member events carry agent_*."""
        name = _g(ev, "event") or ""
        if getattr(self, "solo", False): return None, LEADER          # a single agent run: its events are the leader's
        if name.startswith("Team") or (_g(ev, "team_name") and not _g(ev, "agent_name")):
            return None, LEADER
        return _g(ev, "run_id"), self._display(_g(ev, "agent_name") or _g(ev, "agent_id"))

    def _close_message(self, out):
        if self.msg:
            out.append(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=self.msg["id"], subagent_run_id=self.msg["sub"]))
            self.msg = None

    def _close_steps(self, out, sub_id=None):
        if sub_id is None:
            while self.leader_steps:
                out.append(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=self.leader_steps.pop()))
        else:
            s = self.subs.get(sub_id)
            while s and s["steps"]:
                out.append(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=s["steps"].pop(), subagent_run_id=sub_id))

    def start(self):
        if self.started:
            return []
        self.started = True
        return [RunStartedEvent(type=EventType.RUN_STARTED, thread_id=self.thread_id, run_id=self.run_id)]

    # ---------- main entry ----------
    def feed(self, ev):
        out = []
        if self.terminal:
            return out                      # nothing after the terminal event, whatever Agno still sends
        out += self.start()
        kind = _g(ev, "event") or ""
        if getattr(self, "solo", False):                      # one Agent run, not a Team: its run events are the run's own
            kind = {"RunStarted": "TeamRunStarted", "RunContent": "TeamRunContent", "RunCompleted": "TeamRunCompleted", "RunError": "TeamRunError"}.get(kind, kind)
        sub, who = self._owner(ev)

        if kind in ("TeamRunStarted",):
            return out                      # RUN_STARTED already sent; a run-level retry repeats this event: never duplicate
        if kind in ("TeamModelRequestStarted", "ModelRequestStarted"):
            step = f"{who} is thinking"
            if sub is None:
                self._close_steps(out)      # a retry can start a new request with the old one never completed
                self.leader_steps.append(step)
            else:
                self.subs.setdefault(sub, {"name": who, "steps": []})["steps"].append(step)
            out.append(StepStartedEvent(type=EventType.STEP_STARTED, step_name=step, subagent_run_id=sub))
            return out
        if kind in ("TeamModelRequestCompleted", "ModelRequestCompleted"):
            if sub is None and self.leader_steps:
                out.append(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=self.leader_steps.pop()))
            elif sub is not None and sub in self.subs and self.subs[sub]["steps"]:
                out.append(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=self.subs[sub]["steps"].pop(), subagent_run_id=sub))
            return out
        if kind in ("TeamRunContent", "RunContent"):
            text = _g(ev, "content")
            if not isinstance(text, str) or not text:
                return out                  # reasoning-only delta: private, dropped
            text = self._strip_think(text)
            if not text:
                return out
            if self.msg is None or self.msg["sub"] != sub:
                self._close_message(out)
                mid = uuid.uuid4().hex
                self.msg = {"id": mid, "sub": sub}
                out.append(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=mid, role="assistant", name=who, subagent_run_id=sub))
            out.append(TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=self.msg["id"], delta=text, subagent_run_id=sub))
            return out
        if kind in ("TeamToolCallStarted", "ToolCallStarted"):
            t = _tool(ev)
            if not t or not t.get("tool_call_id"):
                return out
            self._close_message(out)
            tcid, name = str(t["tool_call_id"]), str(t.get("tool_name") or "tool")
            if tcid in self.open_tools:
                return out                  # duplicate start (retry)
            self.open_tools[tcid] = {"ended": False, "sub": sub}
            out.append(ToolCallStartEvent(type=EventType.TOOL_CALL_START, tool_call_id=tcid, tool_call_name=name, subagent_run_id=sub))
            out.append(ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS, tool_call_id=tcid, delta=_args_json(t.get("tool_args")), subagent_run_id=sub))
            out.append(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tcid, subagent_run_id=sub))
            self.open_tools[tcid]["ended"] = True
            if sub is None and name in DELEGATE_TOOLS:
                a = _parse_args(t.get("tool_args"))
                self.pending_delegations.append({"tool_call_id": tcid, "member_id": str(a.get("member_id") or ""), "task": str(a.get("task") or "")[:MAX_TASK]})
            return out
        if kind in ("TeamToolCallCompleted", "ToolCallCompleted", "TeamToolCallError", "ToolCallError"):
            t = _tool(ev)
            if not t or not t.get("tool_call_id"):
                return out
            tcid = str(t["tool_call_id"])
            if tcid not in self.open_tools:
                return out                  # a result without a start: do not invent the start
            if self.open_tools[tcid].get("done"):
                return out
            self.open_tools[tcid]["done"] = True
            failed = kind.endswith("Error") or str(t.get("tool_call_error")).lower() == "true"
            res = t.get("result") if t.get("result") is not None else _g(ev, "error")
            body = ("ERROR: " if failed else "") + (str(res) if res is not None else "")
            out.append(ToolCallResultEvent(type=EventType.TOOL_CALL_RESULT, message_id=uuid.uuid4().hex, tool_call_id=tcid, content=body[:MAX_RESULT] or "(no output)",
                                           role="tool", subagent_run_id=self.open_tools[tcid]["sub"]))
            return out
        if kind == "RunStarted":            # a member (specialist) begins
            rid = _g(ev, "run_id")
            if not rid or rid in self.subs:
                return out
            self._close_message(out)
            pend = self.pending_delegations.pop(0) if self.pending_delegations else None
            self.subs[rid] = {"name": who, "steps": [], "done": False}
            out.append(SubagentStartedEvent(type=EventType.SUBAGENT_STARTED, subagent_run_id=rid, name=who,
                                            description=(pend or {}).get("task") or None, parent_tool_call_id=(pend or {}).get("tool_call_id")))
            return out
        if kind == "RunCompleted":
            rid = _g(ev, "run_id")
            s = self.subs.get(rid)
            if not s or s.get("done"):
                return out
            if self.msg and self.msg["sub"] == rid:
                self._close_message(out)
            self._close_steps(out, rid)
            s["done"] = True
            res = _g(ev, "content")
            out.append(SubagentFinishedEvent(type=EventType.SUBAGENT_FINISHED, subagent_run_id=rid, result=(str(res)[:MAX_RESULT] if res else None),
                                             outcome=SubagentFinishedSuccessOutcome()))
            return out
        if kind == "RunError":
            rid = _g(ev, "run_id")
            s = self.subs.get(rid)
            if not s or s.get("done"):
                return out
            if self.msg and self.msg["sub"] == rid:
                self._close_message(out)
            self._close_steps(out, rid)
            s["done"] = True
            out.append(SubagentErrorEvent(type=EventType.SUBAGENT_ERROR, subagent_run_id=rid, message=self._safe_error(ev), code="SUBAGENT_FAILED"))
            return out
        if kind == "TeamRunCompleted":
            out += self._wind_down(sub_reason="INTERRUPTED")
            res = _g(ev, "content")
            self.terminal = True
            out.append(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=self.thread_id, run_id=self.run_id, result=(str(res)[:MAX_RESULT] if res else None)))
            return out
        if kind == "TeamRunError":
            out += self._wind_down(sub_reason="RUN_FAILED")
            self.terminal = True
            out.append(RunErrorEvent(type=EventType.RUN_ERROR, message=self._safe_error(ev), code="RUN_FAILED"))
            return out
        if kind in ("TeamRunCancelled", "RunCancelled"):
            if kind == "RunCancelled" and sub is not None:
                return out                  # a member cancelled inside a run that the leader will finish or cancel itself
            out += self._wind_down(sub_reason="CANCELLED")
            self.terminal = True
            out.append(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=self.thread_id, run_id=self.run_id, outcome=RunFinishedCancelledOutcome()))
            return out
        return out                          # unknown / irrelevant Agno events are ignored, never invented into the protocol

    # ---------- termination helpers ----------
    def _wind_down(self, sub_reason):
        """close everything still open so the terminal event is legal"""
        out = []
        self._flush_held(out)
        self._close_message(out)
        for rid, s in list(self.subs.items()):
            if not s.get("done"):
                self._close_steps(out, rid)
                s["done"] = True
                out.append(SubagentErrorEvent(type=EventType.SUBAGENT_ERROR, subagent_run_id=rid, message=f"{s['name']} did not finish ({sub_reason.lower()})", code=sub_reason))
        self._close_steps(out)
        return out

    def close(self, reason="STREAM_ENDED", message="The run ended without a final result"):
        """call when the Agno stream stops (exhausted, exception, client gone) without a terminal event"""
        out = self.start()
        if self.terminal:
            return []
        out += self._wind_down(sub_reason=reason)
        self.terminal = True
        if reason == "CANCELLED":
            out.append(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=self.thread_id, run_id=self.run_id, outcome=RunFinishedCancelledOutcome()))
        else:
            out.append(RunErrorEvent(type=EventType.RUN_ERROR, message=message[:400], code=reason))
        return out

    # ---------- text hygiene ----------
    def _strip_think(self, text):
        """defence in depth: if a model ever puts <think>…</think> inside content, never show it (works across deltas).
        Anything that could still turn into a '<think>' tag is held back until it is known not to be one."""
        buf = _THINK.sub("", self._think_buf + text)
        i = buf.find("<think>")
        if i >= 0:                          # an unterminated block: hold it (and everything after) until it closes
            self._think_buf = buf[i:]
            return buf[:i]
        for n in range(min(len("<think>") - 1, len(buf)), 0, -1):      # a trailing fragment like '<thi'
            if "<think>".startswith(buf[-n:]):
                self._think_buf = buf[-n:]
                return buf[:-n]
        self._think_buf = ""
        return buf

    def _flush_held(self, out):
        """at the end of the run: a held fragment that never became a <think> block was ordinary text"""
        held, self._think_buf = self._think_buf, ""
        if held and not held.startswith("<think>") and self.msg is not None:
            out.append(TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=self.msg["id"], delta=held, subagent_run_id=self.msg["sub"]))

    @staticmethod
    def _safe_error(ev):
        msg = str(_g(ev, "error") or _g(ev, "content") or "The run failed")
        return re.sub(r"(?i)(bearer\s+\S+|api[_-]?key\S*|password\S*)", "<redacted>", msg)[:400]


def custom(name, value, thread_run=None):
    return CustomEvent(type=EventType.CUSTOM, name=name, value=value)
