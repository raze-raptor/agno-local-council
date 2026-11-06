"use strict";
/* Capability sections: Workflows, Agents (roster + Agno capability matrix), Tools, Skills, Knowledge, Evaluations, Traces, PC access, Browser access.
   Same shell/region pattern as dash.js; every list is read from /api/cap/* (the live registry and state files). */
CATS.work.sections.push("workflows");
CATS.library.label = "Council"; CATS.library.sections.push("tools", "skills", "knowledge", "evaluations", "traces");
CATS.machine.sections.push("browser");
for (const [c, v] of Object.entries(CATS)) for (const s of v.sections) CAT_OF[s] = c;
const ENDPOINT = { agents: "/api/cap/agents", tools: "/api/cap/tools", skills: "/api/cap/skills", workflows: "/api/cap/workflows", knowledge: "/api/cap/knowledge", evaluations: "/api/cap/evaluations", traces: "/api/cap/traces", pc: "/api/bridge", browser: "/api/bridge" };
Object.assign(SECTIONS, {
  workflows: { icon: "flow", title: "Workflows", lede: "Repeatable multi-step jobs. Steps run one after another, one model request at a time.", every: 4000 },
  tools: { icon: "wrench", title: "Tools", lede: "Every tool the council knows, who may use it, and what is switched off. Permissions are enforced in code on each call.", every: 20000 },
  skills: { icon: "book", title: "Skills", lede: "Short how-to guides agents load only when they need them.", every: 30000 },
  knowledge: { icon: "search", title: "Knowledge", lede: "What the council can look up, and how.", every: 30000 },
  evaluations: { icon: "flask", title: "Evaluations", lede: "Does the right agent call the right tool? Real runs, checked by Agno's reliability test.", every: 6000 },
  traces: { icon: "trace", title: "Traces", lede: "Every tool call the council made or was refused, with who asked.", every: 8000 },
  pc: { icon: "monitor", title: "PC access", lede: "Control of your Windows PC. Prepared, but switched off.", every: 30000 },
  browser: { icon: "globe", title: "Browser & PC", lede: "Lets the Screen Operator work in your Brave browser and read basic facts from your PC. You switch it on and off here.", every: 4000 },
});
SECTIONS.agents.lede = "The Orchestrator and its specialists, with their skills and permitted tools, and the full Agno capability audit.";
SECTIONS.agents.icon = "agents";
Object.assign(SUBS, { agents: [["roster", "Roster"], ["matrix", "Capability matrix"]], tools: [["active", "Active"], ["off", "Switched off"], ["rules", "Rules"]], workflows: [["run", "Run"], ["runs", "History"]],
  traces: [["recent", "Recent"], ["counts", "Counts"]], pc: [["status", "Status"], ["tools", "Tools"], ["how", "Activation"]], browser: [["status", "Control"], ["activity", "Activity"], ["tools", "Tools"], ["how", "Setup"]] });
Object.assign(SHELL, {
  agents: `<div data-sub="roster" data-reg="all"></div><section class="block" data-sub="matrix"><h3>Agno capability matrix</h3><div data-reg="matrix"></div></section>`,
  tools: `<div data-sub="active" data-reg="active"></div><section class="block" data-sub="off"><h3>Switched off</h3><div data-reg="off"></div></section><section class="block" data-sub="rules"><h3>How permissions work</h3><div data-reg="rules"></div></section>`,
  skills: `<div data-reg="all"></div>`, knowledge: `<div data-reg="all"></div>`,
  workflows: `<section class="block" data-sub="run"><h3>Start a workflow</h3><div class="jobform"><label class="vh" for="wfText">What the workflow should work on</label><textarea id="wfText" rows="3" maxlength="2000" placeholder="For example: a function that parses a duration like 1h30m into seconds"></textarea></div><div data-reg="defs"></div></section><section class="block" data-sub="runs"><h3>History</h3><div data-reg="runs"></div></section>`,
  evaluations: `<div data-reg="all"></div>`,
  traces: `<div data-sub="recent" data-reg="recent"></div><section class="block" data-sub="counts"><h3>By tool</h3><div data-reg="counts"></div></section>`,
  pc: `<div data-sub="status" data-reg="status"></div><section class="block" data-sub="tools"><h3>Tools</h3><div data-reg="tools"></div></section><section class="block" data-sub="how"><h3>How it will be activated</h3><div data-reg="how"></div></section>`,
  browser: `<div data-sub="status" data-reg="status"></div><section class="block" data-sub="activity"><h3>Recent activity</h3><div data-reg="activity"></div></section><section class="block" data-sub="tools"><h3>What it can do</h3><div data-reg="tools"></div></section><section class="block" data-sub="how"><h3>Setup</h3><div data-reg="how"></div></section>`,
});
const bd = (t, c = "") => `<span class="badge ${c}">${esc(t)}</span>`;
const scopeBd = s => bd(s, s === "read" ? "ok" : s === "user" ? "warn" : "mute");
const stBd = s => bd(s, s === "active" ? "ok" : "mute");
const li = (head, right, sum, meta) => `<div class="li"><div class="li-h">${head}<span class="st">${right}</span></div>${sum ? `<p class="li-s">${sum}</p>` : ""}${meta ? `<p class="li-m">${meta}</p>` : ""}</div>`;
const flags = t => [t.approval !== "none" ? "needs your approval" + (t.approval === "per_call" ? " each time" : "") : "", t.network ? "reaches the network" : "", t.untrusted_output ? "returns untrusted text" : ""].filter(Boolean);
const toolRow = (t, showWho = true) => li(`${dot(t.state === "active" ? "ok" : "off")}<span class="nm">${esc(t.name)}</span>${scopeBd(t.scope)}`, stBd(t.state), esc(t.summary), [showWho ? (t.agents.length >= 6 ? "Used by every agent" : "Used by " + t.agents.map(esc).join(", ")) : "", ...flags(t)].filter(Boolean).join(" · "));

function rAgents2(d) {
  put("all", `<div class="agent-list">${d.agents.map(x => `<div class="agent" style="--c:var(${specVar(x.name)})"><h3><i></i>${esc(x.name)}<small>${esc(x.state)}</small></h3><div><p>${esc(x.role)}</p>${x.skills && x.skills.length ? `<div class="tools">Skills: ${x.skills.map(esc).join(", ")}</div>` : ""}<div class="tools">${(x.permitted_tools || []).length ? "Tools: " + x.permitted_tools.map(t => `${esc(t.name)}${t.state !== "active" ? " (off)" : ""}`).join(", ") : (x.tools || []).map(esc).join(", ") || "no tools"}</div></div></div>`).join("")}</div>`);
}
async function loadMatrix() {
  if (!D.matrix) { try { D.matrix = await jget("/api/cap/matrix"); } catch { return; } }
  const d = D.matrix, areas = [...new Set(d.matrix.map(r => r.area))], cls = s => s === "IMPLEMENTED" ? "ok" : s === "PARTIAL" ? "warn" : "mute";
  put("matrix", `<p class="note">${Object.entries(d.counts).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ")}. Checked against the installed Agno 3.1.12, which is the latest release.</p>` + areas.map(a => `<h4>${esc(a)}</h4><div class="lst">${d.matrix.filter(r => r.area === a).map(r => li(`<span class="nm sans">${esc(r.feature)}</span>`, bd(r.status.toLowerCase(), cls(r.status)), "", esc(r.evidence))).join("")}</div>`).join(""));
}
function rTools(d) {
  const on = d.tools.filter(t => t.state === "active"), off = d.tools.filter(t => t.state !== "active");
  put("active", `<p class="note">${on.length} tools active, ${off.length} switched off.</p><div class="lst">${on.map(t => toolRow(t)).join("")}</div>`);
  put("off", `<div class="callout">${ico("info")}<span>These exist only as prepared definitions. No agent holds them, and the permission layer refuses them while the bridge flag file is absent (${d.bridge_flag ? "present" : "absent"}).</span></div><div class="lst">${off.map(t => toolRow(t)).join("")}</div>`);
  put("rules", `<ul class="plain"><li><b>read</b>: looks only. <b>user</b>: an ordinary change; every call needs your approval. <b>admin</b>: privileged; never available.</li><li>Each call goes through one hook that checks: the tool is registered, this agent may use it, the tool and the PC bridge are enabled, the arguments are sane.</li><li>Refusals and allowed calls are both written to the audit (see Traces) with the arguments, secrets redacted.</li><li>Text returned by the web, notes, files and the browser is treated as data; instruction-like text is flagged to the agent.</li></ul>`);
}
function rSkills(d) { put("all", `<p class="note">${esc(d.note)}</p><div class="agent-list">${d.skills.map(s => `<div class="agent" style="--c:var(--c-orch)"><h3><i></i>${esc(s.name)}<small>${s.chars} chars</small></h3><div><p>${esc(s.description)}</p><div class="tools">Used by: ${s.agents.map(esc).join(", ")}</div></div></div>`).join("")}</div>`); }
function rKnowledge(d) { const i = d.index; put("all", block("Vault index", dl([["Engine", esc(i.engine)], ["Notes", num(i.notes)], ["Chunks", num(i.chunks)], ["Built", i.built ? clock(i.built) : "not yet"], ["Meaning-based search", "off: it needs a second model loaded"]]), esc(d.note)) + block("Sources", `<ul class="plain">${d.sources.map(s => `<li>${esc(s)}</li>`).join("")}</ul>`)); }
function rWorkflows(d) {
  const busy = d.runs.some(r => r.status === "running");
  put("defs", `<div class="agent-list">${d.workflows.map(w => `<div class="agent" style="--c:var(--c-orch)"><h3><i></i>${esc(w.title)}</h3><div><p>${w.steps.map(s => esc(s.name) + " (" + esc(s.agent) + ")").join(" → ")}</p><div class="row-end"><span class="note">Needs: ${esc(w.needs)}.</span><button class="btn-ink small" data-act="wf-run" data-name="${esc(w.name)}" ${busy ? "disabled" : ""}>${ico("bolt")}Run</button></div></div></div>`).join("")}</div>`);
  put("runs", d.runs.length ? d.runs.map(r => `<article class="prop"><header><b>${esc(r.name.replace(/_/g, " "))}</b><span class="muted">${r.status}${r.ended ? " · " + Math.round(r.ended - r.started) + " s" : ""} · ${ageOf(r.started)}</span></header><p>${esc(r.input)}</p>${r.steps.map(s => `<details><summary>${esc(s.name)}</summary><pre class="cmd">${esc(s.output)}</pre></details>`).join("")}${r.error ? `<p class="note">${esc(r.error)}</p>` : ""}${r.status === "running" ? `<div class="row-end"><button class="btn-line" data-act="wf-cancel" data-wid="${esc(r.id)}">${ico("x")}Cancel</button></div>` : ""}</article>`).join("") : `<p class="note">No runs yet.</p>`);
}
function rEvals(d) {
  const last = d.runs[0], busy = last && last.status === "running";
  put("all", `<div class="row-end"><span class="note">${d.cases.length} cases, one real model run each (about 3 to 6 minutes in total, one at a time).</span><button class="btn-ink small" data-act="eval-run" ${busy ? "disabled" : ""}>${ico("bolt")}${busy ? "Running…" : "Run evaluation"}</button></div>` +
    block("Cases", `<div class="lst">${d.cases.map(c => { const r = last && last.results.find(x => x.id === c.id); return li(`${r ? vIcon(!!r.passed) : dot("off")}<span class="nm sans">${esc(c.id.replace(/_/g, " "))}</span>`, r ? bd((r.passed ? "passed" : "failed") + " · " + r.secs + " s", r.passed ? "ok" : "warn") : bd("not run", "mute"), esc(c.prompt), `Asked of ${esc(c.agent)}. Expects ${c.expects.length ? c.expects.map(esc).join(" or ") : "no tool or delegation (ask, don't act)"}${r && r.tools ? ". Used " + (r.tools.length ? [...new Set(r.tools)].map(esc).join(", ") : "nothing") : ""}${r && r.error ? ". " + esc(r.error) : ""}`); }).join("")}</div>`) +
    (last ? `<p class="note">Last run ${ageOf(last.started)}: ${last.results.filter(r => r.passed).length} of ${last.results.length} passed (${last.status}).</p>` : ""));
}
function rTraces(d) {
  put("recent", `<p class="note">${esc(d.agno_tracing.why)}</p>` + (d.recent.length ? `<div class="lst">${d.recent.map(r => li(`${dot(r.decision === "allowed" ? "ok" : "bad")}<span class="nm">${esc(r.tool)}</span>`, bd(r.decision, r.decision === "allowed" ? "ok" : "warn"), r.reason ? esc(r.reason) : "", `${esc(r.actor)} · ${esc((r.ts || "").slice(11, 19))}${r.ms != null ? " · " + r.ms + " ms" : ""}`)).join("")}</div>` : `<p class="note">No tool calls recorded yet.</p>`));
  put("counts", `<p class="note">${d.refused} refused in the last ${Object.values(d.by_tool).reduce((a, b) => a + b, 0)} calls.</p><ul class="plain">${Object.entries(d.by_tool).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<li><b>${esc(k)}</b> ${v}</li>`).join("")}</ul>`);
}
function rBridge(d) {
  const on = d.armed, lvl = d.level, ext = d.extension_seen_s;
  const extTxt = ext == null ? (d.companion ? "not connected (switch it on in the extension)" : "unknown") : ext < 20 ? "connected" : "last seen " + ext + " s ago";
  const btn = on ? `<button class="btn-line" data-act="bridge" data-cmd="disarm">Switch off</button>`
    : `<button class="btn-ink small" data-act="bridge" data-cmd="arm" ${lvl === "ready" ? "" : "disabled"}>Switch on</button>`;
  put("status", `<div class="verdict ${on ? "ok" : "no"}">${vIcon(on)}<div><b>${on ? "On" : "Off"}</b><span>${esc(d.why)}</span></div></div>` +
    `<div class="row-end"><span class="note">${on ? "Switching off stops the next action at once and cancels any pending approval." : "Nothing can touch your browser or PC while this is off."}</span>${btn}</div>` +
    (d.pending && d.pending.length ? `<div class="lst">${d.pending.map(g => li(`${dot("warn")}<span class="nm sans">Waiting for you</span>`, bd(g.kind === "risky" ? "risky step" : "approval", "warn"), esc(g.summary), `<button class="btn-ink small" data-act="grant" data-id="${esc(g.id)}" data-d="allow_task">Allow for this task</button> <button class="btn-line small" data-act="grant" data-id="${esc(g.id)}" data-d="allow">Allow once</button> <button class="btn-line small" data-act="grant" data-id="${esc(g.id)}" data-d="deny">Deny</button>`)).join("")}</div>` : "") +
    dl([["PC companion", d.companion ? "running" : "not running"], ["Brave extension", extTxt], ["Approval", "Asked once per task in the chat. Payments and deletions ask every time."], ["Passwords and codes", "Never typed by the council: it stops and you type them"], ["Where it works", "A tab it opens itself, only on sites you allow in the extension"]]));
  put("activity", d.recent.length ? `<div class="lst">${d.recent.map(r => li(`${dot(r.decision === "refused" ? "bad" : "ok")}<span class="nm">${esc(r.tool)}</span>`, bd(r.decision, r.decision === "refused" ? "warn" : "ok"), r.reason ? esc(r.reason) : "", esc((r.ts || "").slice(5, 19).replace("T", " ")))).join("")}</div>` : `<p class="note">Nothing yet. Every action, allowed or refused, is listed here.</p>`);
  put("tools", `<div class="lst">${d.tools.map(t => toolRow(t, false)).join("")}</div>`);
  put("how", `<ol class="plain"><li><b>On the PC:</b> the companion (<code>CouncilBridge.ps1</code>) runs only after you log in, on 127.0.0.1, reached over your existing SSH access. No firewall port is opened.</li><li><b>In Brave:</b> load the Council Bridge extension (Extensions, Developer mode, Load unpacked), click its icon, tick <i>Let the council use this browser</i> and list the sites it may open.</li><li><b>Here:</b> press Switch on. Switching off is always one click, and the extension has its own off switch too.</li><li>The Screen Operator is the only agent that gets these tools. The main council and Rogue's team never do.</li></ol>`);
}
Object.assign(RENDER, { agents: rAgents2, tools: rTools, skills: rSkills, knowledge: rKnowledge, workflows: rWorkflows, evaluations: rEvals, traces: rTraces, browser: rBridge });
const EXTRA = { agents: loadMatrix };
document.addEventListener("click", async e => {
  const t = e.target.closest("[data-act]"); if (!t || !$("#dash").contains(t)) return;
  const act = t.dataset.act;
  if (act === "wf-run") { const txt = $("#wfText").value.trim(); if (!txt) { $("#wfText").focus(); return; } t.disabled = true; const r = await api("/api/workflows/run", { method: "POST", body: JSON.stringify({ name: t.dataset.name, input: txt }) }); const j = await r.json(); if (!r.ok) { $("#dashErr").hidden = false; $("#dashErr").textContent = j.error || "Could not start"; t.disabled = false; } else { $("#wfText").value = ""; setSub("runs"); } dashLoad(); }
  if (act === "wf-cancel" && await ask("Cancel this workflow?", "<p>It stops after the model request that is in flight.</p>", "Cancel workflow", true)) { await api(`/api/workflows/${t.dataset.wid}/cancel`, { method: "POST" }); dashLoad(); }
  if (act === "bridge") { const arm = t.dataset.cmd === "arm"; if (arm && !(await ask("Switch on browser and PC control?", "<p>The Screen Operator can then open sites in a tab of its own in Brave and read basic facts from the PC. It asks you before the first change in each task, never types passwords or card details, and asks every time for payments and deletions. You can switch it off here at any moment.</p>", "Switch on"))) return; t.disabled = true; const r = await api("/api/bridge", { method: "POST", body: JSON.stringify({ action: t.dataset.cmd }) }); const j = await r.json().catch(() => ({})); if (!r.ok) { $("#dashErr").hidden = false; $("#dashErr").textContent = j.message || "Could not do that."; } dashLoad(); }
  if (act === "grant") { t.disabled = true; await api("/api/grants/" + t.dataset.id, { method: "POST", body: JSON.stringify({ decision: t.dataset.d }) }); dashLoad(); }
  if (act === "mem-del") { t.disabled = true; await api("/api/memories/" + t.dataset.id, { method: "POST", body: JSON.stringify({ action: "delete" }) }); dashLoad(); }
  if (act === "eval-run" && await ask("Run the evaluation?", "<p>Five real model runs, one after another. It uses the model for several minutes and blocks other work meanwhile.</p>", "Run evaluation")) { t.disabled = true; await api("/api/evals/run", { method: "POST" }); dashLoad(); }
});

const _dashEnter = dashEnter;                       // keep the active category button in view when the bar scrolls (7 sections in Council)
dashEnter = function (section) { _dashEnter(section); const cb = document.querySelector("#dash .catbar"); if (cb && cb.children.length > 3) cb.classList.add("many"); const a = document.querySelector("#dash .catbar .seg.on"); if (a && a.scrollIntoView) a.scrollIntoView({ inline: "center", block: "nearest" }); };

/* ---------- AI Mode > Model: swap between the council's model and Rogue's ---------- */
SHELL.aimode += `<section class="block" data-sub="control"><h3>Model</h3><div data-reg="modelswap"></div></section>`;
EXTRA.aimode = async () => {
  let st; try { st = await jget("/api/status"); } catch { return; }
  const m = st.models || {}, loaded = (st.inference || {}).loaded, pc = ((D.data.aimode || {}).pc) || {};
  const which = loaded === m.rogue ? "Rogue" : loaded === m.council ? "the council" : loaded ? loaded : "none";
  const on = pc.ai_mode === "ON", busy = !!D.swapNote;
  const target = loaded === m.rogue ? "main" : "rogue";
  put("modelswap", `<p class="note">Loaded now: <b>${esc(which)}</b>. Only one model fits at a time, so switching unloads the other (about a minute). Needs AI Mode ON and no game running. Rogue's team normally starts by itself, after the council declines a request; this button is the manual override.</p>` +
    (D.swapNote ? `<p class="note">${esc(D.swapNote)}</p>` : "") +
    `<div class="row-end"><span class="note">${target === "rogue" ? "Rogue is the unfiltered assistant. While its model is loaded the council and the overnight jobs are offline." : "Back to the Orchestrator and its specialists."}</span><button class="btn-line" data-act="swap" data-cmd="${target}" ${(!on || busy) ? "disabled" : ""}>${ico("power")}${target === "rogue" ? "Switch to Rogue's model" : "Switch back to the council"}</button></div>` +
    (!on ? `<p class="note">AI Mode is off, so the model cannot be switched.</p>` : ""));
};
document.addEventListener("click", async e => {
  const t = e.target.closest('[data-act="swap"]'); if (!t || !$("#dash").contains(t)) return;
  const cmd = t.dataset.cmd, rg = cmd === "rogue";
  if (!(await ask(rg ? "Switch to Rogue's model?" : "Switch back to the council model?", rg ? "<p>This unloads the council's model and loads Rogue's unfiltered model (about a minute). The council and the overnight jobs are offline until you switch back. Anything running finishes first. Rogue's team only takes requests the council declined.</p>" : "<p>This unloads Rogue's model and loads the council's model again (about a minute).</p>", rg ? "Switch to Rogue" : "Switch back", false))) return;
  t.disabled = true; D.swapNote = "Sending the request…"; EXTRA.aimode();
  const r = await api("/api/aimode", { method: "POST", body: JSON.stringify({ cmd, confirm: true }) }); const j = await r.json().catch(() => ({}));
  if (!r.ok) { D.swapNote = (j.msg || j.reason || j.error || "The request did not go through"); EXTRA.aimode(); setTimeout(() => { D.swapNote = null; EXTRA.aimode(); }, 6000); return; }
  D.swapNote = "Sent. The PC is unloading one model and loading the other…";
  const want = rg ? "rogue" : "council";
  for (let i = 0; i < 45 && D.section === "aimode"; i++) {
    await new Promise(r => setTimeout(r, 4000)); await EXTRA.aimode();
    try { const s = await jget("/api/status"), m = s.models || {}; if ((s.inference || {}).loaded === m[want]) break; } catch {}
  }
  D.swapNote = null; EXTRA.aimode(); if (typeof pollStatus === "function") pollStatus();
});

/* ---------- System > Findings: hourly security patch watch ---------- */
SHELL.system = `<div data-sub="findings" data-reg="patches"></div>` + SHELL.system;
const PATCH_ST = { ok: ["up to date", "ok"], patch_available: ["patch available", "warn"], newer_line: ["newer release line", "warn"], security_update: ["security update", "warn"], unknown: ["not checked", "mute"] };
EXTRA.system = async () => {
  let d; try { d = await jget("/api/cap/patches"); } catch { return; }
  const age = d.ts ? ageOf(d.ts) : null, worst = d.results.some(r => r.severity === "high") ? "high" : d.results.some(r => r.severity === "medium") ? "medium" : d.results.some(r => r.severity === "low") ? "low" : "none";
  put("patches", `<section class="block"><h3>Security patches</h3><p class="note">${d.ts ? "Checked " + esc(age) + ". " : ""}Runs hourly while AI Mode is on; it only reads versions and advisories and never updates anything.${d.last_skipped && (!d.ts || d.last_skipped > d.ts) ? " Last skipped " + esc(ageOf(d.last_skipped)) + " (AI Mode was off)." : ""}</p>` +
    (d.results.length ? `<div class="lst">${d.results.map(r => { const [lbl, cls] = PATCH_ST[r.status] || PATCH_ST.unknown; return li(`${dot(r.severity === "high" || r.severity === "medium" ? "bad" : r.status === "ok" ? "ok" : "wait")}<span class="nm sans">${esc(r.name)}</span>`, bd(lbl, cls), `Installed <b>${esc(r.installed || "?")}</b>${r.latest_in_line && r.latest_in_line !== r.installed ? ` · newest in your line <b>${esc(r.latest_in_line)}</b>` : ""}${r.latest && r.latest !== r.latest_in_line ? ` · newest overall ${esc(r.latest)}` : ""}`, esc((r.details || []).join(" ")) + (r.status !== "ok" && r.hint ? `<br>To update: ${esc(r.hint)}` : "")); }).join("")}</div>` : `<p class="note">${esc(d.note || "No result yet.")}</p>`) + `</section>`);
};
