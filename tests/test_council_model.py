import asyncio, json, threading, time, types
import council_model as CM
from fakes import *

def wrapped(script, is_async, delay=0.0, max_recover=2):
    f = Fake(script, delay); f.is_async = is_async
    return f, CM._Wrapped(f, is_async, max_recover)

ok = []
def check(name, cond, extra=""):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name, extra)

def reset(): 
    for k in CM.STATS: CM.STATS[k] = 0

# 1) gate: 4 async + 4 sync threads at once -> never >1 live upstream
reset()
fa, wa = wrapped([comp("hi")], True, 0.15); fs, ws = wrapped([comp("hi")], False, 0.15)
async def many(): await asyncio.gather(*[wa.chat.completions.create(model="m", messages=[]) for _ in range(4)])
th = [threading.Thread(target=lambda: ws.chat.completions.create(model="m", messages=[])) for _ in range(4)]
t0 = time.time(); [t.start() for t in th]; asyncio.run(many()); [t.join() for t in th]; el = time.time() - t0
check("gate_global_sync_async", CM.STATS["max_inflight"] == 1 and el >= 8 * 0.15 * 0.95, f"max_inflight={CM.STATS['max_inflight']} calls={CM.STATS['calls']} {el:.2f}s (>=1.2s if serial)")
check("fake_upstream_never_overlapped", fa.maxlive <= 1 and fs.maxlive <= 1 and CM.STATS["calls"] == 8)

# 2) streaming holds the gate for the whole stream, and across two loops
reset()
fa, wa = wrapped([[chunk(reasoning="r"), chunk(content="a"), chunk(content="b", finish="stop")]], True, 0.05)
async def streams():
    async def one():
        out = []
        async for c in await wa.chat.completions.create(model="m", messages=[], stream=True): out.append(c)
        return out
    return await asyncio.gather(one(), one(), one())
r = asyncio.run(streams()); r2 = asyncio.run(streams())
check("stream_gate_exclusive_two_loops", CM.STATS["max_inflight"] == 1 and all(len(x) == 3 for x in r + r2), f"max_inflight={CM.STATS['max_inflight']}")

# 3) empty reply -> nudge retry -> good
reset(); f, w = wrapped([comp("", "thinking"), comp("answer")], False)
resp = w.chat.completions.create(model="m", messages=[{"role": "user", "content": "q"}])
check("empty_retry_recovers", resp.choices[0].message.content == "answer" and CM.STATS["empty_retries"] == 1 and "empty" in f.calls[1]["messages"][-1]["content"])
# 4) leaked tool call promoted
reset(); f, w = wrapped([comp("", LEAK)], False)
resp = w.chat.completions.create(model="m", messages=[], tools=TOOLS)
tc = resp.choices[0].message.tool_calls
check("leaked_tool_call_promoted", tc and tc[0].function.name == "get_time" and resp.choices[0].finish_reason == "tool_calls" and f.n == 1 and CM.STATS["promoted"] == 1)
# 5) malformed args -> corrective retry
bad = comp("", None, [{"id": "1", "type": "function", "function": {"name": "add", "arguments": "{oops"}}], "tool_calls")
good = comp("", None, [{"id": "2", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}], "tool_calls")
reset(); f, w = wrapped([bad, good], False)
resp = w.chat.completions.create(model="m", messages=[], tools=TOOLS)
check("malformed_call_retry", resp.choices[0].message.tool_calls[0].function.name == "get_time" and CM.STATS["malformed_retries"] == 1 and f.n == 2)
# 6) give up -> EmptyReply
reset(); f, w = wrapped([comp("", "nothing")], False)
try: w.chat.completions.create(model="m", messages=[]); raised = False
except CM.EmptyReply: raised = True
check("gives_up_after_max_recover", raised and f.n == 3 and CM.STATS["gave_up"] == 1, f"upstream calls={f.n}")
# 7) async twins
reset(); f, w = wrapped([comp("", LEAK)], True)
resp = asyncio.run(w.chat.completions.create(model="m", messages=[], tools=TOOLS))
check("async_leaked_promoted", resp.choices[0].message.tool_calls[0].function.name == "get_time")
reset(); f, w = wrapped([comp("", "x"), comp("ok")], True)
resp = asyncio.run(w.chat.completions.create(model="m", messages=[]))
check("async_empty_retry", resp.choices[0].message.content == "ok" and CM.STATS["empty_retries"] == 1)
# 8) stream ends with leaked tool call -> extra chunk with tool_calls
reset(); f, w = wrapped([[chunk(reasoning="think " + LEAK, finish="stop")]], True)
async def sl():
    return [c async for c in await w.chat.completions.create(model="m", messages=[], tools=TOOLS, stream=True)]
cs = asyncio.run(sl())
check("stream_leaked_promoted", any(c.choices[0].delta.tool_calls for c in cs) and CM.STATS["promoted"] == 1)
# 9) cancel safety: cancelled waiter must not leak the gate
reset(); f, w = wrapped([comp("hi")], True, 0.3)
async def cancel():
    a = asyncio.create_task(w.chat.completions.create(model="m", messages=[]))
    await asyncio.sleep(0.05)
    b = asyncio.create_task(w.chat.completions.create(model="m", messages=[]))
    await asyncio.sleep(0.05); b.cancel()
    try: await b
    except asyncio.CancelledError: pass
    await a
    await asyncio.sleep(0.1)
asyncio.run(cancel())
got = CM.GATE.acquire(timeout=1.0); 
if got: CM.GATE.release()
check("cancelled_waiter_does_not_leak_gate", got and CM.STATS["inflight"] == 0, f"inflight={CM.STATS['inflight']}")
print(f"\n{sum(ok)}/{len(ok)} passed"); raise SystemExit(0 if all(ok) else 1)
