/* The workshop: one list, each video with its own boxes for the steps (download, subtitles, dubbing), its state in
   each one and the button of its next step; unfolded, the settings of the steps ticked on its line and its log. The
   settings of the project are folded above, and each step can be run for the videos ticked for it.

   Nothing is linked: a video can be subtitled before another is downloaded, an engine changed between two, a video
   added at any time. */
import { useEffect, useLayoutEffect, useRef, useState } from "/static/vendor/preact-htm.js";
import { DownloadBar, phaseText, StepLog, units } from "./dllog.js";
import { ProjectQuality } from "./download.js";
import { DubbingSettings, GpuSettings } from "./dubbing.js";
import { InfoPanel } from "./info.js";
import { Notices, OrderSwitch, StepsChoice } from "./common.js";
import { act, api, busy, duration, html, isAddress, keepInside, listOf, originOf, STAGES, T, tabKeys, Tip, update, useMenu, useStore } from "./lib.js";
import { AddBox, LastProject, ProjectHead, VideoList } from "./select.js";
import { actIcon, DETAILS, lackShort, lackText, LineButtons, PlayButton, playPath, roughly, roughlyLeft, workParts, workText } from "./stage.js";
import { ProjectLanguages, SubtitleSettings } from "./subtitles.js";

const CONTROLS = "button, input, select, a, label, textarea, .tip";       // a click on them does not fold the line

/* Said by the service (project/rules.py): whether a step concerns a video, the step its line acts on now
   (`item.current`), the step whose log is shown first (`item.log_step`). */
const applies = (item, step) => item.stages[step].applies;

/* Where the running step of a video stands, short enough for its cell: its percent (download, sentences, subtitles
   translated) and the time left; null when nothing is known yet (the cell then says "running"). */
function cellProgress(p, item, step) {
  if (step === "download") {
    const d = p.downloads?.[item.id];
    return d?.percent != null ? { percent: d.percent, text: `${Math.floor(d.percent)} %${d.eta ? " · " + d.eta : ""}` } : null;
  }
  const w = p.work?.[item.id];
  if (!w || w.queued) return null;
  const percent = w.progress?.total ? (100 * w.progress.done) / w.progress.total : null;
  const left = w.remaining_s ? roughly(w.remaining_s) : "";
  const text = [percent != null ? `${Math.floor(percent)} %` : "", left].filter(Boolean).join(" · ");
  return text ? { percent, text } : null;
}

/* Why a step ticked on a line cannot start yet, as a mark in its cell with its bubble (tapped on a phone):
   an hourglass when it only waits for the step before (Run does them in order), red when it cannot happen as things
   are (no translation to speak, the dubbing not set up). Null: nothing in the way. */
function waitOf(p, item, step) {
  const st = item.stages[step];
  return st.left && st.lacks ? { stop: st.blocking, text: lackText(p, item, step) } : null;
}

const hourglass = html`<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><path d="M5 3h14M5 21h14" /><path d="M7 3v3.5c0 1.3.6 2.5 1.7 3.3L12 12l3.3-2.2c1.1-.8 1.7-2 1.7-3.3V3" /><path d="M7 21v-3.5c0-1.3.6-2.5 1.7-3.3L12 12l3.3 2.2c1.1.8 1.7 2 1.7 3.3V21" /><path d="M9 19.6h6l-3-2.6z" fill="currentColor" stroke-width="1" /></svg>`;   // two bulbs, sand fallen in the lower one
const warning = html`<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path d="M12 3 2 20h20L12 3z" /><path d="M12 10v4M12 17v.5" /></svg>`;

const checkIcon = html`<svg class="done-mark" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10" /><path d="m7.5 12.5 3 3 6-6.5" /></svg>`;

/* The compact list: each step a small mark (its icon, coloured by its state, with its percent while it runs), its
   state in words in its bubble. */
const STEP_ICON = {
  download: html`<path d="M12 4v11M7 10l5 5 5-5M5 20h14" />`,
  subtitles: html`<rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 11h10M7 15h4M13 15h4" />`,
  dubbing: html`<rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />`,
};

/* The mark of a step of a video, under the icon of its column: an empty ring to do (orange when partly done), a ring
   filling while it runs, an hourglass, a warning, a pause, a green ring checked once done; the sentence of its bubble. */
const ring = (percent) => html`<svg class="state-ring" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <circle cx="12" cy="12" r="9" class="ring-back" />
  <circle cx="12" cy="12" r="9" class="ring-fill" pathLength="100" stroke-dasharray=${`${percent} 100`} transform="rotate(-90 12 12)" /></svg>`;
const emptyRing = html`<svg class="state-ring" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9" /></svg>`;
const pauseIcon = html`<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9" /><path d="M10 9v6M14 9v6" /></svg>`;

function stepState(p, item, step) {
  const st = item.stages[step];
  const name = T("v2.step." + step);
  const running = st.state === "running";
  const now = running ? cellProgress(p, item, step) : null;
  const wait = running ? null : waitOf(p, item, step);
  if (running) return { kind: "running", icon: ring(now?.percent ?? 8), text: now?.percent != null ? `${Math.floor(now.percent)} %` : "",
                        tip: `${name} : ${[step === "download" ? T("v2.state.running") : workText(p, item), now?.text].filter(Boolean).join(" · ")}` };
  if (st.state === "done") return { kind: "done", icon: checkIcon, tip: `${name} : ${T("v2.state.done")}` };
  if (wait) return { kind: wait.stop ? "error" : "waiting", icon: wait.stop ? warning : hourglass, tip: `${name} : ${wait.text}` };
  if (st.state === "error" || st.state === "cancelled") return { kind: "error", icon: warning, tip: `${name} : ${st.error || T("v2.state." + st.state)}` };
  if (st.state === "paused") return { kind: "paused", icon: pauseIcon, tip: `${name} : ${T("v2.state.paused")}` };
  if (step === "subtitles" && item.facts.transcribed) return { kind: "partial", icon: emptyRing, tip: `${name} : ${T("v2.work.translation_left")}` };
  if (step === "download" && item.facts.partial) return { kind: "todo", icon: emptyRing, tip: `${name} : ${T("v2.work.download_partial")}` };
  return { kind: "todo", icon: emptyRing, tip: `${name} : ${T("v2.state.todo")}` };
}

/* A step of a video in the compact list: its box under the box of its column, its mark under the icon (the mark
   alone has the bubble, so a tap reads the state without ticking). */
/* Under 380 px the states are icons: what each icon of the list means, under it (only those shown). */
const LEGEND = ["todo", "partial", "running", "waiting", "paused", "error", "done"];
function StateLegend({ p, steps }) {
  const kinds = new Set();
  for (const i of p.items) {
    for (const k of steps) {
      if (!applies(i, k)) continue;
      const s = stepState(p, i, k);
      if (["todo", "partial"].includes(s.kind) && !(i.selected && i.stages[k].selected)) continue;   // not ticked: no mark
      if (s.kind !== "done") kinds.add(s.kind);                                                      // done: the green chip says it
    }
  }
  const icon = { todo: emptyRing, partial: emptyRing, running: ring(60), waiting: hourglass, paused: pauseIcon, error: warning };
  const shown = LEGEND.filter((k) => kinds.has(k));
  if (!shown.length) return null;
  return html`<p class="state-legend" aria-hidden="true">${shown.map((k) => html`
    <span key=${k}><span class=${`step-mark mark-${k}`}>${icon[k]}</span>${T("v2.chip." + k)}</span>`)}</p>`;
}

/* The state of a step in a short word, on a phone's card ("done", "42 %", "waiting"); nothing for a step not ticked
   and still to do. */
function chipText(s, ticked) {
  if (s.kind === "running") return s.text || T("v2.chip.running");
  if (["todo", "partial"].includes(s.kind) && !ticked) return "";
  return T("v2.chip." + s.kind);
}

function CompactCell({ p, item, step, video }) {
  if (!applies(item, step)) return html`<span class="compact-cell"></span>`;
  const st = item.stages[step];
  const s = stepState(p, item, step);
  const name = T("v2.step." + step);
  const inProject = p.stages.includes(step);
  const label = T("v2.work.tick_step", { state: s.tip, name: video });
  return html`
    <span class=${`compact-cell ${inProject ? "" : "outside"} cell-${s.kind} ${item.selected && st.selected ? "on" : ""}`}>
      ${st.state === "done" ? html`<span class="box-gap" aria-hidden="true"></span>` : html`
      <input type="checkbox" checked=${item.selected && st.selected} disabled=${st.state === "running"}
             aria-label=${inProject ? label : `${label} · ${T("v2.work.outside", { step: name })}`} title=${inProject ? name : T("v2.work.outside", { step: name })}
             onChange=${(e) => act(`/api/projects/${p.id}/select`, { items: { [item.id]: e.target.checked }, stage: step })} />`}
      <svg class="cell-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${STEP_ICON[step]}</svg>
      <span class="cell-name" aria-hidden="true">${name}</span>
      <span class=${`tip step-mark mark-${s.kind}`} role="button" tabindex="0" data-tip=${s.tip} aria-label=${s.tip}>${s.icon}</span>
      <span class=${`tip cell-state state-${s.kind}`} role="button" tabindex="0" data-tip=${s.tip} aria-label=${s.tip}>${chipText(s, item.selected && st.selected)}</span>
      ${s.text && html`<span class="mark-text">${s.text}</span>`}
    </span>`;
}

/* Above the compact list: Tout (every video), and for each step its box (the step for every video it applies to:
   a dash when only some) and its icon with its name in its bubble. Same columns as the lines. On a phone, one row:
   the icons without their names, and a (?) that says them in their order. */
function CompactHead({ p, steps }) {
  const all = p.items.length > 0 && p.items.every((i) => i.selected);
  const some = p.items.some((i) => i.selected);
  const column = (k) => {
    const rows = p.items.filter((i) => applies(i, k) && i.stages[k].state !== "done");
    const on = rows.filter((i) => i.selected && i.stages[k].selected).length;
    return { rows, full: rows.length > 0 && on === rows.length, part: on > 0 && on < rows.length };
  };
  const tickColumn = (k, c) => act(`/api/projects/${p.id}/select`, { items: Object.fromEntries(c.rows.map((i) => [i.id, !c.full])), stage: k });
  return html`
    <div class="work-line compact compact-head">
      <div class="line-main">
        <span class="fold" aria-hidden="true"></span>
        <input type="checkbox" class="line-tick" checked=${all} indeterminate=${some && !all} aria-label=${T(all ? "ui.uncheck_all" : "ui.check_all")}
               title=${T(all ? "ui.uncheck_all" : "ui.check_all")} onChange=${() => tickAll(p, !all)} />
        <span class="work-title">${T("v2.list.all")}</span>
        <span class="step-pills" style=${`grid-template-columns: repeat(${steps.length}, var(--pill))`}>
          ${steps.map((k) => {
            const c = column(k);
            const label = T(c.full ? "v2.list.untick_step" : "v2.list.tick_step", { step: T("v2.step." + k) });
            return html`
              <span key=${k} class=${`compact-cell ${c.full || c.part ? "on" : ""}`}>
                <input type="checkbox" checked=${c.full} indeterminate=${c.part} disabled=${!c.rows.length} aria-label=${label} title=${label}
                       onChange=${() => tickColumn(k, c)} />
                <svg class="cell-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${STEP_ICON[k]}</svg>
                <span class="cell-name" aria-hidden="true">${T("v2.step." + k)}</span>
                <span class="tip step-head" role="button" tabindex="0" data-tip=${T("v2.step." + k)} aria-label=${T("v2.step." + k)}>
                  <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${STEP_ICON[k]}</svg></span>
              </span>`;
          })}
        </span>
        <span class="head-tip phone-only"><${Tip} text=${T("v2.list.head_tip", { steps: listOf(steps.map((k) => T("v2.step." + k))) })} /></span>
        <span class="line-buttons"></span>
        <button type="button" class="close" tabindex="-1" aria-hidden="true" disabled>×</button>
      </div>
    </div>`;
}

function WaitMark({ wait }) {
  return html`<span class=${`tip wait-mark ${wait.stop ? "stop" : ""}`} role="button" tabindex="0" data-tip=${wait.text} aria-label=${wait.text}
                    onClick=${(e) => e.preventDefault()}>${wait.stop ? warning : hourglass}</span>`;   // a tap shows the bubble, does not tick
}

function StepCell({ p, item, step, video }) {
  if (!applies(item, step)) return html`<span class="step-cell na" aria-hidden="true"></span>`;
  const st = item.stages[step];
  const name = T("v2.step." + step);
  const outside = !p.stages.includes(step);
  const running = st.state === "running";
  const now = running ? cellProgress(p, item, step) : null;
  const doing = running ? (step === "download" ? T("v2.state.running") : workText(p, item)) : "";
  if (st.state === "done") return html`
    <span class="step-cell done" title=${`${name} : ${T("v2.state.done")}`}>
      ${checkIcon}
      <span class="step-cell-name">${name}</span><span class="visually-hidden">${T("v2.state.done")}</span>
    </span>`;                                          // done: nothing left to tick (Run skips it), a mark instead of a box
  return html`
    <label class=${`step-cell ${st.selected && item.selected ? "" : "off"} ${outside ? "outside" : ""} ${now ? "measured" : ""}`}
           title=${st.error || doing || (outside ? T("v2.work.outside", { step: name }) : name)}>
      <input type="checkbox" checked=${item.selected && st.selected} disabled=${running} aria-label=${T("v2.work.tick_step", { state: name, name: video })}
             onChange=${(e) => act(`/api/projects/${p.id}/select`, { items: { [item.id]: e.target.checked }, stage: step })} />
      <span class="step-cell-name">${name}</span>
      ${waitOf(p, item, step) && !running
        ? html`<${WaitMark} wait=${waitOf(p, item, step)} />`
        : st.state === "todo" && !now
        ? html`<span class="visually-hidden">${T("v2.state.todo")}</span>`   // to do: nothing shown, the box ticked says it
        : html`<span class=${`chip state-${st.state}`}>${now ? now.text : T("v2.state." + st.state)}</span>`}
      ${now && now.percent != null && html`
        <span class="cell-bar" role="progressbar" aria-label=${doing} aria-valuemin="0" aria-valuemax="100" aria-valuenow=${Math.floor(now.percent)}>
          <span style=${`width: ${Math.min(100, now.percent)}%`}></span></span>`}
    </label>`;
}

/* The unfolded line: Information (what the video file holds, once there is one), one tab per step ticked on the line
   (its settings for this video), and the log. Each tab has the icon of its step before its name; on a phone only the
   icon, except the current tab (five names do not fit on one row, and the icons are those of the columns). */
function ItemTabs({ p, item, first }) {
  // ticked or not: its settings are there; a file whose address is known (or asked from its menu) has Download too
  const fromSite = !isAddress(item.source) && Boolean(item.video) && (Boolean(originOf(item)) || first === "download");
  const steps = STAGES.filter((k) => (applies(item, k) || (k === "download" && fromSite)) && DETAILS[k]);
  const running = STAGES.some((k) => item.stages[k].state === "running");
  const errored = STAGES.some((k) => item.stages[k].state === "error");
  const info = Boolean(item.video);
  const [tab, setTab] = useState(first || (running || errored ? "log" : info ? "info" : steps[0] || "log"));
  const [logOf, setLogOf] = useState(item.log_step);
  const stepNow = item.log_step;
  useEffect(() => setLogOf(stepNow), [stepNow]);           // another step starts (the dubbing after the subtitles): its log is shown
  const tabs = [...(info ? [["info", T("v2.info.tab")]] : []), ...steps.map((k) => [k, T("v2.step." + k)]), ["log", T("v2.dlp.tab_log")]];
  const Details = tab === "info" ? InfoPanel : DETAILS[tab];
  const withLog = STAGES.filter((k) => applies(item, k) && !["todo", "skipped"].includes(item.stages[k].state));
  const id = (key) => `tab-${item.id}-${key}`;
  return html`
    <div class="details-tabs">
      <div class="tabs" role="tablist" aria-label=${item.title || T("v2.work.unfold")} onKeyDown=${tabKeys}>
        ${tabs.map(([key, label]) => html`
          <button type="button" role="tab" id=${id(key)} aria-selected=${tab === key} aria-controls=${id("panel")}
                  tabindex=${tab === key ? 0 : -1} class=${`tab ${tab === key ? "current" : ""}`} title=${label}
                  onClick=${() => setTab(key)}>${actIcon(key)}<span class="tab-label">${label}</span></button>`)}
      </div>
      <div role="tabpanel" id=${id("panel")} aria-labelledby=${id(tab)}>
      ${tab === "log"
        ? html`<div class="details">
            ${withLog.length > 1 && html`
              <span class="mode-switch log-steps" role="group" aria-label=${T("v2.dlp.tab_log")}>${withLog.map((k) => html`
                <button type="button" aria-pressed=${k === logOf} class=${k === logOf ? "current" : ""} onClick=${() => setLogOf(k)}>${T("v2.step." + k)}</button>`)}</span>`}
            <${StepLog} key=${logOf} p=${p} item=${item} step=${logOf} running=${item.stages[logOf].state === "running"} />
          </div>`
        : Details && html`<${Details} p=${p} item=${item} />`}
      </div>
    </div>`;
}

/* The settings this video chose for itself (not the project's), with the tab where each one is changed. */
const OWN_TAB = { language: "subtitles", languages: "subtitles", transcription: "subtitles", translator: "subtitles",
                  max_height: "download", audio: "download", site_subtitles: "download", dub_language: "dubbing",
                  mkv: "dubbing", replace: "dubbing" };
function ownSettings(item) {
  return Object.entries(item.overrides || {})
    .filter(([k, v]) => OWN_TAB[k] && v !== null && v !== "" && !(Array.isArray(v) && !v.length))
    .map(([k]) => k);
}

/* The ▾ of a line: everything that can be done for this video alone, whatever its next step (a language added to a
   video done, a dubbing on top of its subtitles, a download again at another quality, tracks to add, open it). */
function LineMenu({ p, item, openTab, alone = false }) {
  const [open, setOpen, box] = useMenu();
  const st = (k) => item.stages[k];
  const run = (k) => { setOpen(false); act(`/api/projects/${p.id}/items/${item.id}/run`, { stage: k }); };
  const play = (path) => { setOpen(false); busy("", () => api("POST", "/api/open", { path, play: true })); };
  const folder = () => { setOpen(false); busy("", () => api("POST", "/api/open", { path: item.video })); };
  const entries = [];
  // several steps in a row for this video (item.chains, said by the service): those left, in their order
  const chains = item.chains;
  const runAll = (ks) => { setOpen(false); act(`/api/projects/${p.id}/items/${item.id}/run`, { stages: ks }); };
  for (const ks of chains) {
    entries.push({ label: ks.map((k, n) => n ? T("v2.chain." + k) : T("v2.line.run." + k)).join(" + "), help: T("v2.chain.help"),
                   go: () => runAll(ks) });
  }
  if (chains.length) entries.push({ sep: true });
  if (isAddress(item.source)) {
    const done = st("download").state === "done";
    entries.push({ label: T(done ? "v2.menu.download_again" : "v2.line.run.download"), help: done ? T("v2.menu.download_again_help") : "",
                   disabled: !st("download").runnable, go: () => run("download") });
    if (done) entries.push({ label: T("v2.menu.tracks"), help: T("v2.menu.tracks_help"), go: () => { setOpen(false); openTab("download"); } });
  } else if (item.video) {                     // a file: tracks of the site it comes from, or its address to give
    entries.push({ label: T(originOf(item) ? "v2.menu.site_tracks" : "v2.menu.give_origin"), help: T("v2.menu.site_tracks_help"),
                   go: () => { setOpen(false); openTab("download"); } });
  }
  if (item.video) {
    const sub = st("subtitles").state;
    entries.push({ label: T(sub === "done" ? "v2.menu.subtitles_again" : item.facts.transcribed ? "v2.line.translate_left" : "v2.line.run.subtitles"),
                   help: sub === "done" ? T("v2.menu.subtitles_again_help") : "", disabled: !st("subtitles").runnable, go: () => run("subtitles") });
    const lack = st("dubbing").lacks;                    // what it needs that is not there, said in the entry
    entries.push({ label: T(st("dubbing").state === "done" ? "v2.menu.dub_again" : "v2.line.run.dubbing"), help: lack ? lackShort(lack) : "",
                   disabled: !st("dubbing").runnable, go: () => run("dubbing") });
    entries.push({ sep: true });
    entries.push({ label: T("v2.menu.play_video"), go: () => play(item.video) });
    if (item.facts.muxed && !/\.mkv$/i.test(item.video)) entries.push({ label: T("v2.menu.play_mkv"), go: () => play(playPath(item, "dubbing")) });
    entries.push({ label: T("v2.menu.folder"), go: folder });
  }
  const others = entries.length;
  const busyLine = item.busy;
  entries.push({ sep: true, phone: true });
  entries.push({ label: T("v2.menu.settings"), phone: true, go: () => { setOpen(false); openTab(null); } });
  entries.push({ label: T("v2.list.remove"), phone: true, danger: true, disabled: busyLine, go: () => { setOpen(false); removeItem(p, item); } });
  return html`
    <span class=${`line-menu ${others ? "" : "phone-only"}`} ref=${box}>
      <button type="button" class=${`small secondary line-more ${alone ? "alone" : ""}`} aria-haspopup="menu" aria-expanded=${open}
              title=${T("v2.menu.title")} aria-label=${T("v2.menu.title")} onClick=${() => setOpen(!open)}>
        ${alone ? "⋯" : html`<span class="split-glyph">▾</span><span class="dots-glyph">⋯</span>`}</button>
      ${open && html`
        <div class="step-menu line-menu-list" role="menu" aria-label=${T("v2.menu.title")}>
          ${entries.map((e, n) => e.sep ? html`<hr key=${n} class=${e.phone ? "phone-only" : ""} />` : html`
            <button key=${n} type="button" role="menuitem" class=${`${e.phone ? "phone-only" : ""} ${e.danger ? "danger" : ""}`}
                    disabled=${e.disabled} onClick=${e.go}>
              <span>${e.label}</span>${e.help && html`<span class="menu-help">${e.help}</span>`}
            </button>`)}
        </div>`}
    </span>`;
}

/* Under a compact line at work, one row per step running (a download beside a dubbing is possible): the step, what
   it does and how far, the time left at the right, a thin bar. The lines at rest keep a single row. */
function Activity({ p, item }) {
  const rows = STAGES.filter((k) => item.stages[k].state === "running").map((k) => {
    if (k === "download") {
      const d = p.downloads?.[item.id];
      return { k, what: d ? [phaseText(d), d.size && units(d.size), d.speed && units(d.speed)].filter(Boolean).join(" · ") : T("v2.dlp.starting"),
               left: d?.eta ? T("v2.dlp.eta", { eta: d.eta }) : "", percent: d?.percent };
    }
    const w = workParts(p, item) || { what: T("v2.state.running"), progress: "", left: "" };
    const now = cellProgress(p, item, k);
    return { k, what: [w.what, w.progress].filter(Boolean).join(" · "), left: w.left, percent: now?.percent };
  });
  if (!rows.length) return null;
  return html`<div class="line-activity">${rows.map((r) => html`
    <div class="activity-row" key=${r.k}>
      <strong>${T("v2.step." + r.k)}</strong><span class="activity-what">${r.what}</span>
      ${r.left && html`<span class="activity-left">${r.left}</span>`}
      ${r.percent != null && html`
        <span class="activity-bar" role="progressbar" aria-label=${`${T("v2.step." + r.k)} : ${r.what}`} aria-valuemin="0"
              aria-valuemax="100" aria-valuenow=${Math.floor(r.percent)}><span style=${`width: ${Math.min(100, r.percent)}%`}></span></span>`}
    </div>`)}</div>`;
}

/* The settings this video chose for itself: a small icon after its title, which ones in its bubble; a click shows
   them in the tab of their step. */
function OwnMark({ own, open }) {
  const [shown, setShown, box] = useMenu();
  const pop = useRef(null);
  useLayoutEffect(() => keepInside(pop.current, pop.current?.closest(".work-items")), [shown]);   // before it is drawn: it flashed
  const title = T("v2.own.title", { list: listOf(own.map((k) => T("v2.own." + k))) });
  return html`
    <span class="own-wrap" ref=${box}>
      <button type="button" class="own-mark" title=${shown ? "" : title} aria-label=${title} aria-haspopup="menu" aria-expanded=${shown}
              onClick=${() => setShown(!shown)}>
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></svg>
      </button>
      ${shown && html`
        <div class="step-menu own-pop" ref=${pop} role="menu" aria-label=${title}>
          <p>${title}</p>
          <button type="button" role="menuitem" onClick=${() => { setShown(false); open(); }}>${T("v2.own.see")}</button>
        </div>`}
    </span>`;
}

async function removeItem(p, item) {
  const r = await busy("", () => api("POST", `/api/projects/${p.id}/remove`, { ids: [item.id] }));
  if (r) update({ project: r.project });
}

function Line({ p, item, steps, compact }) {
  const [open, setOpen] = useState(false);
  const [first, setFirst] = useState(null);            // the tab to show when the line is unfolded from a mark
  const [asked, setAsked] = useState(0);               // each request shows its tab, even the same one again (another was clicked since)
  const show = (tab) => { setFirst(tab); setAsked((n) => n + 1); setOpen(true); };
  const own = ownSettings(item);
  const name = item.title || (item.video || item.source).split(/[\\/]/).pop();
  const step = item.current;
  const failed = STAGES.map((k) => item.stages[k]).find((st) => st.error && ["error", "cancelled"].includes(st.state));
  const fold = (e) => { if (!e.target.closest(CONTROLS)) setOpen(!open); };
  const busyLine = item.busy;                              // being worked on: it stays
  const buttons = html`
    <span class="line-buttons">
      <span class="line-act">${step && item.stages[step].state === "done" ? html`<${PlayButton} item=${item} step=${step} icons=${compact} />`
        : step && html`<${LineButtons} p=${p} item=${item} step=${step} icons=${compact} />`}</span>
      <${LineMenu} p=${p} item=${item} alone=${!step} openTab=${show} />
    </span>`;
  if (compact) return html`
    <li class=${`item work-line compact ${item.selected ? "" : "unticked"} ${open ? "open" : ""}`}>
      <div class="line-main foldable" onClick=${fold}>
        <button type="button" class="fold" aria-expanded=${open} title=${T(open ? "v2.dl.fold" : "v2.work.unfold")} aria-label=${T(open ? "v2.dl.fold" : "v2.work.unfold")}
                onClick=${() => { setFirst(null); setOpen(!open); }}>${open ? "▾" : "▸"}</button>
        <input type="checkbox" class="line-tick" checked=${item.selected} disabled=${busyLine} aria-label=${T("v2.work.tick_line", { name })}
               title=${T("v2.work.tick_line_title")} onChange=${(e) => act(`/api/projects/${p.id}/select`, { items: { [item.id]: e.target.checked } })} />
        <span class="work-title">
          <span class="item-title" title=${item.video || item.source}>${name}</span>
          ${own.length > 0 && html`
            <${OwnMark} own=${own} open=${() => show(OWN_TAB[own[0]])} />`}
          <span class="cmeta">
            <span class=${`kind ${isAddress(item.source) ? "address" : "file"}`}>${T("v2.kind." + (isAddress(item.source) ? "address" : "file"))}</span>
            ${item.facts.missing && html`<span class="kind missing" title=${T("v2.line.missing_title", { path: item.video })}>${T("v2.line.missing")}</span>`}
            ${item.facts.duration ? html`<span class="cmeta-duration">${duration(item.facts.duration)}</span>` : ""}
          </span>
        </span>
        <span class="step-pills" style=${`grid-template-columns: repeat(${steps.length}, var(--pill))`}>
          ${steps.map((k) => html`<${CompactCell} key=${k} p=${p} item=${item} step=${k} video=${name} />`)}</span>
        ${buttons}
        <button type="button" class="close" disabled=${busyLine} title=${T("v2.list.remove")} aria-label=${T("v2.list.remove")}
                onClick=${() => removeItem(p, item)}>×</button>
        <${Activity} p=${p} item=${item} />
      </div>
      ${failed && html`<p class="item-error">${failed.error}</p>`}
      ${open && html`<${ItemTabs} key=${`${first || ""}:${asked}`} p=${p} item=${item} first=${first} />`}
    </li>`;
  return html`
    <li class=${`item work-line ${item.selected ? "" : "unticked"} ${open ? "open" : ""}`}>
      <div class="line-main foldable" onClick=${fold}>
        <button type="button" class="fold" aria-expanded=${open} title=${T(open ? "v2.dl.fold" : "v2.work.unfold")} aria-label=${T(open ? "v2.dl.fold" : "v2.work.unfold")}
                onClick=${() => { setFirst(null); setOpen(!open); }}>${open ? "▾" : "▸"}</button>
        <span class="work-title">
          <span class="item-title" title=${item.video || item.source}>${name}</span>
          ${own.length > 0 && html`
            <${OwnMark} own=${own} open=${() => show(OWN_TAB[own[0]])} />`}
        </span>
        <button type="button" class="close" disabled=${busyLine} title=${T("v2.list.remove")} aria-label=${T("v2.list.remove")}
                onClick=${() => removeItem(p, item)}>×</button>
        <span class="line-sub">
          <span class="line-meta">
            <span class=${`kind ${isAddress(item.source) ? "address" : "file"}`}>${T("v2.kind." + (isAddress(item.source) ? "address" : "file"))}</span>
            ${item.facts.missing && html`<span class="kind missing" title=${T("v2.line.missing_title", { path: item.video })}>${T("v2.line.missing")}</span>`}
            <span class="item-duration">${duration(item.facts.duration)}</span>
          </span>
          <span class="step-cells" style=${`grid-template-columns: repeat(${steps.length}, var(--cell))`}>
            ${steps.map((k) => html`<${StepCell} key=${k} p=${p} item=${item} step=${k} video=${name} />`)}</span>
          ${buttons}
        </span>
      </div>
      ${item.stages.download.state === "running" && html`<${DownloadBar} d=${p.downloads?.[item.id]} />`}
      ${p.work?.[item.id] && html`<p class="work-now help">${workText(p, item)}</p>`}
      ${failed && html`<p class="item-error">${failed.error}</p>`}
      ${open && html`<${ItemTabs} key=${`${first || ""}:${asked}`} p=${p} item=${item} first=${first} />`}
    </li>`;
}

/* For a step run alone (p.rules.steps, said by the service): the videos it would take (`ready`), those ticked for it
   that it cannot take yet (`waiting`) and why: a step run alone does not run the one before (subtitles need the video
   downloaded, the dubbing its subtitles translated). */
const waitingFor = (p, step) => ({ n: p.rules.steps[step].waiting, why: "v2.stage.wait." + p.rules.steps[step].why });

/* The menu of the ▾ next to Run: one step only, for the videos ticked for it ("Only subtitle (2 to do)"). */
export function StepMenu({ p, close }) {
  const hasAddresses = p.items.some((i) => isAddress(i.source));
  const go = (k) => { close(); act(`/api/projects/${p.id}/start`, { stages: [k], only: true }); };
  return html`
    <div class="step-menu" role="menu" aria-label=${T("v2.nav.one_step")}>
      ${STAGES.filter((k) => k !== "download" || hasAddresses).map((k, n) => {
        const todo = p.rules.steps[k].ready;
        const waiting = waitingFor(p, k);
        const why = waiting.n > 0 ? T(waiting.why, { n: waiting.n }) : "";
        return html`
          <button type="button" role="menuitem" disabled=${!todo} aria-label=${[T(`v2.stage.only.${k}`), T("v2.stage.todo", { todo }), why].filter(Boolean).join(", ")}
                  title=${why || T("v2.stage.run_title", { step: T("v2.step." + k) })} onClick=${() => go(k)}>
            <span>${T(`v2.stage.only.${k}`)} <span class="menu-count">(${T("v2.stage.todo", { todo })}${waiting.n > 0 && html` · ${waiting.n} <span class=${`menu-wait ${waiting.why === "v2.stage.wait.dub_setup" ? "stop" : ""}`}>${waiting.why === "v2.stage.wait.dub_setup" ? warning : hourglass}</span>`})</span></span>
          </button>`;
      })}
    </div>`;
}

const tickAll = (p, on) => act(`/api/projects/${p.id}/select`, { items: Object.fromEntries(p.items.map((i) => [i.id, on])) });

/* Once started, where the project stands above its list: the videos ticked that went through every step of the
   project, and the time left of the work under way. */
function Summary({ p, view }) {
  const rows = p.items.filter((i) => i.selected);
  const finished = p.rules.finished;
  const all = rows.length === p.items.length && rows.length > 0;
  return html`
    <div class="list-head">
      <span>
        ${T("v2.list.count", { n: p.items.length, ticked: rows.length })}
        ${p.started && html` · <strong>${T("v2.progress.done", { done: finished, n: rows.length })}</strong>`}
        ${p.started && p.remaining_s ? html` <span class="help">· ${roughlyLeft(p.remaining_s)}</span>` : ""}
      </span>
      ${p.items.length > 0 && view === "detailed" && html`
        <button type="button" class="secondary small" onClick=${() => tickAll(p, !all)}>${T(all ? "ui.uncheck_all" : "ui.check_all")}</button>`}
      ${p.items.length > 0 && html`
        <span class="mode-switch view-switch" role="group" aria-label=${T("v2.view.label")}>
          ${["compact", "detailed"].map((v) => html`
            <button type="button" aria-pressed=${view === v} class=${view === v ? "current" : ""}
                    title=${T(`v2.view.${v}_title`)} onClick=${() => update({ view: v })}>${T(`v2.view.${v}`)}</button>`)}
        </span>`}
    </div>`;
}

/* Adding videos: open by default, while the list is being made (the first videos added do not fold it) and when a
   project is opened (the page reloaded, Resume, Open a project); folded by its button, or once work starts (Run, one
   step from ▾, an action of a line: a project opened while it works comes folded); its button opens it again, and a
   file dragged over the page too. In the state of the page (`adding`, null: open), so that it survives the lines. */
function Adding({ p }) {
  const has = p?.items.length > 0;
  const open = useStore().adding ?? true;
  const setOpen = (adding) => update({ adding });
  const working = !!p?.rules.working;                    // the project, or a line on its own
  useEffect(() => { if (working) setOpen(false); }, [working]);
  useEffect(() => {
    if (!has || open) return undefined;
    const enter = (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) setOpen(true); };
    document.addEventListener("dragenter", enter);
    return () => document.removeEventListener("dragenter", enter);
  }, [has, open]);
  if (!has) return html`<${AddBox} />`;
  return open
    ? html`<div class="adding"><${AddBox} />
        <button type="button" class="secondary small adding-close" onClick=${() => setOpen(false)}>${T("v2.add.fold")}</button></div>`
    : html`<div class="adding"><button type="button" class="secondary" onClick=${() => setOpen(true)}>${T("v2.add.unfold")}</button></div>`;
}

/* The settings of the project. Always shown under its title, the two set for each project: its languages and the
   order of the work; unfolded, the others from the most used to the rarest. */
function ProjectSettings({ s, p, hasAddresses }) {
  const [open, setOpen] = useState(false);
  const head = useRef(null);
  const fold = () => { setOpen(false); head.current?.focus(); head.current?.scrollIntoView({ block: "nearest" }); };
  return html`
    <section class=${`project-settings ${open ? "open" : ""}`} aria-label=${T("v2.work.settings")}>
      <button type="button" class="ps-head" ref=${head} aria-expanded=${open} onClick=${() => setOpen(!open)}>
        <span class="ps-arrow" aria-hidden="true">${open ? "▾" : "▸"}</span>${T("v2.work.settings")}
        ${!open && html`<span class="ps-hint">${T("v2.work.settings_all")}</span>`}</button>
      <div class="ps-quick">
        <div class="ps-field ps-languages">
          <span class="field-label">${T("v2.quick.languages")} <${Tip} text=${T("v2.quick.languages_title")} /></span>
          <${ProjectLanguages} p=${p} />
        </div>
        <div class="ps-field">
          <span class="field-label">${T("v2.order.label")}</span>
          <${OrderSwitch} p=${p} />
        </div>
      </div>
      ${open && html`
        <div class="ps-more">
          <div class="settings-section"><h2>${T("v2.project.steps")}</h2>
            <p class="help">${T("v2.work.steps_help")}</p>
            <${StepsChoice} s=${s} hasAddresses=${hasAddresses} locked=${p.state === "running"} /></div>
          <div class="settings-section"><h2>${T("v2.step.dubbing")}</h2><${DubbingSettings} p=${p} /></div>
          <div class="settings-section"><h2>${T("v2.step.subtitles")}</h2><${SubtitleSettings} p=${p} cfg=${s.config} /></div>
          ${hasAddresses && html`<div class="settings-section"><h2>${T("v2.step.download")}</h2><${ProjectQuality} p=${p} /></div>`}
          <div class="settings-section"><h2>${T("v2.gpu.label")}</h2><div class="screen-settings grid-settings"><${GpuSettings} p=${p} /></div></div>
          <button type="button" class="secondary small fold-settings" onClick=${fold}>${T("v2.work.settings_fold")}</button>
        </div>`}
    </section>`;
}

export function WorkshopScreen({ s, items }) {
  const p = s.project;
  const hasAddresses = items.some((i) => i.kind === "address");
  return html`
    <section class="card screen workshop">
      ${!s.projectId && html`<${LastProject} />`}
      <${ProjectHead} s=${s} items=${items} />
      <${Adding} p=${p} />
      ${p && html`<${ProjectSettings} s=${s} p=${p} hasAddresses=${hasAddresses} />`}
      <${Notices} p=${p} />
      ${p
        ? html`
          <${Summary} p=${p} view=${s.view} />
          ${s.view !== "detailed" && p.items.length > 0 && html`<${CompactHead} p=${p} steps=${STAGES.filter((k) => k !== "download" || hasAddresses)} />`}
          <ul class=${`items work-items ${s.config?.web?.compact_meta === "inline" ? "" : "meta-below"}`}>
            ${p.items.map((i) => html`<${Line} key=${i.id} p=${p} item=${i} steps=${STAGES.filter((k) => k !== "download" || hasAddresses)} compact=${s.view !== "detailed"} />`)}
          </ul>
          ${s.view !== "detailed" && html`<${StateLegend} p=${p} steps=${STAGES.filter((k) => k !== "download" || hasAddresses)} />`}
          ${s.pending.length > 0 && html`<${VideoList} items=${s.pending} />`}`
        : html`<${VideoList} items=${items} />`}
    </section>`;
}
