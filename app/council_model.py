"""CouncilLMStudio: Agno's LMStudio model with (1) a process-global inference gate and (2) reply recovery.

Gate: ONE threading.Semaphore(1) shared by sync, async and streaming calls and by every agent that uses a
CouncilLMStudio, so at most one request is in flight to the PC. Held for the whole request, including a full stream.
LM Studio itself is also loaded with --parallel 1 (second layer; it queues, see vault).

Recovery (councilguard.py): empty reply -> retry with a nudge; <tool_call> leaked into reasoning -> promoted to a real
tool call; malformed/unknown tool call -> one corrective retry. All retries happen inside the same gate hold.
"""
import asyncio
import contextvars
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional

from openai.types.chat import ChatCompletion, ChatCompletionChunk

from agno.models.lmstudio import LMStudio

import councilguard as G

GATE = threading.Semaphore(1)
_STATS_LOCK = threading.Lock()
STATS = {"calls": 0, "inflight": 0, "max_inflight": 0, "empty_retries": 0, "promoted": 0, "malformed_retries": 0, "gave_up": 0}
CALL_LOG = os.environ.get("COUNCIL_CALL_LOG")  # jsonl, one line per upstream call


class PolicyPause(Exception):
    """Unattended work is no longer allowed (window closed, AI Mode off, gaming, low RAM...). Raised BEFORE a request is sent,
    never during one: an in-flight inference request is always allowed to finish."""


# set by the job runner for unattended work: {"policy": callable -> (ok, reasons), "paused": None | str}
JOB_CTX = contextvars.ContextVar("council_job_ctx", default=None)


def _policy_check():
    ctx = JOB_CTX.get()
    if ctx is None:          # interactive request from the owner: not subject to the window / AI Mode policy
        return
    ok, why = ctx["policy"]()
    if not ok:
        ctx["paused"] = "; ".join(why)
        raise PolicyPause(ctx["paused"])


TOOL_ROUNDS = int(os.environ.get("COUNCIL_TOOL_ROUNDS", "10"))
BUDGET_NUDGE = ("Your tool budget for this task is used up. Do NOT call any tools. Write your final answer now from what you already have: "
                "cite sources as [S#] where you have them, and mark anything unsupported as UNCONFIRMED.")


def _budget(kw):
    """A thinking model can keep REQUESTING tools after the framework starts refusing them, looping until the context is full.
    Once a run has consumed its tool rounds, take the tools away so the only possible reply is a text answer."""
    msgs = kw.get("messages") or []
    n = 0
    for m in reversed(msgs):
        r = m.get("role") if isinstance(m, dict) else None
        if r == "user":
            break
        if r == "tool":
            n += 1
    if n >= TOOL_ROUNDS and kw.get("tools"):
        kw = dict(kw); kw.pop("tools", None); kw.pop("tool_choice", None); kw.pop("parallel_tool_calls", None)
        kw["messages"] = list(msgs) + [{"role": "user", "content": BUDGET_NUDGE}]
        with _STATS_LOCK:
            STATS["tool_budget_cutoffs"] = STATS.get("tool_budget_cutoffs", 0) + 1
    return kw


class EmptyReply(Exception):
    """The model returned nothing usable even after recovery attempts."""


def _log(**kw):
    """Best-effort: a logging problem (full disk, missing dir) must never break an inference request."""
    if CALL_LOG:
        try:
            with _STATS_LOCK, open(CALL_LOG, "a") as f:
                f.write(json.dumps(kw) + "\n")
        except OSError:
            pass


def _enter():
    with _STATS_LOCK:
        STATS["calls"] += 1; STATS["inflight"] += 1
        STATS["max_inflight"] = max(STATS["max_inflight"], STATS["inflight"])


def _exit():
    with _STATS_LOCK:
        STATS["inflight"] -= 1


@contextmanager
def gate_sync():
    t0 = time.time(); GATE.acquire(); t1 = time.time()
    try:
        _policy_check()
    except BaseException:
        GATE.release(); raise
    _enter()
    try:
        yield {"wait": t1 - t0, "t_start": t1}
    finally:
        _exit(); GATE.release()


class _AGate:
    """async context manager around the global threading semaphore (cancel-safe, loop-independent)."""
    async def __aenter__(self):
        self.t0 = time.time()
        fut = asyncio.ensure_future(asyncio.to_thread(GATE.acquire))
        try:
            await asyncio.shield(fut)
        except asyncio.CancelledError:
            fut.add_done_callback(lambda f: GATE.release() if (not f.cancelled() and f.exception() is None) else None)
            raise
        self.t1 = time.time()
        try:
            _policy_check()
        except BaseException:
            GATE.release(); raise
        _enter()
        return {"wait": self.t1 - self.t0, "t_start": self.t1}

    async def __aexit__(self, *a):
        _exit(); GATE.release()


def _schemas(kw):
    return {t["function"]["name"]: t["function"].get("parameters", {}) for t in (kw.get("tools") or [])}


def _msg_dict(resp):
    m = resp.choices[0].message
    return {"content": m.content, "reasoning_content": getattr(m, "reasoning_content", None),
            "tool_calls": [t.model_dump() for t in (m.tool_calls or [])] or None}


def _promote(resp, calls):
    from openai.types.chat.chat_completion_message_function_tool_call import ChatCompletionMessageFunctionToolCall as TC
    from openai.types.chat.chat_completion_message_function_tool_call import Function
    resp.choices[0].message.tool_calls = [
        TC(id=f"promoted-{uuid.uuid4().hex[:8]}", type="function", function=Function(name=c["name"], arguments=json.dumps(c["arguments"])))
        for c in calls]
    resp.choices[0].message.content = resp.choices[0].message.content or ""
    resp.choices[0].finish_reason = "tool_calls"
    return resp


def _bad_tool_call(resp, kw):
    """-> error text if any tool call is unusable, else None."""
    sch = _schemas(kw)
    for t in resp.choices[0].message.tool_calls or []:
        _, err = G.validate_call(t.function.name, t.function.arguments, sch)
        if err:
            return err
    return None


def _fix(resp: ChatCompletion, kw, create, max_recover, log):
    """sync recovery core; `create` is a callable(**kw)->ChatCompletion (already gated by the caller)."""
    attempts = 0
    while True:
        md = _msg_dict(resp); fin = resp.choices[0].finish_reason; kind = G.classify(md, fin)
        if kind == "leaked_tool_call":
            calls = G.raw_tool_calls(md["reasoning_content"] or "")
            with _STATS_LOCK: STATS["promoted"] += 1
            log["recovery"].append("promoted"); resp = _promote(resp, calls); continue
        if kind == "tool_calls":
            err = _bad_tool_call(resp, kw)
            if err and attempts < max_recover:
                attempts += 1
                with _STATS_LOCK: STATS["malformed_retries"] += 1
                log["recovery"].append("malformed:" + err[:60])
                resp = create(**dict(kw, messages=list(kw["messages"]) + [{"role": "user", "content": G.NUDGE_BAD_ARGS.format(err=err)}]))
                continue
            return resp
        if kind in ("empty", "truncated") and not (resp.choices[0].message.content or "").strip() and attempts < max_recover:
            attempts += 1
            with _STATS_LOCK: STATS["empty_retries"] += 1
            log["recovery"].append(kind)
            resp = create(**dict(kw, messages=list(kw["messages"]) + [{"role": "user", "content": G.NUDGE_EMPTY}]))
            continue
        if kind == "empty":
            with _STATS_LOCK: STATS["gave_up"] += 1
            raise EmptyReply("model returned an empty reply after %d recovery attempt(s)" % attempts)
        return resp


def _chunk_from(resp) -> ChatCompletionChunk:
    m = resp.choices[0].message
    delta = {"role": "assistant", "content": m.content or ""}
    if m.tool_calls:
        delta["tool_calls"] = [{"index": i, "id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}}
                               for i, t in enumerate(m.tool_calls)]
    return ChatCompletionChunk.model_validate({"id": resp.id, "object": "chat.completion.chunk", "created": resp.created, "model": resp.model,
                                               "choices": [{"index": 0, "delta": delta, "finish_reason": resp.choices[0].finish_reason}]})


class _Completions:
    def __init__(self, real, is_async, max_recover):
        self._real, self._async, self._max = real, is_async, max_recover

    # ---- sync client ----
    def _create_sync(self, **kw):
        kw = _budget(kw)
        if kw.get("stream"):
            return self._stream_sync(kw)
        log = {"t_req": time.time(), "stream": False, "recovery": []}
        with gate_sync() as g:
            resp = self._real.create(**kw)
            resp = _fix(resp, kw, lambda **k: self._real.create(**k), self._max, log)
        u = getattr(resp, "usage", None)
        if u: log.update(ptok=u.prompt_tokens, ctok=u.completion_tokens)
        log.update(wait=round(g["wait"], 3), dur=round(time.time() - g["t_start"], 3), t_start=g["t_start"]); _log(**log)
        return resp

    def _stream_sync(self, kw):
        log = {"t_req": time.time(), "stream": True, "recovery": []}
        with gate_sync() as g:
            seen_c = seen_t = False; reasoning = ""
            for chunk in self._real.create(**kw):
                for c in chunk.choices:
                    d = c.delta
                    seen_c = seen_c or bool(d.content); seen_t = seen_t or bool(d.tool_calls)
                    reasoning += getattr(d, "reasoning_content", None) or ""
                yield chunk
            if not seen_c and not seen_t:
                calls = G.raw_tool_calls(reasoning)
                if calls:
                    with _STATS_LOCK: STATS["promoted"] += 1
                    log["recovery"].append("promoted")
                    yield _chunk_from(_promote(_fake_resp(kw, calls), calls))
                else:
                    kw2 = dict(kw, stream=False, messages=list(kw["messages"]) + [{"role": "user", "content": G.NUDGE_EMPTY}])
                    log["recovery"].append("empty_stream");
                    with _STATS_LOCK: STATS["empty_retries"] += 1
                    resp = _fix(self._real.create(**kw2), kw2, lambda **k: self._real.create(**k), self._max, log)
                    yield _chunk_from(resp)
        log.update(wait=round(g["wait"], 3), dur=round(time.time() - g["t_start"], 3), t_start=g["t_start"]); _log(**log)

    # ---- async client ----
    async def _create_async(self, **kw):
        kw = _budget(kw)
        if kw.get("stream"):
            return self._stream_async(kw)
        log = {"t_req": time.time(), "stream": False, "recovery": []}
        async with _AGate() as g:
            resp = await self._real.create(**kw)
            resp = await _afix(resp, kw, self._real.create, self._max, log)
        u = getattr(resp, "usage", None)
        if u: log.update(ptok=u.prompt_tokens, ctok=u.completion_tokens)
        log.update(wait=round(g["wait"], 3), dur=round(time.time() - g["t_start"], 3), t_start=g["t_start"]); _log(**log)
        return resp

    async def _stream_async(self, kw):
        log = {"t_req": time.time(), "stream": True, "recovery": []}
        async with _AGate() as g:
            seen_c = seen_t = False; reasoning = ""
            async for chunk in await self._real.create(**kw):
                for c in chunk.choices:
                    d = c.delta
                    seen_c = seen_c or bool(d.content); seen_t = seen_t or bool(d.tool_calls)
                    reasoning += getattr(d, "reasoning_content", None) or ""
                yield chunk
            if not seen_c and not seen_t:
                calls = G.raw_tool_calls(reasoning)
                if calls:
                    with _STATS_LOCK: STATS["promoted"] += 1
                    log["recovery"].append("promoted"); yield _chunk_from(_promote(_fake_resp(kw, calls), calls))
                else:
                    kw2 = dict(kw, stream=False, messages=list(kw["messages"]) + [{"role": "user", "content": G.NUDGE_EMPTY}])
                    log["recovery"].append("empty_stream")
                    with _STATS_LOCK: STATS["empty_retries"] += 1
                    resp = await _afix(await self._real.create(**kw2), kw2, self._real.create, self._max, log)
                    yield _chunk_from(resp)
        log.update(wait=round(g["wait"], 3), dur=round(time.time() - g["t_start"], 3), t_start=g["t_start"]); _log(**log)

    def create(self, **kw):
        return self._create_async(**kw) if self._async else self._create_sync(**kw)


async def _afix(resp, kw, acreate, max_recover, log):
    """async twin of _fix: run the same decision loop but await the retries."""
    attempts = 0
    while True:
        md = _msg_dict(resp); fin = resp.choices[0].finish_reason; kind = G.classify(md, fin)
        if kind == "leaked_tool_call":
            with _STATS_LOCK: STATS["promoted"] += 1
            log["recovery"].append("promoted"); resp = _promote(resp, G.raw_tool_calls(md["reasoning_content"] or "")); continue
        if kind == "tool_calls":
            err = _bad_tool_call(resp, kw)
            if err and attempts < max_recover:
                attempts += 1
                with _STATS_LOCK: STATS["malformed_retries"] += 1
                log["recovery"].append("malformed:" + err[:60])
                resp = await acreate(**dict(kw, messages=list(kw["messages"]) + [{"role": "user", "content": G.NUDGE_BAD_ARGS.format(err=err)}]))
                continue
            return resp
        if kind in ("empty", "truncated") and not (resp.choices[0].message.content or "").strip() and attempts < max_recover:
            attempts += 1
            with _STATS_LOCK: STATS["empty_retries"] += 1
            log["recovery"].append(kind)
            resp = await acreate(**dict(kw, messages=list(kw["messages"]) + [{"role": "user", "content": G.NUDGE_EMPTY}]))
            continue
        if kind == "empty":
            with _STATS_LOCK: STATS["gave_up"] += 1
            raise EmptyReply("model returned an empty reply after %d recovery attempt(s)" % attempts)
        return resp


def _fake_resp(kw, calls):
    return ChatCompletion.model_validate({"id": "promoted", "object": "chat.completion", "created": int(time.time()), "model": kw.get("model", ""),
                                          "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": ""}}]})


class _Wrapped:
    """proxy exposing client.chat.completions.create through the guard; everything else passes through."""
    def __init__(self, client, is_async, max_recover):
        self._c = client
        self.chat = type("Chat", (), {"completions": _Completions(client.chat.completions, is_async, max_recover)})()

    def __getattr__(self, n): return getattr(self._c, n)


@dataclass
class CouncilLMStudio(LMStudio):
    max_recover: int = 2

    def get_client(self):
        c = super().get_client()
        return c if isinstance(c, _Wrapped) else _remember(self, "client", _Wrapped(c, False, self.max_recover))

    def get_async_client(self):
        c = super().get_async_client()
        return c if isinstance(c, _Wrapped) else _remember(self, "async_client", _Wrapped(c, True, self.max_recover))


def _remember(model, attr, wrapped):
    setattr(model, attr, wrapped)
    return wrapped
