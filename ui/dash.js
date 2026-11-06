"use strict";
/* Council dashboard sections: Tasks, Approvals, Schedule, AI Mode, Models, Memory, System. Every number, bar and chart is drawn from the real
   JSON of /api/<section>; nothing is invented. Shell + named regions: polls patch regions only when their HTML changed, so typed text and open rows survive. */
const D = { sub: {}, lastSec: {}, section: null, timer: null, data: {}, openJobs: new Set(), jobDetail: {}, cfg: null, cfgDirty: false };

/* ---------- pure helpers ---------- */
const clock = ts => ts ? new Date(ts * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
const ageOf = ts => { const s = Math.max(0, Date.now() / 1000 - ts); return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " d ago"; };
const num = (v, d = 0) => v == null || isNaN(v) ? "n/a" : Number(v).toLocaleString([], { maximumFractionDigits: d });
function meter(label, value, scale, text, ticks = [], flip = false) {   // ticks: [{at, label}] positions in the same unit as value
  const pct = v => Math.max(0, Math.min(100, v / scale * 100));
  return `<div class="meter"><div class="m-h"><span>${esc(label)}</span><b>${esc(text)}</b></div><div class="m-bar"><i style="width:${pct(value).toFixed(1)}%"></i>${ticks.map(t => `<u style="left:${pct(t.at).toFixed(1)}%" title="${esc(t.label)}"></u>`).join("")}</div>` +
    `<div class="m-t">${ticks.map(t => `<span style="left:${pct(t.at).toFixed(1)}%" class="${pct(t.at) > 82 ? "r" : pct(t.at) < 8 ? "l" : ""}">${esc(t.label)}</span>`).join("")}</div></div>`;
}
function line(points, { w = 360, h = 70, min = null, max = null, unit = "", ref = null } = {}) {   // points: [[t, v]]
  const pts = points.filter(p => p[1] != null && !isNaN(p[1]));
  if (pts.length < 2) return `<div class="empty-chart">Collecting… (one point per minute since the service started)</div>`;
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
  let lo = min ?? Math.min(...pts.map(p => p[1])), hi = max ?? Math.max(...pts.map(p => p[1])); if (hi === lo) hi = lo + 1;
  const X = t => 4 + (t - t0) / ((t1 - t0) || 1) * (w - 8), Y = v => h - 4 - (v - lo) / (hi - lo) * (h - 8);
  const d = pts.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join("");
  const r = ref != null && ref >= lo && ref <= hi ? `<line x1="0" x2="${w}" y1="${Y(ref).toFixed(1)}" y2="${Y(ref).toFixed(1)}" class="ref"/>` : "";
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="chart" role="img" aria-label="trend, ${num(lo)} to ${num(hi)} ${unit}">${r}<path d="${d}"/></svg><div class="c-ax"><span>${num(lo)}${unit}</span><span>${new Date(t0 * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} to ${new Date(t1 * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span><span>${num(hi)}${unit}</span></div>`;
}
function bars(vals, { w = 360, h = 70, color = (v, i) => "" } = {}) {
  if (!vals.length) return `<div class="empty-chart">No model calls recorded yet.</div>`;
  const hi = Math.max(...vals.map(v => v.y), 1), bw = (w - 4) / vals.length;
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="chart bars" role="img" aria-label="model call durations, longest ${num(hi)} seconds">${vals.map((v, i) => `<rect x="${(2 + i * bw).toFixed(1)}" y="${(h - 2 - v.y / hi * (h - 6)).toFixed(1)}" width="${Math.max(1, bw - 1.2).toFixed(1)}" height="${(v.y / hi * (h - 6)).toFixed(1)}" class="${color(v, i)}"><title>${esc(v.t || "")}</title></rect>`).join("")}</svg><div class="c-ax"><span>0 s</span><span>last ${vals.length} model calls</span><span>${num(hi)} s</span></div>`;
}
const ico = n => `<svg class="ic" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const vIcon = ok => `<span class="vi ${ok ? "ok" : "no"}">${ico(ok ? "check" : "alert")}</span>`;
const dot = c => `<i class="dot ${c || ""}"></i>`;
const stClass = s => ({ done: "ok", running: "run", queued: "wait", failed: "bad", cancelled: "off" }[s] || "off");
const dl = rows => `<dl class="dl">${rows.filter(r => r).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
const block = (title, body, note = "", sub = "") => `<section class="block"${sub ? ` data-sub="${sub}"` : ""}><h3>${esc(title)}</h3>${note ? `<p class="note">${note}</p>` : ""}${body}</section>`;
const errBox = m => `<div class="errcard">${esc(m)}</div>`;

/* ---------- shells (static parts, built once per visit) ---------- */
const CATS = { work: { label: "Work", icon: "tasks", sections: ["tasks", "approvals", "schedule"] }, machine: { label: "Machine", icon: "pulse", sections: ["aimode", "models", "system"] }, library: { label: "Library", icon: "layers", sections: ["memory", "agents"] } };
const CAT_OF = Object.fromEntries(Object.entries(CATS).flatMap(([c, v]) => v.sections.map(s => [s, c])));
const SUBS = { tasks: [["queue", "Queue"], ["add", "Add"], ["settings", "Settings"]], approvals: [["waiting", "Waiting"], ["decided", "Decided"]], schedule: [["now", "Now"], ["checks", "Checks"], ["nights", "Nights"]],
  aimode: [["state", "State"], ["meters", "Resources"], ["control", "Switch"], ["config", "Config"]], memory: [["chats", "Chats"], ["sessions", "Sessions"], ["you", "About you"], ["vault", "Vault"], ["store", "Store"]],
  system: [["health", "Health"], ["findings", "Findings"], ["services", "Services"], ["trends", "Trends"], ["calls", "Model calls"], ["logs", "Logs"]] };
const SECTIONS = {
  agents: { title: "Agents", icon: "agents", lede: "Read live from the running council. The Orchestrator calls specialists one at a time.", every: 30000 },
  tasks: { icon: "tasks", title: "Tasks", lede: "Overnight work the council does between 03:00 and 09:00, and anything you queue by hand.", every: 4000 },
  approvals: { icon: "approve", title: "Approvals", lede: "Changes the SysAdmin proposed. Your decision is recorded; this version of the council cannot execute anything.", every: 8000 },
  schedule: { icon: "clock", title: "Schedule", lede: "When unattended work may run, and which safety checks pass right now.", every: 5000 },
  aimode: { icon: "power", title: "AI Mode", lede: "The PC's switch for AI work: live state, thresholds, and a guarded control.", every: 5000 },
  models: { icon: "chip", title: "Models", lede: "What LM Studio on the PC has, and what is loaded.", every: 10000 },
  memory: { icon: "layers", title: "Memory", lede: "What the council stores: conversations, runs, notes about you (off), and the vault copy it reads.", every: 15000 },
  system: { icon: "pulse", title: "System", lede: "Health of server1, the council's services and the GPU PC, from the collectors' own data.", every: 10000 },
};
const SHELL = {
  tasks: `<div data-sub="queue"><div data-reg="stats"></div><section class="block"><h3>Queue and history</h3><div data-reg="jobs"></div></section></div>
    ${block("Queue a task", `<form id="jobForm" class="jobform"><label class="vh" for="jobText">Task for the council</label><textarea id="jobText" rows="3" maxlength="4000" placeholder="For example: summarise what changed in the vault this week"></textarea><div class="row-end"><span class="note">Runs only inside the overnight window, when AI Mode is ON and everything is safe. Findings only; it changes nothing.</span><button class="btn-ink small" type="submit">${ico("plus")}Queue task</button></div></form>`, "", "add")}
    <section class="block" data-sub="settings"><h3>Overnight work</h3><div data-reg="config"></div></section>`,
  approvals: `<div data-sub="waiting" data-reg="pending"></div><section class="block" data-sub="decided"><h3>Decided</h3><div data-reg="decided"></div></section>`,
  schedule: `<div data-sub="now" data-reg="now"></div><section class="block" data-sub="checks"><h3>Safety checks</h3><div data-reg="checks"></div></section><section class="block" data-sub="nights"><h3>Nights</h3><div data-reg="nights"></div></section><section class="block" data-sub="nights"><h3>Waiting to run</h3><div data-reg="queued"></div></section>`,
  aimode: `<div data-sub="state" data-reg="state"></div><section class="block" data-sub="meters"><h3>Resources on the PC</h3><div data-reg="meters"></div></section><section class="block" data-sub="control"><h3>Switch</h3><div data-reg="control"></div></section><section class="block" data-sub="config"><h3>Configured behaviour</h3><div data-reg="config"></div></section>`,
  models: `<div data-reg="list"></div>`,
  memory: `<div data-reg="all"></div>`,
  system: `<div data-reg="all"></div>`,
  agents: `<div data-reg="all"></div>`,
};
const reg = (n) => document.querySelector(`#dash [data-reg=${n}]`);
const put = (n, html) => { const el = reg(n); if (el && el.__h !== html) { el.__h = html; el.innerHTML = html; if (D.applyLater == null) D.applyLater = setTimeout(() => { D.applyLater = null; applySub(); }, 0); } };

/* ---------- renderers ---------- */
function rTasks(d) {
  const c = d.counts || {}, run = d.running ? d.jobs.find(j => j.id === d.running) : null;
  put("stats", `<div class="stat-line">${["running", "queued", "done", "failed", "cancelled"].map(k => `<span>${dot(stClass(k))}<b>${num(c[k] || 0)}</b> ${k}</span>`).join("")}</div>${run ? `<div class="now-line">${dot("run")} Running now: <b>${esc(run.title)}</b> <span class="muted">(job ${run.id}, started ${ageOf(run.started_at)})</span></div>` : ""}`);
  put("jobs", d.jobs.length ? `<div class="tbl" role="table">${d.jobs.map(j => {
    const open = D.openJobs.has(j.id);
    return `<div class="tr ${open ? "open" : ""}" role="row"><button class="tr-h" data-act="job" data-id="${j.id}" aria-expanded="${open}">${dot(stClass(j.status))}<span class="c-t">${esc(j.title)}</span><span class="c-k">${esc(j.kind)}</span><span class="c-w">${j.status}${j.attempts > 1 ? ` · try ${j.attempts}` : ""}</span><span class="c-d">${j.duration_s != null ? fmt(j.duration_s * 1000) : ""}</span><span class="c-a">${clock(j.created_at)}</span></button>${open ? `<div class="detail" data-detail="${j.id}">${jobDetail(j.id)}</div>` : ""}</div>`;
  }).join("")}</div>` : `<p class="note">No jobs yet. The council queues its own at 03:00 and you can add one above.</p>`);
  if (!D.cfgDirty) renderConfig(d);
}
function jobDetail(id) {
  const j = D.jobDetail[id]; if (!j) return `<p class="note">Loading…</p>`;
  const ev = (j.events || []).map(e => `<li><time>${new Date(e.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time><b>${esc(e.event)}</b>${e.detail ? `<span>${esc(e.detail)}</span>` : ""}</li>`).join("");
  return `<div class="d-cols"><div><h4>Timeline</h4><ol class="events">${ev || "<li>no events</li>"}</ol>${j.note ? `<p class="note">Note: ${esc(j.note)}</p>` : ""}${["queued", "running"].includes(j.status) ? `<button class="btn-line" data-act="cancel" data-id="${j.id}">Cancel this task</button>` : ""}</div>` +
    `<div><h4>${j.report ? "Report" : "Prompt"}</h4><div class="answer small">${j.report ? md(j.report) : `<p class="note">No report yet. The task given to the council:</p><pre>${esc(j.prompt || "")}</pre>`}</div></div></div>`;
}
function renderConfig(d) {
  const cfg = D.cfg = D.cfg || d.config, kinds = d.kinds;
  put("config", `<p class="note">Each night the council collects facts with scripts, then asks the model to interpret them. Switch a kind off here; unfinished work carries over to the next night.</p><div class="kinds">${Object.entries(kinds).map(([k, v]) =>
    `<label class="kind"><input type="checkbox" data-kind="${k}" ${cfg.kinds[k] ? "checked" : ""}><span class="sw" aria-hidden="true"></span><span class="k-t"><b>${esc(v.title)}</b><small>${esc(v.about)}</small></span></label>`).join("")}</div>` +
    `<h4>Standing research topics</h4><p class="note">One topic is researched per night with web search and source checking (at most once per 6 days each). Up to 8.</p><ul class="topics">${cfg.topics.map((t, i) => `<li><span>${esc(t)}</span><button class="btn-x" data-act="rmtopic" data-i="${i}" aria-label="Remove topic ${esc(t)}">Remove</button></li>`).join("") || `<li class="note">No topics yet, so no research runs.</li>`}</ul>` +
    `<form id="topicForm" class="inline-form"><label class="vh" for="topicIn">New topic</label><input id="topicIn" maxlength="120" placeholder="For example: OpenSSH security advisories" ${cfg.topics.length >= 8 ? "disabled" : ""}><button class="btn-line" type="submit" ${cfg.topics.length >= 8 ? "disabled" : ""}>Add topic</button></form>` +
    `<h4>Cap</h4><div class="inline-form"><label for="capIn">Jobs per night (1 to 8)</label><input id="capIn" type="number" min="1" max="8" value="${cfg.max_per_night}"><button class="btn-line" data-act="savecap">Save</button></div>` +
    `<h4>Documented known state</h4><p class="note">These are expected and never counted as problems unless they change: containers ${Object.keys(d.known_state.containers).map(esc).join(", ")}; failed units ${Object.keys(d.known_state.units).map(esc).join(", ")}.</p>`);
}
function rApprovals(d) {
  const p = d.proposals.filter(x => x.status === "pending"), r = d.proposals.filter(x => x.status !== "pending");
  put("pending", `<div class="callout">${ico("info")}<span>${esc(d.note)}</span></div>` + block(`Waiting for you (${p.length})`, p.length ? p.map(x => `<article class="prop"><header><b>${esc(x.what)}</b><span class="muted">on ${esc(x.host)} · ${ageOf(x.ts)}</span></header><pre class="cmd">${esc(x.command)}</pre><p>${esc(x.why)}</p><div class="row-end"><button class="btn-line" data-act="decide" data-id="${x.id}" data-d="reject">${ico("x")}Reject</button><button class="btn-ink small" data-act="decide" data-id="${x.id}" data-d="approve">${ico("check")}Approve</button></div></article>`).join("") : `<p class="note">Nothing waiting. Proposals appear here when the SysAdmin finds something worth changing.</p>`));
  put("decided", (r.length ? `<div class="tbl">${r.map(x => `<div class="tr static"><span class="tr-h">${dot(x.status === "approved" ? "ok" : "off")}<span class="c-t">${esc(x.what)}</span><span class="c-k">${esc(x.host)}</span><span class="c-w">${x.status}</span><span class="c-a">${clock(x.decided_at)}</span></span></div>`).join("")}</div>` : `<p class="note">No decisions yet.</p>`) +
    (d.decisions.length ? `<h4>Decision log</h4><ul class="plain">${d.decisions.slice().reverse().map(x => `<li>${esc(x.ts || "")} · proposal ${esc(x.proposal)} ${esc(x.decision)}d${x.via ? " via " + esc(x.via) : ""}</li>`).join("")}</ul>` : ""));
}
function rSchedule(d) {
  const w = d.window, m = /(\d{2}):(\d{2})/.exec(w.now_london.split(" ")[1] || "00:00"), nowPct = ((+m[1]) * 60 + (+m[2])) / 1440 * 100;
  put("now", `<div class="verdict ${d.allowed ? "ok" : "no"}">${vIcon(d.allowed)}<div><b>${d.allowed ? "Unattended work may run right now" : "Unattended work is blocked right now"}</b><span>${d.allowed ? "Every check below passes." : esc(d.reasons.join("; "))}</span></div></div>` +
    `<div class="track" role="img" aria-label="Day timeline: window ${w.start_time} to ${w.end_time}, now ${w.now_london}"><i class="band" style="left:${3 / 24 * 100}%;width:${6 / 24 * 100}%"></i><i class="nowm" style="left:${nowPct.toFixed(2)}%"></i></div><div class="track-l"><span>00</span><span style="left:12.5%">03 window opens</span><span style="left:37.5%">09 closes</span><span>24</span></div>` +
    dl([["London time", esc(w.now_london)], ["Window", `${w.start_time} to ${w.end_time}, every day (BST handled)`], ["Next opens", esc(new Date(w.next_start).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" }))], ["Jobs tonight", `${d.caps.runs_tonight} of at most ${d.caps.max_per_night} runs (retries count)`], ["Scheduler", `checks every ${d.caps.tick_s} s; ${d.running ? "running job " + d.running : "idle"}`]]));
  put("checks", `<ul class="checks">${d.checks.map(c => `<li class="${c.ok ? "ok" : "no"}"><span class="g" aria-hidden="true">${c.ok ? "✓" : "✕"}</span><span>${esc(c.label)}</span><small>${esc(c.detail)}</small></li>`).join("")}</ul>`);
  const mx = Math.max(1, ...d.nights.map(n => n.total));
  put("nights", d.nights.length ? `<div class="nights">${d.nights.slice().reverse().map(n => `<div class="night" title="${esc(n.date)}: ${n.total} jobs (${Object.entries(n.by_status).map(([k, v]) => v + " " + k).join(", ")})"><div class="stack" style="height:${(n.total / mx * 100).toFixed(0)}%">${["done", "running", "queued", "cancelled", "failed"].filter(k => n.by_status[k]).map(k => `<i class="s-${k}" style="flex:${n.by_status[k]}"></i>`).join("")}</div><span>${esc(n.date.slice(5))}</span><b>${n.total}</b></div>`).join("")}</div><p class="note">Bars are jobs per night by outcome (green done, amber queued, grey cancelled, red failed).</p>` : `<p class="note">No overnight jobs recorded yet. The first window is tonight.</p>`);
  put("queued", d.queued.length ? `<ul class="plain">${d.queued.map(j => `<li>${dot("wait")} <b>${esc(j.title)}</b> <span class="muted">${esc(j.kind)} · priority ${j.priority}${j.note ? " · " + esc(j.note) : ""}</span></li>`).join("")}</ul>` : `<p class="note">Nothing is waiting.</p>`);
}
function rAiMode(d) {
  const pc = d.pc, c = d.config || {};
  if (!pc) { put("state", errBox("No PC state: the PC is off, asleep, or the poller is down.")); return; }
  const on = pc.ai_mode === "ON";
  put("state", `<div class="verdict ${on ? "ok" : "no"}">${vIcon(on)}<div><b>AI Mode ${esc(pc.ai_mode)}${pc.gaming ? ` · gaming (${esc(pc.game || "")})` : ""}</b><span>${esc(pc.note || "")} · reported ${pc.age_s != null ? num(pc.age_s) + " s ago" : "?"}</span></div></div>` +
    dl([["Windows", pc.logged_in ? "logged in" : "not logged in"], ["LM Studio", pc.lmstudio_up ? "running" : "not running"], ["Model", pc.model_loaded ? `${esc(pc.model)} @ ${num(pc.context)} tokens` : "not loaded"], ["Power plan", esc(pc.power_plan || "")], ["Tray app up for", pc.app_uptime_s != null ? fmt(pc.app_uptime_s * 1000) : "?"], ["Unload pending", pc.unload_pending ? "yes, waiting for the current request" : "no"]]));
  const vt = (pc.vram_used_mib || 0) + (pc.vram_free_mib || 0);
  put("meters", meter("RAM available", pc.ram_available_mb, 8000, num(pc.ram_available_mb) + " MB", [{ at: c.ram_unsafe_available_mb || 400, label: "unsafe " + (c.ram_unsafe_available_mb || 400) }, { at: c.load_min_available_mb || 3500, label: "load needs " + (c.load_min_available_mb || 3500) }]) +
    meter("Committed memory", pc.commit_pct, 100, num(pc.commit_pct, 1) + "%", [{ at: c.commit_unsafe_pct || 93, label: "unsafe " + (c.commit_unsafe_pct || 93) + "%" }]) +
    (vt ? meter("VRAM used", pc.vram_used_mib, vt, `${num(pc.vram_used_mib)} of ${num(vt)} MiB`, [{ at: vt - 250, label: "keep 250 free" }]) : ""));
  const last = d.control.last_result;
  put("control", (D.aimNote ? `<div class="callout">${ico("info")}<span>${esc(D.aimNote)}</span></div>` : "") + `<p class="note">${d.rules.map(r => esc(r.charAt(0).toUpperCase() + r.slice(1)) + ".").join(" ")}</p><div class="row-end">${on ? `<button class="btn-line" data-act="aim" data-cmd="off">${ico("power")}Turn AI Mode OFF</button>` : `<button class="btn-ink small" data-act="aim" data-cmd="on">${ico("power")}Turn AI Mode ON</button>`}</div>` +
    (last ? `<p class="note">Last request: ${esc(last.cmd)} → ${last.ok ? "sent" : "refused"} (${esc(last.msg || "")}) ${esc(clock(last.ts))}</p>` : "") +
    (d.control.audit.length ? `<h4>Control log</h4><ul class="plain">${d.control.audit.slice().reverse().map(x => `<li>${esc(clock(x.ts))} · ${esc(x.event)} · ${x.ok ? "ok" : "refused"} · ${esc(x.msg || "")}</li>`).join("")}</ul>` : ""));
  const n = c.night || {};
  put("config", c.night ? dl([["Night cycle", `${esc(n.start)} to ${esc(n.end)} Europe/London`], ["Preload", `${n.preload_minutes} minutes before the window`], ["Auto-load model", n.auto_load ? `yes, up to ${n.max_load_attempts} tries, ${n.retry_minutes} min apart` : "no"], ["After the window", n.auto_off_after_window ? "unload (only a model the tray loaded) and switch AI Mode OFF" : "leave as is"], ["Model", esc((c.model || {}).key || "") + ` @ ${num((c.model || {}).context)}, parallel ${(c.model || {}).parallel}`], ["Power plan", esc(c.power_plan || "")], ["Read from the PC", ageOf(c.poll_ts)]]) : `<p class="note">The PC's config has not been read yet (the poller refreshes it every 10 minutes).</p>`);
}
function rModels(d) {
  put("list", (d.ok ? "" : errBox(d.reason)) + (d.models.length ? `<div class="tbl models">${d.models.map(m => `<div class="tr static"><span class="tr-h">${dot(m.state === "loaded" ? "ok" : "off")}<span class="c-t">${esc(m.id)}</span><span class="c-k">${esc(m.arch || m.type || "")} ${esc(m.quantization || "")}</span><span class="c-w">${esc(m.state)}</span><span class="c-d">${m.state === "loaded" ? num(m.loaded_context_length) + " ctx" : "max " + num(m.max_context_length)}</span></span></div>`).join("")}</div>` : "") +
    (d.pc ? block("Memory use on the PC", dl([["GPU memory used", d.pc.vram_used_mib != null ? num(d.pc.vram_used_mib) + " MiB (" + num(d.pc.vram_free_mib) + " free)" : "n/a"], ["RAM available", num(d.pc.ram_available_mb) + " MB"], ["Committed", num(d.pc.commit_pct, 1) + "%"]]), "LM Studio does not report a model's size, so this is what the whole PC shows with the model loaded.") : "") +
    `<div class="callout">${ico("info")}<span>${esc(d.controls || "")}</span></div>`);
}
function rMemory(d) {
  const a = d.agno_db, c = d.conversations, v = d.vault_mirror;
  put("all", block("Conversations", dl([["Conversations", num(c.conversations)], ["Messages", num(c.messages)], ["Runs", num(c.runs) + " " + esc(JSON.stringify(c.runs_by_status || {}))]]), "", "chats") +
    block("Agno sessions (what the model sees as history)", dl([["Sessions", num(a.sessions)], ["Agent runs", num(a.runs)], ["Database size", num(a.size_kb) + " KB"], ["History given to the model", "the last 2 runs of a conversation, to stay inside 16K tokens"]]) +
      (a.top_sessions && a.top_sessions.length ? `<div class="tbl">${a.top_sessions.map(s => `<div class="tr static"><span class="tr-h"><span class="c-t mono">${esc(s.session_id)}</span><span class="c-k">${esc(s.type)}</span><span class="c-w">${s.runs} runs</span><span class="c-a">${s.updated ? clock(s.updated) : ""}</span></span></div>`).join("")}</div>` : "")) +
    block("Notes you asked it to keep", `<p class="note">${esc(d.user_memories.note)}</p>` + ((d.user_memories.notes || []).length ? `<div class="lst">${d.user_memories.notes.map(n => `<div class="li"><div class="li-h"><span class="nm sans">${esc(n.text)}</span><span class="st"><button class="btn-line small" data-act="mem-del" data-id="${n.id}">Forget</button></span></div></div>`).join("")}</div>` : `<p class="note">Nothing saved yet. In any chat, say: remember that my server is called server1.</p>`)) +
    block("Vault copy the council reads", dl([["Notes", num(v.notes)], ["Newest note changed", v.newest_note_mtime ? ageOf(v.newest_note_mtime) : "n/a"], ["Refresh", esc(v.refresh || "")], ["Access", "read-only markdown mirror; the council cannot write to the vault"]])) +
    block("Council key/value store", `<div class="tbl">${d.kv.map(k => `<div class="tr static"><span class="tr-h"><span class="c-t mono">${esc(k.k)}</span><span class="c-k wide">${esc(k.v)}</span></span></div>`).join("") || '<p class="note">Empty.</p>'}</div>${d.kv_seeded_flags ? `<p class="note">${num(d.kv_seeded_flags)} per-night "already evaluated" flags not shown.</p>` : ""}`, "Read-only here. Deleting a conversation is done from the sidebar and asks first."));
}
function rSystem(d) {
  const f = d.facts || {}, a = d.assessment, g = d.gate || {};
  const trend = k => line((d.pc_trend || []).map(r => [r.t, r[k]]), { unit: k === "commit_pct" ? "%" : "" });
  const calls = d.calls.map(c => ({ y: c.dur, t: `${c.dur}s, ${c.ptok} prompt / ${c.ctok} reply tokens` }));
  const tok = d.calls.filter(c => c.dur > 0 && c.ctok).map(c => c.ctok / c.dur);
  const lg = (t, rows) => rows.length ? `<details class="log"><summary>${t} (${rows.length})</summary><pre>${esc(rows.map(r => JSON.stringify(r)).join("\n"))}</pre></details>` : "";
  put("all",
    (a ? `<div class="verdict ${a.overall === "calm" ? "ok" : "no"}">${vIcon(a.overall === "calm")}<div><b>server1: ${esc(a.overall)}</b><span>${a.findings.length ? a.findings.length + " finding(s) above 'info'" : "no findings above 'info'"}; ${a.known.length} documented and expected item(s). Severity is decided by script rules, not by the model.</span></div></div>` : "") +
    block("Findings", a && (a.findings.length || a.known.length) ? `<ul class="plain">${a.findings.map(x => `<li>${dot(x.sev === "high" ? "bad" : "wait")} <b>${esc(x.sev)}</b> ${esc(x.what)} <span class="muted">${esc(x.why)}</span></li>`).join("")}${a.known.map(x => `<li>${dot("off")} <b>info</b> ${esc(x.what)} <span class="muted">${esc(x.why)}</span></li>`).join("")}</ul>` : `<p class="note">No facts collected yet.</p>`) +
    block("server1", dl([["Uptime", f.uptime_h != null ? num(f.uptime_h, 1) + " h" : "n/a"], ["Load (1/5/15)", (f.load_1_5_15 || []).join(" / ")], ["Memory", f.mem ? `${f.mem.available_gb} GB available of ${f.mem.total_gb}; swap used ${f.mem.swap_used_gb} GB` : ""], ["Root disk", f.disk_root ? `${f.disk_root.used_pct}% used, ${num(f.disk_root.free_gb)} GB free` : ""], ["Containers", f.containers_total != null ? num(f.containers_total) + " total" : ""], ["Backup", f.backup ? `log ${num(f.backup.log_age_h, 1)} h old, last run ${f.backup.last_run_ok ? "ok" : "NOT ok"}, restic check ${f.backup.restic_check_clean ? "clean" : "not clean"}` : ""], ["Facts collected", f.ts ? ageOf(f.ts) : "n/a"]]) +
      Object.entries(d.disk || {}).map(([p, x]) => meter("Disk " + p, x.used_gb, x.total_gb, `${num(x.used_gb)} of ${num(x.total_gb)} GB`)).slice(0, 1).join("")) +
    block("Council services", `<ul class="svc">${d.services.map(s => `<li>${dot(s.active === "active" ? "ok" : "bad")}<span>${esc(s.unit)}</span><small>${esc(s.active)}${s.next && s.next !== "n/a" ? " · next " + esc(s.next.replace(/^\w+ /, "")) : ""}</small></li>`).join("")}</ul>`) +
    block("GPU PC trend", `<div class="charts"><div><h4>RAM available (MB)</h4>${trend("ram_available_mb")}</div><div><h4>Committed memory (%)</h4>${trend("commit_pct")}</div><div><h4>VRAM used (MiB)</h4>${trend("vram_used_mib")}</div></div>`, "One sample per minute, kept for 24 hours, recorded by the council service itself.") +
    block("Model calls", bars(calls, { color: (v, i) => d.calls[i].recovery ? "rec" : "" }) + dl([["Calls shown", num(calls.length)], ["Median reply speed", tok.length ? num(tok.sort((x, y) => x - y)[Math.floor(tok.length / 2)], 1) + " tokens/s (includes thinking)" : "n/a"], ["Largest prompt", num(Math.max(0, ...d.calls.map(c => c.ptok || 0))) + " of 16,384 tokens"], ["Gate counters", esc(JSON.stringify(g))], ["Inference", d.inference.ok ? "ready" : esc(d.inference.reason)]]), "Bars are seconds per call; an outlined bar needed a recovery step (retry or tool-budget cutoff).") +
    block("Recent activity (secret-like fields are redacted by name)", lg("Tool calls", d.tool_calls) + lg("Council audit", d.audit) + lg("Ops broker (checks run on hosts)", d.ops_audit) + lg("Web helper", d.web_audit)));
}
function rAgents(d) {
  put("all", `<div class="agent-list">${d.agents.map(x => `<div class="agent" style="--c:var(${specVar(x.name)})"><h3><i></i>${esc(x.name)}<small>${esc(x.state)}</small></h3><div><p>${esc(x.role)}</p><div class="tools">${(x.tools || []).map(esc).join(", ") || "no tools"}</div></div></div>`).join("")}</div>`);
}
const RENDER = { agents: rAgents, tasks: rTasks, approvals: rApprovals, schedule: rSchedule, aimode: rAiMode, models: rModels, memory: rMemory, system: rSystem };

/* ---------- subsections: stacked on desktop, one at a time on a phone ---------- */
const SUB_MAP = {
  memory: { "Conversations": "chats", "Agno sessions": "sessions", "Notes about you": "you", "Vault copy": "vault", "Council key": "store" },
  system: { "Findings": "findings", "server1": "health", "Council services": "services", "GPU PC trend": "trends", "Model calls": "calls", "Recent activity": "logs" },
};
function applySub() {
  const sec = D.section, subs = SUBS[sec]; if (!subs) return;
  const map = SUB_MAP[sec] || {};
  document.querySelectorAll("#dash .block:not([data-sub])").forEach(b => { const t = (b.querySelector("h3") || {}).textContent || ""; const k = Object.keys(map).find(x => t.startsWith(x)); if (k) b.dataset.sub = map[k]; });
  if (sec === "system") document.querySelectorAll("#dash .verdict:not([data-sub])").forEach(v => v.dataset.sub = "health");
  const cur = D.sub[sec] && subs.some(x => x[0] === D.sub[sec]) ? D.sub[sec] : subs[0][0]; D.sub[sec] = cur;
  document.querySelectorAll("#dash [data-sub]").forEach(b => b.classList.toggle("off", b.dataset.sub !== cur));
  document.querySelectorAll("#dash .subbar [data-subgo]").forEach(c => { const on = c.dataset.subgo === cur; c.classList.toggle("on", on); c.setAttribute("aria-selected", on); });
}
function setSub(k) { D.sub[D.section] = k; try { sessionStorage.setItem("sub:" + D.section, k); } catch {} applySub(); const sc = $("#viewDash"); if (sc) sc.scrollTop = 0; }

/* ---------- loading ---------- */
async function dashLoad() {
  const s = D.section; if (!s) return;
  try {
    const r = await api((typeof ENDPOINT !== "undefined" && ENDPOINT[s]) || "/api/" + s); const d = await r.json();
    if (D.section !== s) return;
    if (!r.ok) throw new Error(d.error || r.status);
    D.data[s] = d; RENDER[s](d); if (typeof EXTRA !== "undefined" && EXTRA[s]) EXTRA[s](); $("#dashErr").hidden = true;
    for (const id of D.openJobs) if (s === "tasks" && !D.jobDetail[id]) loadJob(id);
  } catch (e) { if (e.message !== "login required") { $("#dashErr").hidden = false; $("#dashErr").textContent = "Could not load this section: " + e.message; } }
  refreshBadges();
}
async function loadJob(id) { try { D.jobDetail[id] = await (await api("/api/tasks/" + id)).json(); const el = document.querySelector(`[data-detail="${id}"]`); if (el) el.innerHTML = jobDetail(id); } catch {} }
async function refreshBadges() {
  try {
    const [t, a] = await Promise.all([jget("/api/tasks"), jget("/api/approvals")]);
    const run = (t.counts.running || 0) + (t.counts.queued || 0), pend = a.counts.pending || 0;
    $("#nbTasks").textContent = run ? run : ""; $("#nbApprovals").textContent = pend ? pend : ""; $("#nbWork").textContent = pend ? pend : ""; $("#nbWork").classList.toggle("alert", pend > 0);
    $("#nbApprovals").classList.toggle("alert", pend > 0);
  } catch {}
}
function dashEnter(section) {
  clearInterval(D.timer); D.section = section; D.cfg = null; D.cfgDirty = false;
  const sec = SECTIONS[section], cat = CAT_OF[section], cs = CATS[cat], subs = SUBS[section];
  try { const k = sessionStorage.getItem("sub:" + section); if (k && !D.sub[section]) D.sub[section] = k; } catch {}
  D.lastSec[cat] = section;
  const catbar = `<nav class="catbar" aria-label="${cs.label}">${cs.sections.map(k => `<button class="seg ${k === section ? "on" : ""}" data-go="${k}" ${k === section ? 'aria-current="page"' : ""}>${ico(SECTIONS[k].icon)}<span>${SECTIONS[k].title}</span></button>`).join("")}</nav>`;
  const subbar = subs ? `<div class="subbar" role="tablist" aria-label="${sec.title} sections">${subs.map(([k, l]) => `<button role="tab" class="chip" data-subgo="${k}">${l}</button>`).join("")}</div>` : "";
  $("#dash").innerHTML = `<div class="bars">${catbar}${subbar}</div><h2><span class="hico">${ico(sec.icon)}</span>${sec.title}</h2><p class="lede">${sec.lede}</p><div id="dashErr" class="errcard" hidden></div>` + SHELL[section];
  applySub(); dashLoad(); D.timer = setInterval(() => { if (!document.hidden && !document.querySelector("dialog[open]")) dashLoad(); }, sec.every);
  syncTabs();
}
function dashLeave() { clearInterval(D.timer); D.section = null; }

/* ---------- phone tab bar: five places, each one tap away ---------- */
function curTab() { return S.view === "chat" ? "chat" : S.view === "dash" && D.section ? CAT_OF[D.section] : null; }
function syncTabs() { const c = curTab(); document.querySelectorAll("#tabbar .tab").forEach(t => { const on = t.dataset.tab === c; t.classList.toggle("active", on); on ? t.setAttribute("aria-current", "page") : t.removeAttribute("aria-current"); }); }
document.addEventListener("click", e => {
  const t = e.target.closest("#tabbar .tab"), g = e.target.closest("[data-go]"), c = e.target.closest("[data-subgo]");
  if (g && $("#dash").contains(g)) return setView("dash", g.dataset.go);
  if (c) return setSub(c.dataset.subgo);
  if (!t) return; const k = t.dataset.tab;
  if (k === "chat") return setView("chat");
  if (k === "chats") return openSide();
  setView("dash", D.lastSec[k] || CATS[k].sections[0]);
});
const kbOn = () => document.body.classList.add("kb"), kbOff = () => document.body.classList.remove("kb");
document.addEventListener("focusin", e => { if (e.target.matches("textarea, input:not([type=checkbox])") && matchMedia("(max-width: 640px)").matches) kbOn(); });
document.addEventListener("focusout", () => setTimeout(() => { if (!document.activeElement || !document.activeElement.matches("textarea, input:not([type=checkbox])")) kbOff(); }, 50));

/* ---------- actions ---------- */
function ask(title, body, okLabel, danger) {       // native <dialog>: focus is trapped, Esc cancels, focus returns to the opener
  return new Promise(res => {
    const dlg = $("#dlg"), opener = document.activeElement;
    $("#dlgTitle").textContent = title; $("#dlgBody").innerHTML = body; $("#dlgOk").textContent = okLabel; $("#dlgOk").className = danger ? "btn-line danger" : "btn-ink small";
    const done = v => { dlg.close(); cleanup(); res(v); };
    const ok = () => done(true), cancel = () => done(false), onClose = () => { cleanup(); res(false); };
    function cleanup() { $("#dlgOk").removeEventListener("click", ok); $("#dlgCancel").removeEventListener("click", cancel); dlg.removeEventListener("close", onClose); if (opener && opener.focus) opener.focus(); }
    $("#dlgOk").addEventListener("click", ok); $("#dlgCancel").addEventListener("click", cancel); dlg.addEventListener("close", onClose, { once: true });
    dlg.showModal(); $("#dlgCancel").focus();
  });
}
async function saveCfg(patch) {
  const r = await api("/api/proactive", { method: "PATCH", body: JSON.stringify(patch) }); const j = await r.json();
  if (!r.ok) { alert(j.error || "Could not save"); return false; }
  D.cfg = j; D.cfgDirty = false; if (D.data.tasks) renderConfig({ ...D.data.tasks, config: j }); return true;
}
document.addEventListener("click", async e => {
  const t = e.target.closest("[data-act]"); if (!t || !$("#dash").contains(t)) return;
  const act = t.dataset.act, id = +t.dataset.id;
  if (act === "job") { if (D.openJobs.has(id)) D.openJobs.delete(id); else { D.openJobs.add(id); loadJob(id); } if (D.data.tasks) { reg("jobs").__h = null; rTasks(D.data.tasks); } }
  if (act === "cancel" && await ask("Cancel this task?", "<p>A queued task will not run. A running task stops after the model request that is in flight.</p>", "Cancel task", true)) { await api(`/api/tasks/${id}/cancel`, { method: "POST" }); delete D.jobDetail[id]; dashLoad(); loadJob(id); }
  if (act === "rmtopic") { const cfg = D.cfg; await saveCfg({ topics: cfg.topics.filter((_, i) => i !== +t.dataset.i) }); }
  if (act === "savecap") { const v = +$("#capIn").value; await saveCfg({ max_per_night: v }); }
  if (act === "decide") {
    const d = t.dataset.d, p = D.data.approvals.proposals.find(x => x.id === id);
    if (await ask(d === "approve" ? "Approve this change?" : "Reject this change?", `<p><b>${esc(p.what)}</b> on ${esc(p.host)}</p><pre class="cmd">${esc(p.command)}</pre><p class="note">This records your decision only. The council in this version cannot run commands, so nothing will be executed.</p>`, d === "approve" ? "Record approval" : "Record rejection", d === "reject")) { await api(`/api/approvals/${id}/${d}`, { method: "POST" }); dashLoad(); }
  }
  if (act === "aim") {
    const cmd = t.dataset.cmd, pc = (D.data.aimode || {}).pc || {};
    const msg = cmd === "on" ? `<p>This switches AI Mode <b>ON</b>: the PC loads the model (about 20 seconds; skipped with a message if less than 3,500 MB RAM is free), raises LM Studio's priority, and the council may start work when the window is open.</p>${pc.gaming ? `<p class="note"><b>A game is running (${esc(pc.game || "")}).</b> The tray refuses to turn AI Mode on while a game is running; close the game first.</p>` : ""}${pc.logged_in ? "" : `<p class="note">Windows is not logged in.</p>`}` : `<p>This switches AI Mode <b>OFF</b>: <b>the model unloads</b> once any request already running has finished, and priorities and the power plan are restored. The PC is yours for games and normal use.</p>`;
    if (await ask(cmd === "on" ? "Turn AI Mode ON?" : "Turn AI Mode OFF?", msg, cmd === "on" ? "Turn ON" : "Turn OFF", false)) {
      t.disabled = true; D.aimNote = `Sending: AI Mode ${cmd.toUpperCase()}…`; if (D.data.aimode) { reg("control").__h = null; rAiMode(D.data.aimode); }
      const r = await api("/api/aimode", { method: "POST", body: JSON.stringify({ cmd, confirm: true }) }); const j = await r.json().catch(() => ({}));
      if (!r.ok) { D.aimNote = null; alert(j.reason || j.error || "The request did not go through"); dashLoad(); return; }
      D.aimNote = `Sent. Waiting for the PC to report AI Mode ${cmd.toUpperCase()}…`;
      for (let i = 0; i < 24 && D.section === "aimode"; i++) { await new Promise(r => setTimeout(r, 2500)); await dashLoad(); if (((D.data.aimode || {}).pc || {}).ai_mode === cmd.toUpperCase()) break; }
      D.aimNote = null; dashLoad();
    }
  }
});
document.addEventListener("change", async e => {
  const k = e.target.dataset && e.target.dataset.kind; if (!k || !$("#dash").contains(e.target)) return;
  if (!await saveCfg({ kinds: { [k]: e.target.checked } })) e.target.checked = !e.target.checked;
});
document.addEventListener("submit", async e => {
  if (e.target.id === "jobForm") {
    e.preventDefault(); const v = $("#jobText").value.trim(); if (!v) return;
    const r = await api("/api/tasks", { method: "POST", body: JSON.stringify({ prompt: v, title: v.slice(0, 60) }) });
    if (r.ok) { $("#jobText").value = ""; dashLoad(); } else alert("Could not queue the task");
  }
  if (e.target.id === "topicForm") {
    e.preventDefault(); const v = $("#topicIn").value.trim(); if (!v) return;
    await saveCfg({ topics: [...D.cfg.topics, v] });
  }
});
