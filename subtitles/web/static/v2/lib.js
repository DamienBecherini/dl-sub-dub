/* What every screen shares: the API, the state of the page, the translations.

   State: one plain object, changed only through `update`, read by the components through `useStore`. A component
   re-renders when the state changes; nothing else holds data, so what is shown is always what the state says. */
import { html, useEffect, useRef, useState } from "/static/vendor/preact-htm.js";

export { html };
/* global I18N */
// i18n.js is a classic script: its `const I18N` is a global lexical binding (not a property of window), read by name.
export const T = (key, params) => globalThis.T(key, params);
export const languageName = (code) => globalThis.languageName(code);

/* Words as a sentence in the page's language: "German, Spanish and Italian" ("allemand, espagnol et italien"). */
export function listOf(words) {
  /* global I18N */
  try { return new Intl.ListFormat(I18N.language, { type: "conjunction" }).format(words); } catch (e) { return words.join(", "); }
}
export const languagesOf = (codes) => listOf(codes.map((c) => languageName(c) || c));
export const capital = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// --- API --------------------------------------------------------------------------------------------------------

export async function api(method, path, body) {
  let r;
  try {
    r = await fetch(path, {
      method,
      headers: { "X-Language": I18N.language, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {               // no answer at all (the service stopped, or restarts): said plainly
    throw Object.assign(new Error(T("ui.service_down")), { data: {}, status: 0 });
  }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(d.error || `${r.status} ${r.statusText}`), { data: d, status: r.status });
  return d;
}

// --- state --------------------------------------------------------------------------------------------------------

const KEEP = "v2-page";                  // what survives a reload: the selection, the project open

let state = {
  selection: [],                         // before the project exists: [{source, title, duration, kind, origin, checked}]
  title: "",                             // name of the project to create
  location: { base: "", named: null },   // where it goes, as chosen ("" and null: the default, see `suggested`)
  suggested: null,                       // the default for the selection: {base, named, name} (POST /api/sources/location)
  stages: { download: true, subtitles: true, dubbing: false },
  projectId: null,
  project: null,                         // summary of the project once created (GET /api/projects/<id>)
  busy: "",                              // what is being done ("resolving"…), "" when nothing
  error: "",
  notes: [],                             // messages for the user
  config: null,                          // settings of the service (engines), read once: GET /api/config
  pending: [],                           // lines being read once the project exists (see select.js)
  view: "compact",                       // the list: "compact" (one row a video, the steps as marks) or "detailed" (boxes)
};
try { Object.assign(state, JSON.parse(localStorage.getItem(KEEP) || "{}")); } catch (e) { /* private mode: nothing kept */ }
state.busy = ""; state.error = ""; state.project = null; state.pending = []; state.config = null; state.suggested = null;
state.selection = state.selection.filter((i) => !i.pending);   // a line being read when the page was left is lost: add it again

const listeners = new Set();

export function getState() { return state; }

export function update(change) {
  state = { ...state, ...(typeof change === "function" ? change(state) : change) };
  try {
    const { selection, title, location, stages, projectId, view } = state;
    localStorage.setItem(KEEP, JSON.stringify({ selection, title, location, stages, projectId, view }));
  } catch (e) { /* not kept */ }
  for (const l of listeners) l(state);
}

export function useStore() {
  const [s, set] = useState(state);
  useEffect(() => { listeners.add(set); set(state); return () => listeners.delete(set); }, []);
  return s;
}

/* Runs an action with a busy label; its error is shown instead of being lost. */
export async function busy(label, action) {
  update({ busy: label, error: "" });
  try { return await action(); }
  catch (e) { update({ error: e.message }); return undefined; }
  finally { update({ busy: "" }); }
}

// --- formatting ---------------------------------------------------------------------------------------------------

export function duration(s) {
  if (!s) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")}` : m ? `${m} min ${String(r).padStart(2, "0")}` : `${r} s`;
}

export const isAddress = (s) => /^https?:\/\//.test(s);
/* The address a video comes from: its source, or for a file the one read in it or given by the user ("" unknown). */
export const originOf = (item) => (isAddress(item.source) ? item.source : item.facts?.address || "");

export const STAGES = ["download", "subtitles", "dubbing"];

/* The steps ticked for the project (those "Run" runs): the download only when there are addresses. */
export const tickedStages = (s, hasAddresses) => STAGES.filter((k) => s.stages[k] && (k !== "download" || hasAddresses));

/* Ticked for some of its steps only: its tick in the list shows half ticked. */
function partlyTicked(i) {
  const ticks = Object.values(i.stages).filter((st) => st.state !== "skipped").map((st) => st.selected);
  return i.selected && ticks.some((t) => !t);
}

/* The videos of the list, the same shape before and after the project exists. */
export function itemsOf(s) {
  if (s.project) {
    return [...s.project.items.map((i) => ({
      key: i.id, source: i.source, title: i.title || (i.video || i.source).split(/[\\/]/).pop(), duration: i.facts.duration,
      kind: isAddress(i.source) ? "address" : "file", origin: "", selected: i.selected, partial: partlyTicked(i),
      busy: Object.values(i.stages).some((st) => ["running", "paused"].includes(st.state)),
    })), ...s.pending];                                 // lines still being read go at the end
  }
  return s.selection.map((i) => ({ ...i, key: i.key || i.source, busy: false }));
}

/* What can be done now is decided by the service (project/rules.py) and comes with the project: for each step of a
   video, `item.stages[k]` holds `applies`, `lacks` (what it needs that is not there; `blocking`: no step will bring
   it), `left` and `ready` (ticked and to do; Run would take it), `actions` (the buttons of its line) and `runnable`;
   the video has `current` (the step its line acts on), `log_step`, `busy`, `locked` (its languages, while it is
   subtitled or dubbed), `spoken`, `dub_targets` and `chains`; the project has `p.rules` (status, locked, working,
   finished, run: {steps, open, resume}, steps: {step: {ready, waiting, why}}). The page shows them and works none
   of it out again from the states. */

/* The state of a project in words: created and not run yet, it is "ready", not "paused"; with nothing left, "done". */
export const stateKey = (p) => p.rules.status;

/* A help bubble (?) with its text (style.css, placed by i18n.js). */
/* Not a <button>: inside a <label> before its field, a button would be the control of the label, and hovering or clicking
   anywhere on the label would open the bubble. Still reachable with Tab (tabindex) and named for screen readers. */
export const Tip = ({ text }) => html`<span class="tip" role="button" tabindex="0" data-tip=${text} aria-label=${text}>?</span>`;

/* An action on the project that answers with the project. */
export async function act(path, body = {}) {
  const p = await busy("", () => api("POST", path, body));
  if (p) update({ project: p });
  return p;
}

/* A question before an action that cannot easily be undone: its title, its text, the button that confirms and Cancel.
   Escape, the × or a click outside cancel; the focus starts on Cancel (Enter does not confirm by mistake). */
/* A menu opened by its button (role=menu, the button with aria-haspopup inside `box`): on opening, the focus goes to
   its first item; ↑ ↓ Home End move among the items, Escape closes it and gives the focus back to its button, Tab
   closes it and goes on from its button, a click elsewhere closes it. An item chosen gives the focus back too.
   Returns [open, setOpen, box]. */
/* A popup (menu, bubble) moved sideways to stay inside `within` (default: the page), before it is drawn (from a
   useLayoutEffect). Not innerWidth: on a phone, what overflows widens it. */
export function keepInside(m, within) {
  if (!m) return;
  m.style.left = m.style.right = "";                     // its place from the style sheet, then moved (not translate: the page still widened)
  m.style.maxWidth = "";
  const z = m.currentCSSZoom || 1, e = (within || document.body).getBoundingClientRect();
  if (m.getBoundingClientRect().width > e.width - 8) m.style.maxWidth = `${(e.width - 8) / z}px`;   // never wider than its room
  const r = m.getBoundingClientRect();
  const dx = r.left < e.left + 4 ? e.left + 4 - r.left : r.right > e.right - 4 ? Math.max(e.left + 4 - r.left, e.right - 4 - r.right) : 0;
  if (!dx) return;
  const left = parseFloat(getComputedStyle(m).left) || 0;
  m.style.left = `${left + dx / z}px`;
  m.style.right = "auto";
}

export function useMenu() {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const items = () => [...(box.current?.querySelectorAll('[role^="menuitem"]:not([disabled])') || [])];   // menuitemradio too
    const button = () => box.current?.querySelector("[aria-haspopup]");
    requestAnimationFrame(() => items()[0]?.focus());
    const away = (e) => { if (!box.current?.contains(e.target)) setOpen(false); };
    const keys = (e) => {
      if (e.key === "Escape") { button()?.focus(); setOpen(false); return; }
      if (!box.current?.contains(document.activeElement)) return;
      if (e.key === "Tab") { button()?.focus(); setOpen(false); return; }      // the browser goes on from the button
      const list = items();
      const at = list.indexOf(document.activeElement);                         // -1: on the button
      const to = { ArrowDown: at + 1, ArrowUp: at < 0 ? list.length - 1 : at - 1, Home: 0, End: list.length - 1 }[e.key];
      if (to === undefined || !list.length) return;
      e.preventDefault();
      list[(to + list.length) % list.length].focus();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", keys);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", keys);
      const at = document.activeElement;                 // an item chosen: gone with the menu, the focus with it
      if (!at || at === document.body || box.current?.contains(at)) button()?.focus();
    };
  }, [open]);
  return [open, setOpen, box];
}

/* Tabs (role=tablist): ← → Home End go from tab to tab and show it; only the tab shown is in the Tab order. */
export function tabKeys(e) {
  const list = [...e.currentTarget.querySelectorAll('[role="tab"]')];
  const at = list.indexOf(document.activeElement);
  const to = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: list.length - 1 }[e.key];
  if (at < 0 || to === undefined) return;
  e.preventDefault();
  const tab = list[(to + list.length) % list.length];
  tab.focus();
  tab.click();
}

/* The language its site gives for a video (fr-FR → fr), to say where the language shown comes from. The language
   spoken in a video is `item.spoken`, the languages it will be dubbed in `item.dub_targets` (said by the service). */
export const siteLanguage = (item) => (item.facts?.site?.language || "").toLowerCase().split(/[-_]/)[0];

export function Confirm({ title, text, yes, onYes, onNo, other = "", onOther = null, danger = true }) {
  const ref = useRef(null);
  useEffect(() => { ref.current.showModal(); ref.current.querySelector("[data-no]").focus(); }, []);
  const answer = (fn) => { ref.current.close(); fn(); };
  return html`
    <dialog ref=${ref} class="confirm-dialog" role="alertdialog" aria-labelledby="confirm-title" aria-describedby="confirm-text"
            onCancel=${(e) => { e.preventDefault(); answer(onNo); }} onClick=${(e) => { if (e.target === ref.current) answer(onNo); }}>
      <div class="head dialog-bar">
        <h2 id="confirm-title">${title}</h2>
        <button type="button" class="close" aria-label=${T("ui.close")} onClick=${() => answer(onNo)}>×</button>
      </div>
      <p id="confirm-text">${text}</p>
      <div class="actions">
        <button type="button" class=${`primary ${danger ? "danger-primary" : ""}`} onClick=${() => answer(onYes)}>${yes}</button>
        ${other && html`<button type="button" class="secondary" onClick=${() => answer(onOther)}>${other}</button>`}
        <button type="button" class="secondary" data-no onClick=${() => answer(onNo)}>${T("ui.cancel")}</button>
      </div>
    </dialog>`;
}
