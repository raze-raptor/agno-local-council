import asyncio, json, threading, time, types
from openai.types.chat import ChatCompletion, ChatCompletionChunk

def comp(content="", reasoning=None, tool_calls=None, finish="stop"):
    m = {"role": "assistant", "content": content}
    if reasoning is not None: m["reasoning_content"] = reasoning
    if tool_calls: m["tool_calls"] = tool_calls
    return ChatCompletion.model_validate({"id": "x", "object": "chat.completion", "created": 1, "model": "m",
                                          "choices": [{"index": 0, "finish_reason": finish, "message": m}]})
def chunk(content=None, reasoning=None, finish=None):
    d = {}
    if content: d["content"] = content
    if reasoning: d["reasoning_content"] = reasoning
    return ChatCompletionChunk.model_validate({"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
                                               "choices": [{"index": 0, "delta": d, "finish_reason": finish}]})
TOOLS = [{"type": "function", "function": {"name": "get_time", "parameters": {"type": "object", "properties": {}, "required": []}}}]
LEAK = '<tool_call>\n{"name": "get_time", "arguments": {}}\n</tool_call>'

class Fake:
    """scripted upstream; tracks concurrent entries"""
    def __init__(self, script, delay=0.0):
        self.script, self.delay, self.n, self.live, self.maxlive, self.calls = list(script), delay, 0, 0, 0, []
        self.lock = threading.Lock()
        self.completions = types.SimpleNamespace(create=self.create)
        self.chat = types.SimpleNamespace(completions=self.completions)
    def _next(self):
        with self.lock:
            self.live += 1; self.maxlive = max(self.maxlive, self.live); self.n += 1
            return self.script.pop(0) if len(self.script) > 1 else self.script[0]
    def _done(self):
        with self.lock: self.live -= 1
    def is_closed(self): return False
    # sync + async + stream flavours selected by wrapper
    def create(self, **kw):
        if self.is_async: return self._acreate(**kw)
        self.calls.append(kw); item = self._next()
        try:
            time.sleep(self.delay)
            if kw.get("stream"): return iter(item)
            return item
        finally: self._done()
    async def _acreate(self, **kw):
        self.calls.append(kw); item = self._next()
        try:
            await asyncio.sleep(self.delay)
            if kw.get("stream"):
                async def g():
                    for c in item: yield c
                return g()
            return item
        finally: self._done()


