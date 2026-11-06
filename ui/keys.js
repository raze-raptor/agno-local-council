"use strict";
/* Desktop keyboard layer (command palette, shortcuts, help) and the phone sheet drag. Shortcuts are ignored while typing unless they carry Ctrl/Cmd. */
const PAL = { items: [], sel: 0 };
const SECTION_NAMES = [["chat", "Chat", "chat", "chat"], ["tasks", "Tasks", "dash", "tasks"], ["approvals", "Approvals", "dash", "approve"], ["schedule", "Schedule", "dash", "clock"], ["aimode", "AI Mode", "dash", "power"], ["models", "Models", "dash", "chip"], ["system", "System", "dash", "pulse"], ["memory", "Memory", "dash", "layers"], ["agents", "Agents", "dash", "agents"], ["workflows", "Workflows", "dash", "flow"], ["tools", "Tools", "dash", "wrench"], ["skills", "Skills", "dash", "book"], ["knowledge", "Knowledge", "dash", "search"], ["evaluations", "Evaluations", "dash", "flask"], ["traces", "Traces", "dash", "trace"], ["pc", "PC access", "dash", "monitor"], ["browser", "Browser access", "dash", "globe"]];
function paletteItems(q) {
  const items = [{ label: "New chat", icon: "plus", hint: "Ctrl+Shift+O", run: () => newConversation() }, ...SECTION_NAMES.map(([id, label, view, icon]) => ({ label: "Go to " + label, icon, run: () => setView(view, id) })),
    { label: "Keyboard shortcuts", icon: "keys", hint: "Ctrl+/", run: () => $("#help").showModal() },
    ...(S.convs || []).slice(0, 40).map(c => ({ label: c.title, icon: "chat", hint: "conversation", run: () => openConv(c.id) }))];
  q = q.trim().toLowerCase(); return q ? items.filter(i => i.label.toLowerCase().includes(q)) : items.slice(0, 14);
}
function paintPalette() {
  PAL.items = paletteItems($("#palIn").value); PAL.sel = Math.min(PAL.sel, Math.max(0, PAL.items.length - 1));
  $("#palList").innerHTML = PAL.items.map((it, i) => `<li role="option" id="pal${i}" class="${i === PAL.sel ? "sel" : ""}" aria-selected="${i === PAL.sel}" data-i="${i}"><svg class="ic" aria-hidden="true"><use href="#i-${it.icon || "chat"}"/></svg><span class="pl">${esc(it.label)}</span>${it.hint ? `<small>${esc(it.hint)}</small>` : ""}</li>`).join("") || `<li class="none">Nothing matches.</li>`;
  $("#palIn").setAttribute("aria-activedescendant", PAL.items.length ? "pal" + PAL.sel : "");
}
function openPalette() { const d = $("#palette"); if (d.open) return; $("#palIn").value = ""; PAL.sel = 0; paintPalette(); d.showModal(); $("#palIn").focus(); }
function runPalette(i) { const it = PAL.items[i]; $("#palette").close(); if (it) it.run(); }
$("#palIn").addEventListener("input", () => { PAL.sel = 0; paintPalette(); });
$("#palIn").addEventListener("keydown", e => {
  if (e.key === "ArrowDown") { e.preventDefault(); PAL.sel = (PAL.sel + 1) % Math.max(1, PAL.items.length); paintPalette(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); PAL.sel = (PAL.sel - 1 + PAL.items.length) % Math.max(1, PAL.items.length); paintPalette(); }
  else if (e.key === "Enter") { e.preventDefault(); runPalette(PAL.sel); }
});
$("#palList").addEventListener("click", e => { const li = e.target.closest("[data-i]"); if (li) runPalette(+li.dataset.i); });
document.querySelectorAll("dialog").forEach(d => d.addEventListener("click", e => { if (e.target === d || e.target.closest("[data-close]")) d.close(); }));   // backdrop click and Close button

const typing = t => t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable);
document.addEventListener("keydown", e => {
  if ($("#app").hidden) return;
  const mod = e.ctrlKey || e.metaKey, k = e.key, anyDialog = document.querySelector("dialog[open]");
  if (mod && !e.shiftKey && k.toLowerCase() === "k") { e.preventDefault(); return anyDialog && $("#palette").open ? $("#palette").close() : openPalette(); }
  if (mod && e.shiftKey && k.toLowerCase() === "o") { e.preventDefault(); return newConversation(); }
  if (mod && k === "/") { e.preventDefault(); return $("#help").open ? $("#help").close() : $("#help").showModal(); }
  if (anyDialog) return;
  if (mod && e.shiftKey && k.toLowerCase() === "c") { const last = Object.values(S.runs).filter(r => r.status === "done").pop(); if (last) { e.preventDefault(); navigator.clipboard && navigator.clipboard.writeText(answerText(last)); } return; }
  if (k === "Escape" && !e.shiftKey && S.busy && S.view === "chat" && !$("#side").classList.contains("open")) { e.preventDefault(); return stop(); }
  if (k === "Escape" && e.shiftKey) { e.preventDefault(); setView("chat"); return $("#input").focus(); }
  if (k === "/" && !typing(e.target) && !mod) { e.preventDefault(); setView("chat"); return $("#input").focus(); }
  if (k === "ArrowUp" && e.target === $("#input") && !$("#input").value && S.lastSent) { e.preventDefault(); $("#input").value = S.lastSent; autosize(); }
});
const _send = send; send = async function (text) { if (text && text.trim()) S.lastSent = text.trim(); return _send(text); };

/* phone: drag the sheet's grabber down to close it (no left-edge swipe: it fights the iOS back gesture) */
(() => {
  const side = $("#side"), grab = side.querySelector(".grabber"); let y0 = null, dy = 0;
  const start = e => { if (!matchMedia("(max-width: 640px)").matches) return; y0 = e.touches[0].clientY; dy = 0; side.style.transition = "none"; };
  const move = e => { if (y0 == null) return; dy = Math.max(0, e.touches[0].clientY - y0); side.style.transform = `translateY(${dy}px)`; };
  const end = () => { if (y0 == null) return; side.style.transition = ""; side.style.transform = ""; if (dy > 90) closeSide(); y0 = null; };
  for (const el of [grab, side.querySelector(".side-top")]) { el.addEventListener("touchstart", start, { passive: true }); el.addEventListener("touchmove", move, { passive: true }); el.addEventListener("touchend", end); el.addEventListener("touchcancel", end); }
})();
