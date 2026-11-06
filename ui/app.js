"use strict";
/* Council UI. Talks only to its own origin (/api/*, /auth/*). The chat stream is the official AG-UI event protocol
   (RUN_*, STEP_*, TEXT_MESSAGE_*, TOOL_CALL_*, SUBAGENT_*, CUSTOM); every element of the activity view is derived from those events. */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { convs: [], id: null, runs: {}, messages: [], active: null, status: null, view: "chat", stick: true, ctrl: null };

/* ---------- api ---------- */
async function api(path, opt = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opt, headers: { "X-Council": "1", ...(opt.body ? { "Content-Type": "application/json" } : {}), ...(opt.headers || {}) } });
  if (r.status === 401 && !path.startsWith("/auth/")) { showLogin(); throw new Error("login required"); }
  return r;
}
const jget = async p => (await api(p)).json();

/* ---------- markdown (escape first, then format; links limited to http/https) ---------- */
function md(src) {
  const blocks = []; let t = String(src ?? "");
  t = t.replace(/```([\w+-]*)\n([\s\S]*?)(```|$)/g, (_, lang, code) => { blocks.push({ lang, code }); return `\u0000${blocks.length - 1}\u0000`; });
  const inline = s => esc(s).replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>").replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  const out = []; let list = null;
  const flush = () => { if (list) { out.push(`<${list.t}>${list.items.join("")}</${list.t}>`); list = null; } };
  for (const line of t.split("\n")) {
    let m;
    if ((m = line.match(/^\u0000(\d+)\u0000$/))) { flush(); out.push(m[0]); continue; }
    if ((m = line.match(/^(#{1,3})\s+(.*)/))) { flush(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (!list || list.t !== "ul") { flush(); list = { t: "ul", items: [] }; } list.items.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (!list || list.t !== "ol") { flush(); list = { t: "ol", items: [] }; } list.items.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^>\s?(.*)/))) { flush(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
    flush(); if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  flush();
  return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => {
    const b = blocks[+i];
    return `<div class="code"><div class="code-h"><span>${esc(b.lang || "code")}</span><button data-copy>Copy</button></div><pre><code>${esc(b.code.replace(/\n$/, ""))}</code></pre></div>`;
  });
}

/* ---------- AG-UI event reducer ---------- */
const t0of = r => r.t0 || (r.t0f = r.t0f || Date.now());          // a run's clock starts at its first event, or at the moment its card appeared
function newRun(id) { return { id, status: "running", t0: null, t1: null, chain: [], items: [], msgs: {}, tools: {}, subs: {}, error: null, verification: null, open: null, result: null }; }
function reduce(run, e) {
  const ts = e.timestamp || Date.now();
  const lastNode = () => run.chain[run.chain.length - 1];
  const closeOrch = st => { const n = lastNode(); if (n && n.kind === "orch" && n.state === "active") { n.state = st; n.t1 = ts; } };
  switch (e.type) {
    case "RUN_STARTED": run.t0 = run.t0 || ts; if (!run.chain.length) run.chain.push({ kind: "orch", name: "Orchestrator", state: "active", t0: ts }); break;
    case "STEP_STARTED":
      if (!e.subagentRunId) {
        const n = lastNode(); if (!n || n.kind !== "orch" || n.state !== "active") run.chain.push({ kind: "orch", name: "Orchestrator", state: "active", t0: ts });
        run.items.push({ k: "step", label: e.stepName, sub: null, t0: ts, live: true });
      } else run.items.push({ k: "step", label: e.stepName, sub: e.subagentRunId, t0: ts, live: true });
      break;
    case "STEP_FINISHED": { for (let i = run.items.length - 1; i >= 0; i--) { const it = run.items[i]; if (it.k === "step" && it.live && it.label === e.stepName && (it.sub || null) === (e.subagentRunId || null)) { it.live = false; it.t1 = ts; break; } } break; }
    case "SUBAGENT_STARTED":
      closeOrch("done");
      run.subs[e.subagentRunId] = { id: e.subagentRunId, name: e.name, task: e.description || "", parentTool: e.parentToolCallId, state: "active", t0: ts };
      run.chain.push({ kind: "sub", id: e.subagentRunId, name: e.name, state: "active", t0: ts });
      run.items.push({ k: "sub", id: e.subagentRunId });
      break;
    case "SUBAGENT_FINISHED": case "SUBAGENT_ERROR": {
      const s = run.subs[e.subagentRunId]; const ok = e.type === "SUBAGENT_FINISHED";
      if (s) { s.state = ok ? "done" : "error"; s.t1 = ts; s.result = e.result; s.error = e.message; }
      const n = run.chain.find(x => x.id === e.subagentRunId); if (n) { n.state = ok ? "done" : "error"; n.t1 = ts; }
      break;
    }
    case "TEXT_MESSAGE_START": run.msgs[e.messageId] = { sub: e.subagentRunId || null, name: e.name, text: "", done: false, t0: ts }; run.items.push({ k: "msg", id: e.messageId, sub: e.subagentRunId || null }); break;
    case "TEXT_MESSAGE_CONTENT": if (run.msgs[e.messageId]) run.msgs[e.messageId].text += e.delta; break;
    case "TEXT_MESSAGE_END": if (run.msgs[e.messageId]) { run.msgs[e.messageId].done = true; run.msgs[e.messageId].t1 = ts; } break;
    case "TOOL_CALL_START": run.tools[e.toolCallId] = { id: e.toolCallId, name: e.toolCallName, sub: e.subagentRunId || null, args: "", result: null, t0: ts }; run.items.push({ k: "tool", id: e.toolCallId, sub: e.subagentRunId || null }); break;
    case "TOOL_CALL_ARGS": if (run.tools[e.toolCallId]) run.tools[e.toolCallId].args += e.delta; break;
    case "TOOL_CALL_RESULT": if (run.tools[e.toolCallId]) { const t = run.tools[e.toolCallId]; t.result = e.content; t.t1 = ts; t.failed = /^ERROR:/.test(e.content || ""); } break;
    case "CUSTOM": if (e.name === "verification") run.verification = e.value; if (e.name === "agent" && run.chain[0]) run.chain[0].name = e.value; if (e.name === "rogue_offer") run.rogueOffer = e.value; if (e.name === "handoff_from") run.handoffFrom = e.value; if (e.name === "note") run.note = e.value || null; if (e.name === "grant") { (run.grants = run.grants || {})[e.value.id] = { ...e.value, state: "pending" }; } if (e.name === "grant_done" && run.grants && run.grants[e.value.id]) run.grants[e.value.id].state = e.value.state; if (e.name === "image") (run.shots = run.shots || []).push(e.value); break;
    case "RUN_FINISHED":
      run.t1 = ts; run.result = e.result;
      if (e.outcome && e.outcome.type === "cancelled") { run.status = "cancelled"; run.chain.forEach(n => { if (n.state === "active") { n.state = "cancelled"; n.t1 = ts; } }); }
      else { run.status = "done"; closeOrch("done"); run.chain.push({ kind: "you", name: "You", state: "done", t0: ts, t1: ts }); }
      break;
    case "RUN_ERROR":
      run.t1 = ts; run.status = "error"; run.error = { message: e.message, code: e.code };
      run.chain.forEach(n => { if (n.state === "active") { n.state = "error"; n.t1 = ts; } });
      break;
  }
}

/* ---------- pure helpers (kept free of the DOM so they can be tested) ---------- */
const fmt = ms => { if (ms == null) return ""; if (ms < 950) return "<1s"; if (ms < 9500) return (ms / 1000).toFixed(1) + "s"; const s = Math.round(ms / 1000); return s < 60 ? s + "s" : Math.floor(s / 60) + "m" + String(s % 60).padStart(2, "0") + "s"; };
const prettyArgs = a => { try { return JSON.stringify(JSON.parse(a), null, 2); } catch { return a; } };
function argOf(t, ...keys) {            // tool arguments stream in as partial JSON; fall back to a regex while they are incomplete
  try { const o = JSON.parse(t.args); for (const k of keys) if (o[k] != null) return String(o[k]); } catch {}
  for (const k of keys) { const m = (t.args || "").match(new RegExp('"' + k + '"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)')); if (m) return m[1]; }
  return "";
}
const short = (s, n) => { s = String(s || "").replace(/\s+/g, " ").trim(); if (s.length <= n) return s; const cut = s.slice(0, n); return cut.slice(0, Math.max(cut.lastIndexOf(" "), n * .6)) + "…"; };
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return short(u, 40); } };
const lowerFirst = s => /^[A-Z]{2,}/.test(s) ? s : s.charAt(0).toLowerCase() + s.slice(1);
function toolBrief(t) {
  if (t.name === "web_search") return argOf(t, "query");
  if (t.name === "web_fetch") return host(argOf(t, "url"));
  if (t.name === "run_check") return [argOf(t, "host"), argOf(t, "check")].filter(Boolean).join(" · ");
  if (t.name === "vault_search") return argOf(t, "query");
  if (t.name === "vault_read") return argOf(t, "note");
  if (/delegate/.test(t.name)) return argOf(t, "member_id");
  return "";
}
const SPEC = [[/orchestrator/i, "--c-orch"], [/engineer/i, "--c-eng"], [/critic/i, "--c-crit"], [/admin/i, "--c-sys"], [/research/i, "--c-res"], [/strateg/i, "--c-str"], [/rogue/i, "--c-rog"]];
function specVar(name) {
  for (const [rx, v] of SPEC) if (rx.test(name)) return v;
  if (name === "You") return "--ok";
  let h = 0; for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return ["--c-eng", "--c-crit", "--c-sys", "--c-res"][h % 4];
}
const shortName = n => /engineer/i.test(n) ? "Engineer" : /critic/i.test(n) ? "Critic" : /admin/i.test(n) ? "SysAdmin" : /research/i.test(n) ? "Researcher" : n;
const monogram = n => n === "You" ? "" : n === "Orchestrator" ? "O" : n.replace(/\(.*?\)/g, "").trim().split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
function busyNodes(run) {               // chain nodes that currently have an unfinished tool call
  const busy = new Set();
  Object.values(run.tools).filter(t => t.t1 == null).forEach(t => {
    const i = t.sub ? run.chain.findIndex(n => n.id === t.sub) : run.chain.map(n => n.kind === "orch" && n.state === "active").lastIndexOf(true);
    if (i >= 0) busy.add(i);
  });
  return busy;
}
function ambient(s) {
  if (!s) return "bad";
  const inf = s.inference || {}, pc = s.pc || {};
  if (pc.reachable === false) return "bad";
  if (pc.ai_mode === "OFF") return "off";                    // the PC is yours: the model is unloaded on purpose
  if (!inf.ok && (s.rogue || {}).ok) return "warn";             // Rogue's model is loaded on purpose: the council is offline, nothing is broken
  if (!inf.ok) return "bad";
  if (pc.ai_mode !== "ON" || pc.gaming || (s.blocked_because || []).some(b => !/outside/i.test(b))) return "warn";
  return "ok";
}
/* The "Let me check that" line: derived only from what the run is doing right now (its latest unfinished tool call). */
function statusLine(run) {
  if (run.status !== "running") return null;
  const items = run.items.filter(i => i.k !== "step"), last = items[items.length - 1];
  if (last && last.k === "msg" && !last.sub && run.msgs[last.id].text.trim()) return null;        // the answer is already streaming
  let t = null;
  for (let i = run.items.length - 1; i >= 0; i--) { const it = run.items[i]; if (it.k === "tool" && run.tools[it.id].t1 == null) { t = run.tools[it.id]; break; } }
  if (run.note && !run.items.some(i => i.k === "tool" || i.k === "sub")) return { html: run.note, since: t0of(run) };
  let since = t0of(run);
  [...Object.values(run.tools), ...Object.values(run.subs)].forEach(x => { if (x.t1 && x.t1 > since) since = x.t1; });
  if (!t) return { html: "Let me think about that…", since };
  since = t.t0;
  const sub = Object.values(run.subs).find(s => s.parentTool === t.id);
  let text;
  switch (t.name) {
    case "web_search": text = `Let me search the web for “${esc(short(argOf(t, "query"), 60))}”…`; break;
    case "web_fetch": text = `Reading ${esc(host(argOf(t, "url")) || "the page")}…`; break;
    case "run_check": text = `Checking ${esc(argOf(t, "host") || "the server")}…`; break;
    case "vault_search": case "vault_read": text = "Let me look that up in your notes…"; break;
    case "read_health_facts": text = "Checking the latest health facts…"; break;
    default:
      if (/delegate/.test(t.name)) {
        const who = sub ? shortName(sub.name) : shortName(argOf(t, "member_id"));
        const task = lowerFirst(short((sub && sub.task) || argOf(t, "task"), 70).replace(/^to\s+/i, ""));
        text = who ? `Let me ask the ${esc(who)}${task ? " to " + esc(task) : ""}…` : "Let me ask a specialist…";
      } else text = `Using ${esc(t.name)}…`;
  }
  return { html: text, since };
}
function groupConvs(convs, now = Date.now()) {
  const d0 = new Date(now); d0.setHours(0, 0, 0, 0); const mid = d0.getTime(), DAY = 86400000;
  const buckets = [["Today", mid], ["Yesterday", mid - DAY], ["Previous 7 days", mid - 7 * DAY], ["Older", -Infinity]];
  const out = buckets.map(([label]) => ({ label, items: [] }));
  convs.forEach(c => { const ms = c.updated * 1000; const i = buckets.findIndex(([, from]) => ms >= from); out[i].items.push(c); });
  return out.filter(g => g.items.length);
}
const answerOf = run => {
  const top = run.items.filter(i => !i.sub && (i.k === "msg" || i.k === "tool" || i.k === "sub")).pop();
  return top && top.k === "msg" ? top.id : null;
};
const answerText = run => { const id = answerOf(run); const m = id && run.msgs[id]; return m && m.text.trim() ? m.text : (run.status === "done" && run.result ? run.result : ""); };

/* ---------- rendering ---------- */
function renderMap(run) {
  const busy = busyNodes(run), shown = run.mapShown || 0, running = run.status === "running";
  const seen = {}, total = {}; run.chain.forEach(n => { total[n.name] = (total[n.name] || 0) + 1; });
  let h = "";
  run.chain.forEach((n, i) => {
    const st = n.state === "active" && !running ? "interrupted" : n.state, isNew = i >= shown ? " new" : "";
    const col = `var(${specVar(n.name)})`;
    if (i) h += `<li class="lnk ${st === "active" ? "on flow" : st === "done" ? "on" : st === "error" ? "err" : "dotted"}${isNew}" style="--c:${col}" aria-hidden="true"></li>`;
    seen[n.name] = (seen[n.name] || 0) + 1;
    const nm = shortName(n.name) + (n.kind === "sub" && total[n.name] > 1 ? " " + seen[n.name] : "");
    const bdg = st === "done" ? "✓" : st === "error" ? "✕" : st === "cancelled" ? "■" : "";
    const t = n.kind === "you" ? "" : st === "active" ? `<span class="t" data-t0="${n.t0}"></span>` : n.t1 ? `<span class="t">${fmt(n.t1 - n.t0)}</span>` : `<span class="t"></span>`;
    h += `<li class="seat ${st}${busy.has(i) && st === "active" ? " busy" : ""}${n.kind === "you" ? " you" : ""}${isNew}" style="--c:${col}" title="${esc(n.name)}"><span class="disc"><b>${esc(monogram(n.name))}</b><i class="bdg">${bdg}</i></span><span class="nm">${esc(nm)}</span>${t}<span class="vh">${st === "active" ? " working" : st === "done" ? " finished" : st === "error" ? " failed" : st === "cancelled" ? " stopped" : ""}</span></li>`;
  });
  if (running && run.chain.length) h += `<li class="lnk dotted" aria-hidden="true"></li><li class="seat ghost you" aria-hidden="true"><span class="disc"><b></b></span><span class="nm">You</span><span class="t"></span></li>`;
  if (!running && run.t1 && run.t0) h += `<li class="map-sum">${fmt(run.t1 - run.t0)}</li>`;
  return h;
}

function renderTool(t) {
  const state = t.result == null ? "live" : t.failed ? "bad" : "ok";
  const br = toolBrief(t);
  const res = t.result == null ? "" : `<div class="lab">Result</div><pre>${esc(t.result)}</pre>`;
  const key = "t:" + t.id;
  return `<div class="tool" data-key="${key}"><button class="tool-h ${t.failed ? "bad" : ""}" data-toggle="${key}"><span class="nm">${esc(t.name)}</span><span class="br">${esc(br)}</span><span class="res">${t.t1 ? fmt(t.t1 - t.t0) : "running"}<i class="gl ${state === "live" ? "" : state}"></i></span></button>` +
    `<div class="tool-b"><div class="tool-in"><div class="lab">Arguments</div><pre>${esc(prettyArgs(t.args))}</pre>${res}</div></div></div>`;
}

function renderTrail(run) {
  const bySub = {}, top = [];
  run.items.forEach(it => { if (it.sub) (bySub[it.sub] = bySub[it.sub] || []).push(it); else top.push(it); });
  const delegateTools = new Set(Object.values(run.subs).map(s => s.parentTool));
  const answerId = answerOf(run);
  const inner = (its, withSteps) => its.map(it => {
    if (it.k === "step") return withSteps ? `<div class="ev ${it.live ? "spin" : ""}"><span class="ic"></span><span>${esc(it.label)}${it.t1 ? ` <span class="muted">${fmt(it.t1 - it.t0)}</span>` : ""}</span></div>` : "";
    if (it.k === "tool") return delegateTools.has(it.id) && !run.tools[it.id].failed ? "" : renderTool(run.tools[it.id]);
    if (it.k === "msg") { const m = run.msgs[it.id]; if (it.id === answerId || !m.text.trim()) return ""; return `<div class="subtext">${md(m.text)}</div>`; }
    if (it.k === "sub") {
      const s = run.subs[it.id], st = s.state === "active" ? "live" : s.state === "error" ? "err" : "";
      const meta = s.state === "active" ? "working…" : s.state === "error" ? "failed" : "finished " + fmt(s.t1 - s.t0);
      return `<div class="sub ${st}" style="--c:var(${specVar(s.name)})"><div class="sub-h">${esc(s.name)}<span class="muted">${meta}</span></div>${s.task ? `<div class="sub-task">Task: ${esc(s.task)}</div>` : ""}${inner(bySub[it.id] || [], true)}${s.error ? `<div class="errcard"><b>${esc(s.name)} failed:</b> ${esc(s.error)}</div>` : ""}</div>`;
    }
    return "";
  }).join("");
  const nTools = Object.keys(run.tools).length, nSubs = Object.keys(run.subs).length, running = run.status === "running";
  const meta = running ? "" : [nSubs ? `${nSubs} specialist${nSubs > 1 ? "s" : ""}` : "", nTools - nSubs > 0 ? `${nTools - nSubs} tool call${nTools - nSubs > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · ");
  const label = running ? `Working<span class="muted" data-t0="${t0of(run)}"></span>` : `Worked${run.t1 && run.t0 ? " for " + fmt(run.t1 - run.t0) : ""}`;
  return { body: inner(top, true) || '<div class="ev spin"><span class="ic"></span><span>Starting…</span></div>', head: `<svg><use href="#i-chev"/></svg><b>${label}</b>${meta ? `<span class="muted">${esc(meta)}</span>` : ""}`, any: nTools > 0 || nSubs > 0 };
}

const FILEASK = /(\bfile\b|\.md\b|markdown|\bword\b|\.docx|docx|text file|\.txt|\bdocument\b|\bdownload|\bexport\b|\bsave (it|this|that)\b)/i;
const fileName = run => { const t = answerText(run), h = (t.match(/^#{1,3}\s+(.+)$/m) || [])[1] || (S.messages.find(x => x.role === "user" && x.run_id === run.id) || {}).content || "council-answer"; return (h.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "council-answer") + "-" + new Date().toISOString().slice(0, 10); };
function saveBlob(blob, name) { const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); }
function renderFoot(run) {
  let f = "";
  if (run.shots && run.shots.length) {
    f += `<div class="shots" style="flex-basis:100%">` + run.shots.map(s => `<a class="shot" href="${esc(s.url)}" target="_blank" rel="noopener" title="${esc(s.label || "Screenshot")}"><img src="${esc(s.url)}" alt="${esc(s.label || "Screenshot")}" loading="lazy"></a>`).join("") + `</div>`;
  }
  for (const g of Object.values(run.grants || {})) {
    if (g.state === "pending") f += `<div class="offer grant${g.kind === "risky" ? " risky" : ""}" style="flex-basis:100%" role="group" aria-label="Approval needed"><div><b>${g.kind === "risky" ? "This one needs a yes from you." : "The Screen Operator needs your OK."}</b> ${esc(g.summary)}<span class="muted">${g.once ? "It asks every time for steps like this." : "Allow for this task and it will not ask again until the task ends."}</span></div><span class="btns">${g.once ? "" : `<button class="btn-ink" data-grant="${esc(g.id)}" data-d="allow_task">Allow for this task</button>`}<button class="btn-line" data-grant="${esc(g.id)}" data-d="allow">Allow once</button><button class="btn-line" data-grant="${esc(g.id)}" data-d="deny">Deny</button></span></div>`;
    else f += `<div class="stopped" style="flex-basis:100%">${g.state === "deny" ? "You denied" : g.state === "expired" ? "No answer, so skipped" : "You allowed"}: ${esc(g.summary)}</div>`;
  }
  if (run.status === "error") f += `<div class="errcard" style="flex-basis:100%"><b>The run failed.</b> ${esc(run.error?.message || "")} <span class="muted">(${esc(run.error?.code || "error")})</span></div>`;
  if (run.status === "cancelled") f += `<div class="stopped" style="flex-basis:100%">Stopped. Whatever was already produced is kept above.</div>`;
  if (run.status === "interrupted") f += `<div class="stopped" style="flex-basis:100%">The connection ended before the council finished. It may still be working; this view refreshes when it is done.</div>`;
  if (run.status === "done" && answerText(run)) f += `<button class="tool-btn" data-copyans aria-label="Copy answer"><svg><use href="#i-copy"/></svg>Copy</button>`;
  if (run.status === "done" && answerText(run)) {
    const um = S.messages.find(x => x.role === "user" && x.run_id === run.id), want = um && FILEASK.test(um.content || um.text || "");
    f += `<span class="savegrp" role="group" aria-label="Save this answer"><button class="tool-btn" data-save="md" aria-label="Save as Markdown file">.md</button><button class="tool-btn" data-save="txt" aria-label="Save as text file">.txt</button><button class="tool-btn" data-save="docx" aria-label="Save as Word document">Word</button></span>`;
    if (want) f += `<div class="offer grant" style="flex-basis:100%"><div><b>Your file is ready.</b> <code>${esc(fileName(run))}.md</code><span class="muted">Also available as a text file or a Word document with the buttons beside Copy.</span></div><span class="btns"><button class="btn-ink" data-save="md">Download .md</button><button class="btn-line" data-save="docx">Download Word</button></span></div>`;
  }
  if (run.rogueOffer && run.status === "done" && !Object.values(S.runs).some(r => r.handoffFrom === run.id)) f += `<div class="offer" style="flex-basis:100%"><div><b>The council declined this.</b> Rogue's own team can try it on its unfiltered model.<span class="muted"> It switches models (about a minute), the council is offline meanwhile, and it switches back by itself.</span></div><button class="btn-line" data-rogue-offer="${esc(run.id)}">Send to Rogue's team</button></div>`;
  if (run.handoffFrom) f += `<span class="badge warn">Rogue's team answered after the council declined</span>`;
  if (run.verification) {
    const v = run.verification, bad = v.warnings && v.warnings.length;
    f += `<span class="badge ${bad ? "warn" : "ok"}">Sources: ${v.fetched} fetched · ${v.domains.length} domain${v.domains.length === 1 ? "" : "s"}</span>${(v.warnings || []).map(w => `<span class="badge warn">${esc(w)}</span>`).join("")}`;
  }
  return f;
}

const RUN_SKELETON = `<div class="run-status" data-r="status" role="status" aria-live="polite" hidden></div><ol class="map" data-r="map" data-toggle="trail" aria-label="Council progress" hidden></ol><ol class="feed" data-r="feed" role="log" aria-label="What the council is doing" hidden></ol>` +
  `<div class="trail" data-r="trail" hidden><button class="trail-h" data-toggle="trail" data-r="trailh"></button><div class="trail-b"><div class="trail-in" data-r="trailb"></div></div></div><div class="answer" data-r="answer" hidden></div><div class="run-foot" data-r="foot" hidden></div>`;
const phone = () => matchMedia("(max-width: 640px)").matches;

/* write a region only when its HTML actually changed, so CSS animations and open/close transitions are never restarted by streaming deltas */
function setHTML(el, html) { if (el.__h !== html) { el.__h = html; el.innerHTML = html; return true; } return false; }

function patchRun(el, run) {
  const r = k => el.querySelector(`[data-r=${k}]`), running = run.status === "running";
  el.classList.toggle("live", running);
  const feedEl = r("feed"), fl = running ? renderFeed(run) : "";
  feedEl.hidden = !fl; if (fl) setHTML(feedEl, fl); else if (feedEl.__h) setHTML(feedEl, "");
  const sl = running ? null : statusLine(run), st = r("status");               // the feed is the status while a run is going
  st.hidden = !sl; if (sl) setHTML(st, `<span class="sl-text">${sl.html}</span><span class="sl-t" data-t0="${sl.since}"></span>`);
  const map = r("map"), hasSub = Object.keys(run.subs).length > 0, showMap = run.chain.length > 0 && (running || hasSub);
  map.hidden = !showMap; if (showMap) { setHTML(map, renderMap(run)); run.mapShown = run.chain.length; }
  const tr = renderTrail(run), trail = r("trail"), showTrail = running || tr.any;
  trail.hidden = !showTrail;
  if (showTrail) {
    setHTML(r("trailh"), tr.head); setHTML(r("trailb"), tr.body);
    const open = run.open && run.open.trail !== undefined ? run.open.trail : false;      // the feed above shows the steps live; the trail holds the details on demand
    trail.classList.toggle("open", !!open);
    trail.querySelectorAll(".tool").forEach(t => t.classList.toggle("open", !!(run.open && run.open[t.dataset.key])));
  }
  const ans = r("answer"), id = answerOf(run), a = id ? run.msgs[id] : null;
  const txt = a && a.text.trim() ? a.text : (run.status === "done" && run.result ? run.result : "");
  ans.hidden = !txt; if (txt) { setHTML(ans, md(txt)); ans.classList.toggle("cursor", running && !!a && !a.done); }
  const foot = r("foot"), fh = renderFoot(run); foot.hidden = !fh; setHTML(foot, fh);
}

/* ---------- the live feed: what the council is doing, line by line, like a coding agent reports it (built only from real events) ---------- */
const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u ? String(u).slice(0, 40) : "a page"; } };
const q = s => s ? "\u201c" + (s.length > 60 ? s.slice(0, 57) + "\u2026" : s) + "\u201d" : "";
const FEED_TOOL = {
  browser_new_tab: t => "Opening " + hostOf(argOf(t, "url")) + " in Brave",
  browser_navigate: t => "Going to " + hostOf(argOf(t, "url")),
  browser_snapshot: () => "Looking at the page",
  browser_read_page: () => "Reading the page",
  browser_click: () => "Clicking on the page",
  browser_fill: () => "Filling in a field",
  browser_press: t => "Pressing " + (argOf(t, "key") || "Enter"),
  browser_scroll: () => "Scrolling the page",
  browser_list_tabs: () => "Checking the open tabs",
  pc_system_info: () => "Reading the PC's status",
  pc_list_windows: () => "Looking at the open windows",
  check_python_syntax: () => "Testing the code",
  web_research: t => "Researching " + (q(argOf(t, "query")) || "the question"),
  web_search: t => "Searching the web for " + (q(argOf(t, "query")) || "it"),
  web_fetch: t => "Reading " + hostOf(argOf(t, "url")),
  vault_search: t => "Looking through your notes for " + (q(argOf(t, "query")) || "it"),
  vault_read: t => "Reading the note " + (q(argOf(t, "note")) || ""),
  read_health_facts: () => "Reading the latest server health facts",
  list_checks: () => "Looking at which checks are available",
  run_check: t => "Running the " + (argOf(t, "check") || "") + " check on " + (argOf(t, "host") || "the server"),
  propose_change: () => "Writing up a proposed change for you",
  get_time: () => "Checking the time",
  get_skill_instructions: t => "Opening the " + (argOf(t, "skill_name") || "skill") + " guide",
};
const WRITES = { Engineer: "the code", Critic: "the review", SysAdmin: "the report", Researcher: "the findings", Strategist: "the recommendation" };
function feedLines(run) {
  const L = [], now = Date.now(); let open = null; const rt0 = t0of(run);
  const closeSub = () => { if (open && open.state !== "active") L.push({ text: shortName(open.name) + " finished", t0: open.t0, t1: open.t1, d: 0, fin: true, failed: open.state === "error" }); open = null; };
  const lowerFirst = s => s.charAt(0).toLowerCase() + s.slice(1);
  if (run.note) L.push({ text: run.note, t0: rt0, t1: null, d: 0 });
  for (const it of run.items) {
    if (it.k === "step") continue;
    if (open && (it.sub || null) !== open.id) closeSub();
    if (it.k === "tool") {
      const t = run.tools[it.id]; if (!t || /delegate/.test(t.name)) continue;
      L.push({ text: (FEED_TOOL[t.name] || (() => "Using " + t.name.replace(/_/g, " ")))(t), t0: t.t0, t1: t.t1, d: it.sub ? 1 : 0, failed: t.failed });
    } else if (it.k === "sub") {
      const s = run.subs[it.id]; if (!s) continue; const who = shortName(s.name);
      const task = s.task ? lowerFirst(s.task.replace(/\s+/g, " ").replace(/[.\s]+$/, "")) : "take this";
      L.push({ text: "Asked the " + who + " to " + (task.length > 90 ? task.slice(0, 87) + "\u2026" : task), t0: s.t0, t1: s.t0, d: 0, instant: true });
      open = s;
    } else if (it.k === "msg") {
      const m = run.msgs[it.id]; if (!m || !m.text.trim()) continue;
      if (it.sub) { const s = run.subs[it.sub], who = s ? shortName(s.name) : "Specialist"; L.push({ text: "Writing " + (WRITES[who] || "its answer"), t0: m.t0 || (s && s.t0) || now, t1: m.done ? (m.t1 || now) : null, d: 1 }); }
      else L.push({ text: "Writing the answer", t0: m.t0 || now, t1: m.done ? (m.t1 || now) : null, d: 0 });
    }
  }
  closeSub();
  if (run.status === "running" && !L.some(x => x.t1 == null)) {
    const last = L.reduce((m, x) => Math.max(m, x.fin ? 0 : (x.t1 || x.t0 || 0)), rt0), sub = Object.values(run.subs).find(s => s.state === "active");
    L.push({ text: sub ? shortName(sub.name) + " is thinking it through" : "Thinking it through", t0: last, t1: null, d: sub ? 1 : 0 });
  }
  return L;
}
function renderFeed(run) {
  const L = feedLines(run), shown = L.slice(-12), hidden = L.length - shown.length;
  return (hidden > 0 ? `<li class="more">${hidden} earlier step${hidden === 1 ? "" : "s"}</li>` : "") + shown.map(x => {
    const live = x.t1 == null;
    return `<li class="${live ? "live" : x.failed ? "bad" : "done"} d${x.d}"><span class="fi" aria-hidden="true">${live ? "" : x.failed ? "\u00d7" : "\u2713"}</span><span class="ft">${esc(x.text)}${live ? "\u2026" : ""}</span><span class="fd"${live ? ` data-t0="${x.t0}"` : ""}>${live || x.instant ? "" : fmt(Math.max(0, x.t1 - x.t0))}</span></li>`;
  }).join("");
}
function runShell(run) { return `<div class="run" data-run="${run.id}">${RUN_SKELETON}</div>`; }
function runEl(id) { return document.querySelector(`[data-run="${id}"]`); }
function paintRun(run) { const el = runEl(run.id); if (el) patchRun(el, run); autoscroll(); }
let paintQueued = false;
function schedulePaint() { if (paintQueued) return; paintQueued = true; requestAnimationFrame(() => { paintQueued = false; if (S.active) paintRun(S.active); }); }

const SUGGEST = [["Check my servers", "Check whether anything is wrong on server1 or on server2 right now."],
  ["Engineer writes, Critic reviews", "Ask the Engineer to write a Python function that checks whether a string is a palindrome, then have the Critic review it."],
  ["Research with two sources", "Research what the OpenSSH restrict option does in authorized_keys and cite two sources."]];
function readyLine() {
  const s = S.status; if (!s) return "Connecting to the council…";
  const inf = s.inference || {}, pc = s.pc || {};
  if (pc.ai_mode === "OFF" && !inf.ok) return "AI Mode is off, so the model is unloaded and the PC is yours. Turn AI Mode on (Machine tab, or the tray) to load it and chat.";
  return inf.ok ? `Orchestrator ready · Mellum ${Math.round((inf.context || 0) / 1024)}K · AI Mode ${pc.ai_mode || "?"}` : `The model is not available right now (${inf.reason || "unknown"}). Messages will fail until it is loaded.`;
}
function renderThread() {
  const th = $("#thread");
  if (!S.messages.length) {
    th.innerHTML = `<div class="empty"><span class="mark" aria-hidden="true"></span><h1>What should the council work on?</h1><p id="readyLine">${esc(readyLine())}</p></div>`;
  } else {
    th.innerHTML = S.messages.map(m => m.role === "user" ? `<div class="msg user">${esc(m.content)}</div>` : (S.runs[m.run_id] ? runShell(S.runs[m.run_id]) : "")).join("");
    Object.values(S.runs).forEach(run => { const el = runEl(run.id); if (el) { run.mapShown = run.chain.length; patchRun(el, run); } });
  }
  $("#sugg").innerHTML = S.messages.length ? "" : SUGGEST.map(([l, p]) => `<button type="button" data-eg="${esc(p)}">${esc(l)}</button>`).join("");
}

/* ---------- scrolling ---------- */
function autoscroll() { const s = $("#scroller"); if (S.stick) s.scrollTop = s.scrollHeight; $("#jump").hidden = S.stick; }
$("#scroller").addEventListener("scroll", () => { const s = $("#scroller"); S.stick = s.scrollHeight - s.scrollTop - s.clientHeight < 80; $("#jump").hidden = S.stick; });
$("#jump").onclick = () => { S.stick = true; autoscroll(); };

/* ---------- conversations ---------- */
const newId = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "").slice(0, 16) : Math.random().toString(16).slice(2, 18));
async function loadList() {
  try { S.convs = (await jget("/api/conversations")).conversations; } catch { return; }
  const box = $("#convList");
  const row = c => `<div class="conv ${c.id === S.id ? "active" : ""}" role="listitem" title="${esc(new Date(c.updated * 1000).toLocaleString())}"><button class="conv-open" data-id="${c.id}">${c.last_status === "running" ? '<span class="pulse"></span>' : ""}<span class="t">${esc(c.title)}</span></button><button class="x" data-del="${c.id}" aria-label="Delete conversation"><svg><use href="#i-trash"/></svg></button></div>`;
  box.innerHTML = groupConvs(S.convs).map(g => `<div class="side-label">${g.label}</div>${g.items.map(row).join("")}`).join("") || '<div class="empty-list">No conversations yet.</div>';
}
async function openConv(id) {
  closeSide(); S.id = id; S.runs = {}; S.messages = []; S.active = null; S.pending = null; setView("chat"); renderMode();
  history.replaceState(null, "", "#" + id);
  const d = await api("/api/conversations/" + id);
  if (d.ok) {
    const j = await d.json(); S.messages = j.messages; $("#title").value = j.conversation.title;
    for (const [rid, r] of Object.entries(j.runs)) {
      const run = newRun(rid); r.events.forEach(e => reduce(run, e));
      if (r.status === "running") { run.status = "running"; S.pending = rid; }
      else if (run.status === "running") run.status = r.status === "interrupted" ? "interrupted" : "error";
      S.runs[rid] = run;
    }
    // user messages of runs that never produced an assistant message still need a run card
    const have = new Set(S.messages.filter(m => m.role === "assistant").map(m => m.run_id));
    S.messages.filter(m => m.role === "user" && !have.has(m.run_id) && S.runs[m.run_id]).forEach(m => { const i = S.messages.indexOf(m); S.messages.splice(i + 1, 0, { role: "assistant", run_id: m.run_id }); });
  } else { $("#title").value = "New chat"; }
  renderThread(); S.stick = true; autoscroll(); loadList(); setBusy(!!S.pending);
  if (S.pending) resumeRun(S.pending, id);
}
async function pollPending(id) {
  setBanner("A run in this conversation is still working on the server…", "warn");
  for (let i = 0; i < 400 && S.id === id; i++) {
    await new Promise(r => setTimeout(r, 3000));
    const j = await (await api("/api/conversations/" + id)).json().catch(() => null); if (!j) continue;
    const r = Object.values(j.runs).find(x => x.status === "running"); if (!r) { S.pending = null; setBanner(""); return openConv(id); }
  }
}
function newConversation() { closeSide(); S.id = newId(); S.runs = {}; S.messages = []; S.active = null; S.pending = null; history.replaceState(null, "", "#" + S.id); $("#title").value = "New chat"; setView("chat"); renderThread(); setBusy(false); setBanner(""); loadList(); $("#input").focus(); renderMode(); }

/* ---------- Rogue mode toggle (per conversation: the same roles, unfiltered, nothing executes) ---------- */
S.rogueConvs = S.rogueConvs || new Set();
function renderMode() {
  const on = S.id && S.rogueConvs.has(S.id);
  const btn = $("#cmMode"), lab = $("#cmModeLabel");
  if (btn) btn.classList.toggle("on", on);
  if (lab) lab.textContent = on ? "Rogue (unfiltered)" : "Council";
}
function toggleMode() {
  if (!S.id) S.id = newId();
  if (S.rogueConvs.has(S.id)) S.rogueConvs.delete(S.id); else S.rogueConvs.add(S.id);
  renderMode();
}
document.addEventListener("click", e => { if (e.target.closest("#cmMode")) toggleMode(); });

/* ---------- sending / streaming ---------- */
function syncSend() { $("#send").disabled = !$("#input").value.trim(); }
function buzz(ms = 8) { try { if (!matchMedia("(prefers-reduced-motion: reduce)").matches && navigator.vibrate) navigator.vibrate(ms); } catch {} }
function setBusy(b) { S.busy = b; $("#send").hidden = b; $("#stop").hidden = !b; $("#stop").disabled = false; $("#thread").setAttribute("aria-busy", b ? "true" : "false"); syncSend(); }
function setBanner(msg, kind = "bad") { const b = $("#banner"); b.hidden = !msg; b.className = "banner " + kind; b.textContent = msg || ""; }


/* One SSE response -> reducer. Returns true when the run reached its end. Shared by the first send and by every re-attach. */
async function readSSE(r, run) {
  const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "", terminal = false;
  for (;;) {
    const { value, done } = await rd.read(); if (done) break;
    buf += dec.decode(value, { stream: true }); let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      for (const ln of chunk.split("\n")) if (ln.startsWith("data:")) {
        let e; try { e = JSON.parse(ln.slice(5)); } catch { continue; }
        reduce(run, e); if (e.type === "RUN_FINISHED" || e.type === "RUN_ERROR") terminal = true; schedulePaint();
      }
    }
  }
  return terminal;
}
/* Re-attach to a run that is still going on the server (page refresh, tab switch, a phone dropping the connection). The server replays every event
   from the start and then follows the run live, so the run card is rebuilt from scratch each time and can never show an event twice. */
async function resumeRun(runId, convId) {
  const run = S.runs[runId] || (S.runs[runId] = newRun(runId)); Object.assign(run, newRun(runId)); S.active = run; S.pending = runId;
  setBusy(true); setBanner("Reconnected: the council is still working on this.", "warn"); renderThread(); autoscroll();
  for (let t = 0; t < 8 && S.id === convId; t++) {
    S.ctrl = new AbortController();
    try {
      const r = await api(`/api/runs/${runId}/events/0`, { signal: S.ctrl.signal });
      if (!r.ok) throw new Error("status " + r.status);
      Object.assign(run, newRun(runId));
      if (await readSSE(r, run)) { setBanner(""); break; }
    } catch (err) { if (err.name === "AbortError") return; }
    await new Promise(r => setTimeout(r, 1200 * (t + 1)));
  }
  if (S.id !== convId) return;
  if (run.status === "running") { run.status = "interrupted"; paintRun(run); setBusy(false); S.active = null; pollPending(convId); return; }
  paintRun(run); setBusy(false); S.active = null; S.ctrl = null; S.pending = null; setBanner(""); loadList(); pollStatus();
  openConv(convId);                                   // pick up the saved messages of the finished run
}
document.addEventListener("visibilitychange", () => {   // coming back to the tab: if the stream was dropped while hidden, re-attach
  if (document.hidden || !S.id) return;
  const live = S.active && S.ctrl && !S.ctrl.signal.aborted;
  if (S.pending && !S.active && !S.busy) resumeRun(S.pending, S.id);
  else if (S.active && S.active.status === "running" && S.streamDead) { S.streamDead = false; resumeRun(S.active.id, S.id); }
});

async function send(text, opts = {}) {
  text = text.trim(); if (!text || S.busy) return; buzz();
  if (!S.id) S.id = newId();
  const runId = newId() + newId();
  const run = newRun(runId); S.runs[runId] = run; S.active = run;
  S.messages.push({ role: "user", content: opts.handoff ? "Sent to Rogue's team: " + text : text, run_id: runId }, { role: "assistant", run_id: runId });
  renderThread(); S.stick = true; autoscroll(); setBusy(true); setBanner(""); $("#input").value = ""; autosize();
  S.ctrl = new AbortController();
  const body = { threadId: S.id, runId, state: {}, messages: [{ id: newId(), role: "user", content: text }], tools: [], context: [], forwardedProps: opts.handoff ? { agent: "rogue_team", handoff_run: opts.handoff } : { agent: S.rogueConvs.has(S.id) ? "rogue_direct" : "council" } };
  let terminal = false;
  try {
    const r = await api("/api/agui", { method: "POST", body: JSON.stringify(body), signal: S.ctrl.signal });
    if (!r.ok) throw new Error("The council returned " + r.status);
    terminal = await readSSE(r, run);
  } catch (err) {
    if (err.name !== "AbortError" && err.message === "login required") { run.status = "interrupted"; }   // anything else: the re-attach below picks the run up
  }
  if (!terminal && run.status === "running" && !(S.ctrl && S.ctrl.signal.aborted)) { if (document.hidden) { S.streamDead = true; return; } return resumeRun(runId, S.id); }
  if (!terminal && run.status === "running") { run.status = "interrupted"; }
  paintRun(run); setBusy(false); S.active = null; S.ctrl = null; loadList(); pollStatus();
  if (run.status === "interrupted") { S.pending = runId; pollPending(S.id); }
}
async function stop() { if (!S.active) return; buzz(12); $("#stop").disabled = true; try { await api("/api/agui/cancel", { method: "POST", body: JSON.stringify({ runId: S.active.id }) }); } catch {} setTimeout(() => { $("#stop").disabled = false; }, 800); }

/* ---------- status pill + popover ---------- */
function statusLabel(s) {
  const inf = s.inference || {}, pc = s.pc || {};
  if (pc.reachable === false) return "PC unreachable"; if (pc.gaming) return "Gaming";
  if (pc.ai_mode === "OFF") return "AI Mode off"; if (!inf.ok && (s.rogue || {}).ok) return "Rogue model loaded"; if (!inf.ok) return "Model down"; return "Ready";
}
async function pollStatus() {
  try {
    const s = await jget("/api/status"); S.status = s;
    $("#connDot").className = "dot ok"; $("#connText").textContent = "Council connected";
    document.body.dataset.ambient = ambient(s); $("#statusLabel").textContent = statusLabel(s);
    const inf = s.inference || {}, pc = s.pc || {}, blocked = s.blocked_because || [], w = s.window || {};
    const row = (cls, k, v) => `<div class="row"><i class="dot ${cls}"></i><b>${k}</b><span>${esc(v)}</span></div>`;
    $("#statusPop").innerHTML =
      row(inf.ok ? "ok" : "bad", "Model", inf.ok ? `${s.model || "loaded"} · ${Math.round((inf.context || 0) / 1024)}K context` : `unavailable (${inf.reason || "unknown"})`) +
      row(pc.reachable === false ? "bad" : pc.ai_mode === "ON" ? "ok" : "warn", "AI Mode", pc.reachable === false ? "PC unreachable" : `${pc.ai_mode || "?"}${pc.note ? " · " + pc.note : ""}`) +
      row(pc.gaming ? "warn" : "ok", "Gaming", pc.gaming ? `Playing ${pc.game || "a game"}; overnight work is paused` : "Not gaming") +
      row(s.unattended_work_allowed ? "ok" : "", "Overnight work", s.unattended_work_allowed ? `Allowed now (${w.now_london || ""})` : blocked.join("; ") || "Blocked") +
      row("ok", "Connection", "Council connected");
    const rl = $("#readyLine"); if (rl) rl.textContent = readyLine();
  } catch {
    $("#connDot").className = "dot bad"; $("#connText").textContent = "Council unreachable"; document.body.dataset.ambient = "bad"; $("#statusLabel").textContent = "Unreachable";
    $("#statusPop").innerHTML = '<div class="row"><i class="dot bad"></i><b>Connection</b><span>The council service cannot be reached.</span></div>';
  }
  try { S.bridge = await jget("/api/bridge"); } catch { S.bridge = null; }
  renderComposerMeta();
}

/* ---------- composer meta: real permission state (Screen Operator bridge) + an honest per-turn context estimate ----------
   The council only ever sends the system prompt plus the last couple of turns (num_history_runs=2), never the whole chat,
   so this is NOT a cumulative "chat so far" meter like Claude's — it is what THIS next turn will roughly cost. */
const SYS_CHARS_EST = 2600;                 // rough: Orchestrator instructions + datetime/history scaffolding Agno adds
function ctxEstimateChars() {
  const draft = $("#input") ? $("#input").value.length : 0;
  const msgs = S.messages || [];
  const recent = msgs.slice(-4);            // ~last 2 user/assistant turns, matching num_history_runs=2
  const histChars = recent.reduce((n, m) => n + (m.content || m.text || "").length, 0);
  return SYS_CHARS_EST + histChars + draft;
}
function renderComposerMeta() {
  const b = S.bridge, armed = !!(b && b.armed);
  const permEl = $("#cmPerm"), labEl = $("#cmPermLabel");
  if (permEl) {
    permEl.classList.toggle("on", armed);
    labEl.textContent = !b ? "Browser & PC: …" : armed ? "Browser & PC: armed" : "Browser & PC: off";
  }
  const ctxTokens = ((S.status || {}).inference || {}).context || 16384;
  const pct = Math.min(100, Math.round((ctxEstimateChars() / 4 / ctxTokens) * 100));
  const ring = $("#cmRing"), wrap = $("#cmCtx"), lab = $("#cmCtxLabel");
  if (ring) { const C = 40.2; ring.setAttribute("stroke-dashoffset", (C * (1 - pct / 100)).toFixed(1)); }
  if (wrap) wrap.classList.toggle("high", pct >= 80);
  if (lab) lab.textContent = "~" + pct + "%";
}
document.addEventListener("click", e => { if (e.target.closest("#cmPerm")) setView("dash", "browser"); });
$("#input") && $("#input").addEventListener("input", renderComposerMeta);

/* ---------- agents view ---------- */
async function loadAgents() {
  const g = $("#agentList"); g.innerHTML = '<div class="muted">Loading…</div>';
  try { const a = (await jget("/api/agents")).agents; g.innerHTML = a.map(x => `<div class="agent" style="--c:var(${specVar(x.name)})"><h3><i></i>${esc(x.name)}<small>${esc(x.state)}</small></h3><div><p>${esc(x.role)}</p><div class="tools">${(x.tools || []).map(esc).join(", ") || "no tools"}</div></div></div>`).join(""); }
  catch { g.innerHTML = '<div class="errcard">Could not load the roster.</div>'; }
}

/* ---------- views / nav / sidebar ---------- */
function setView(v, section) {
  S.view = v; for (const k of ["chat", "dash"]) $("#view" + k[0].toUpperCase() + k.slice(1)).hidden = k !== v;
  document.querySelectorAll(".nav-i").forEach(b => b.classList.toggle("active", b.dataset.view === v && (v !== "dash" || b.dataset.section === section)));
  if (typeof dashLeave === "function") dashLeave();
  if (v === "dash") dashEnter(section);
  $("#title").hidden = v !== "chat"; document.querySelector(".top").classList.toggle("plain", v !== "chat");
  if (typeof syncTabs === "function") syncTabs();
}
let sideOpener = null;
function openSide() { sideOpener = document.activeElement; $("#side").classList.add("open"); $("#scrim").hidden = false; $("#main").inert = true; $("#newChat").focus(); }
function closeSide() { if (!$("#side").classList.contains("open")) return; $("#side").classList.remove("open"); $("#scrim").hidden = true; $("#main").inert = false; if (sideOpener && sideOpener.focus) sideOpener.focus(); }
$("#scrim").onclick = closeSide;
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSide(); });
if (window.visualViewport) {
  const vv = visualViewport, fit = () => { document.documentElement.style.setProperty("--app-h", vv.height + "px"); window.scrollTo(0, 0); if (S.stick) autoscroll(); };
  vv.addEventListener("resize", fit); vv.addEventListener("scroll", fit);
}

/* ---------- composer ---------- */
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 220) + "px"; syncSend(); }
$("#input").addEventListener("input", autosize);
$("#input").addEventListener("focus", () => { if (matchMedia("(pointer: coarse)").matches) setTimeout(() => { S.stick = true; autoscroll(); }, 250); });
$("#input").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); } });
$("#composer").addEventListener("submit", e => { e.preventDefault(); send($("#input").value); });
$("#stop").onclick = stop;
$("#newChat").onclick = newConversation; $("#newChatTop").onclick = newConversation;
$("#openSide").onclick = openSide; $("#closeSide").onclick = closeSide;
$("#logout").onclick = async () => { await api("/auth/logout", { method: "POST" }); showLogin(); };
$("#title").addEventListener("change", async e => { if (S.id && S.messages.length) { await api("/api/conversations/" + S.id, { method: "PATCH", body: JSON.stringify({ title: e.target.value }) }); loadList(); } });
$("#title").addEventListener("keydown", e => { if (e.key === "Enter") e.target.blur(); });

document.addEventListener("click", async e => {
  const t = e.target;
  const eg = t.closest("[data-eg]"); if (eg) { $("#input").value = eg.dataset.eg; autosize(); $("#input").focus(); return; }
  const del = t.closest("[data-del]"); if (del) { e.stopPropagation(); if (confirm("Delete this conversation?")) { await api("/api/conversations/" + del.dataset.del, { method: "DELETE" }); if (S.id === del.dataset.del) newConversation(); else loadList(); } return; }
  const cv = t.closest(".conv-open"); if (cv) return openConv(cv.dataset.id);
  const nav = t.closest(".nav-i"); if (nav) { closeSide(); return setView(nav.dataset.view, nav.dataset.section); }
  const cp = t.closest("[data-copy]"); if (cp) { const code = cp.closest(".code").querySelector("code").textContent; try { await navigator.clipboard.writeText(code); cp.textContent = "Copied"; setTimeout(() => cp.textContent = "Copy", 1200); } catch {} return; }
  const sv = t.closest("[data-save]"); if (sv) {
    const rn = S.runs[sv.closest("[data-run]").dataset.run], txt = answerText(rn), nm = fileName(rn), k = sv.dataset.save;
    if (k === "docx") { const old = sv.textContent; sv.textContent = "Preparing…"; try { const r = await api("/api/export", { method: "POST", body: JSON.stringify({ title: nm.replace(/-\d{4}-\d\d-\d\d$/, "").replace(/-/g, " "), markdown: txt }) }); const j = await r.json(); const bin = atob(j.b64), u8 = Uint8Array.from(bin, c => c.charCodeAt(0)); saveBlob(new Blob([u8], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }), nm + ".docx"); } catch {} sv.textContent = old; }
    else saveBlob(new Blob([txt], { type: k === "md" ? "text/markdown" : "text/plain" }), nm + "." + k);
    return;
  }
  const ca = t.closest("[data-copyans]"); if (ca) { const rn = S.runs[ca.closest("[data-run]").dataset.run]; try { await navigator.clipboard.writeText(answerText(rn)); ca.lastChild.textContent = "Copied"; setTimeout(() => ca.lastChild.textContent = "Copy", 1200); } catch {} return; }
  const tg = t.closest("[data-toggle]"); if (tg) {                 // toggles a class on the live element: no repaint, so the CSS transition runs
    const shell = tg.closest("[data-run]"), run = shell && S.runs[shell.dataset.run]; if (!run) return;
    run.open = run.open || {}; const key = tg.dataset.toggle;
    if (key === "trail") { const el = shell.querySelector(".trail"); if (el.hidden) return; run.open.trail = !el.classList.contains("open"); el.classList.toggle("open", run.open.trail); }
    else { run.open[key] = !run.open[key]; tg.closest(".tool").classList.toggle("open", run.open[key]); }
  }
});

/* live clocks, from the real event times: status line, active seat, and the trail header */
setInterval(() => document.querySelectorAll(".run.live [data-t0]").forEach(el => {
  const t0 = +el.dataset.t0; if (!isFinite(t0) || t0 <= 0) { el.textContent = ""; return; }          // never print NaN
  const ms = Math.max(0, Date.now() - t0);
  el.textContent = el.classList.contains("sl-t") || el.classList.contains("fd") ? (ms >= 2000 ? fmt(ms) : "") : el.closest(".trail-h") ? " · " + fmt(ms) : fmt(ms);
}), 500);

/* ---------- login ---------- */
function showLogin() { $("#app").hidden = true; $("#login").hidden = false; $("#pw").value = ""; $("#pw").focus(); }
$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault(); $("#loginErr").textContent = "";
  const r = await api("/auth/login", { method: "POST", body: JSON.stringify({ password: $("#pw").value }) });
  if (r.ok) return start(); const j = await r.json().catch(() => ({})); $("#loginErr").textContent = j.error || "Sign-in failed";
});
let started = false;
async function start() {
  $("#login").hidden = true; $("#app").hidden = false;
  if (!started) { started = true; setInterval(pollStatus, 10000); setInterval(() => { if (!S.busy) loadList(); }, 20000); }
  pollStatus(); await loadList();
  const h = location.hash.slice(1); if (h && /^[A-Za-z0-9_-]{1,40}$/.test(h)) openConv(h); else newConversation();
}
(async () => { const r = await fetch("/auth/me", { credentials: "same-origin" }); if (r.ok) start(); else showLogin(); })();

// real install (PWA) needs a secure origin; on plain HTTP the browser refuses the worker and we simply skip it
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});

/* ---------- Screen Operator approvals ---------- */
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-grant]"); if (!b) return;
  b.closest(".btns").querySelectorAll("button").forEach(x => x.disabled = true);
  await api("/api/grants/" + b.dataset.grant, { method: "POST", body: JSON.stringify({ decision: b.dataset.d }) });
});

/* ---------- Rogue's team: offered only after a decline ---------- */
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-rogue-offer]"); if (!b || S.busy) return;
  const rid = b.dataset.rogueOffer, m = S.messages.find(x => x.role === "user" && x.run_id === rid); if (!m) return;
  if (await ask("Send this to Rogue's team?", "<p>The council declined this request. Rogue's team runs on a different, unfiltered model, with read-only tools and nothing that executes.</p><p>The PC swaps models (about a minute), the council and the overnight jobs are offline until it finishes, and it swaps back by itself when Rogue's team has answered.</p>", "Send to Rogue's team", false)) send(m.content, { handoff: rid });
});
