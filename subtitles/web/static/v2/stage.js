/* What a video does in one step, shared by the two modes: the buttons of its line for that step (Run this video, this
   step, now; Retry after an error or a cancellation; Translate when its translation waits for the go-ahead; Cancel;
   Open once done), the controls of the running project (Pause, Stop), and the details of each step. */
import { DownloadDetails } from "./download.js";
import { DubbingDetails, oneAtATime } from "./dubbing.js";
import { useState } from "/static/vendor/preact-htm.js";
import { act, api, busy, Confirm, duration, html, languageName, languagesOf, T, Tip } from "./lib.js";
import { costText, SubtitleDetails } from "./subtitles.js";

export const DETAILS = { download: DownloadDetails, subtitles: SubtitleDetails, dubbing: DubbingDetails };

/* Pause (after the videos in progress) and Stop (now: the videos in progress are interrupted, the transcription server
   and the AI stop; what is finished is kept), next to the button that started the work (bottom of the page); a paused
   or stopped project is resumed by that same button ("Resume: …"). */
export function RunControls({ p }) {
  if (p?.state !== "running") return null;
  return html`
    <button type="button" class="secondary" title=${T("v2.project.pause_after_title")} onClick=${() => act(`/api/projects/${p.id}/suspend`)}>${T("v2.project.pause_after")}</button>
    <button type="button" title=${T("v2.project.pause_now_title")} onClick=${() => act(`/api/projects/${p.id}/stop`)}>${T("v2.project.pause_now")}</button>`;
}

/* Paused after the videos in progress, which still run: say so, and offer to stop them now. */
export function Finishing({ p }) {
  const running = p && p.state === "suspended" ? p.items.filter((i) => ["subtitles", "dubbing"].some((k) => i.stages[k].state === "running")) : [];
  if (!running.length) return null;
  return html`
    <span class="help">${T("v2.project.finishing", { n: running.length })}</span>
    <button type="button" class="small" title=${T("v2.project.pause_now_title")} onClick=${() => act(`/api/projects/${p.id}/stop`)}>${T("v2.project.stop_now")}</button>`;
}

/* What the task of a video is doing, in parts: {what: "Dubbing (voice 2 of 3)", progress: "900/2136",
   left: "about 14 min"}; null without a task. Several languages dubbed: which voice it is on. */
export function workParts(p, item) {
  const w = p.work?.[item.id];
  if (!w) return null;
  if (w.queued) return { what: T("v2.work.queued"), progress: "", left: "" };
  const known = ["source", "transcription", "segmentation", "translation", "files", "dubbing", "dub_mix", "mux", "model", "sample"];
  const voice = w.voice && ["dubbing", "dub_mix"].includes(w.step) ? ` (${T("v2.work.voice", { n: w.voice[0], total: w.voice[1] })})` : "";
  return { what: (known.includes(w.step) ? T("v2.work.step." + w.step) : (w.step || T("v2.state.running"))) + voice,
           progress: w.progress?.total && w.step !== "dub_mix" ? `${w.progress.done}/${w.progress.total}` : "",
           left: w.remaining_s ? T("v2.work.left", { t: duration(w.remaining_s) }) : "" };
}

/* The same in one sentence: "Transcription · about 3 min", "Translation 45/120". */
export function workText(p, item) {
  const w = workParts(p, item);
  return w ? w.what + (w.progress ? " " + w.progress : "") + (w.left ? " · " + w.left : "") : "";
}

/* Plays the video in the default player of the system (the subtitles next to it are loaded by the player); once
   dubbed, the MKV with every track if one was made next to the video. */
export const playPath = (item, step) => (step === "dubbing" && item.facts.muxed && !/\.mkv$/i.test(item.video)
  ? item.video.replace(/\.[^./\\]+$/, "") + ".multi.mkv" : item.video);

/* The action of a line as an icon (compact list): its name in its bubble and for screen readers. */
const ACT_ICON = {
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14" />',
  subtitles: '<rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 11h10M7 15h4M13 15h4" />',
  dubbing: '<rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />',
  play: '<path d="M7 4l13 8-13 8z" />',
  cancel: '<rect x="6" y="6" width="12" height="12" rx="1.5" />',
  retry: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" />',
  info: '<circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.4v.2" />',                 // the tabs of a line
  log: '<path d="M4 6h16M4 10h16M4 14h10M4 18h7" />',
};
export const actIcon = (k) => html`<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" dangerouslySetInnerHTML=${{ __html: ACT_ICON[k] }}></svg>`;

export function PlayButton({ item, step, icons = false }) {
  if (!item.video) return null;
  const path = playPath(item, step);
  const play = () => busy("", () => api("POST", "/api/open", { path, play: true }));
  if (icons) return html`<button type="button" class="small icon-act" title=${T("v2.line.play_title")} aria-label=${T("v2.line.play")} onClick=${play}>${actIcon("play")}</button>`;
  return html`<button type="button" class="small secondary" title=${T("v2.line.play_title")} onClick=${play}>${T("v2.line.play")}</button>`;
}

/* What a step of a video lacks (`lacks`, said by the service), in a few words for its line and in a sentence for its
   bubble. The dubbing speaks the translated subtitles and never starts them (a translation may be paid). */
const LACK_SHORT = { download: "v2.line.needs_download", subtitles: "v2.line.needs_subtitles", no_translation: "v2.line.needs_translation",
                     dub_setup: "v2.line.needs_dub_setup", nothing_to_dub: "v2.line.needs_other_language" };
export const lackShort = (lack) => T(LACK_SHORT[lack]);
export function lackText(p, item, step) {
  const lack = item.stages[step].lacks;
  const inOrder = p.stages.includes(step) ? " " + T("v2.wait.in_order") : "";      // Run does the steps in order
  return { download: () => T("v2.wait.download") + inOrder, subtitles: () => T("v2.wait.subtitles") + inOrder,
           no_translation: () => T("v2.wait.no_translation"), dub_setup: () => T("v2.wait.dub_setup"),
           nothing_to_dub: () => T("v2.wait.nothing_to_dub", { lang: languageName(item.spoken) || item.spoken }) }[lack]();
}

/* The time the steps ticked would take, before Run: for each step, the videos ticked with this step to do, by their
   duration and the speed of this machine (measured, otherwise announced in the settings; p.rates). The downloads and
   the videos without a known duration are not counted, and said so. Null: nothing to estimate. */
export function runEstimate(p, steps) {
  if (!p?.rates) return null;
  const length = (i) => Number(i.facts?.duration || i.facts?.site?.duration || 0);
  const parts = {}, unknown = new Set();
  for (const k of steps) {
    if (k === "download" || (k === "dubbing" && !p.dubbing?.available)) continue;
    const rate = p.rates[k];
    let s = 0;
    for (const i of p.items) {
      if (!i.stages[k].left) continue;
      if (length(i) && rate) s += length(i) * rate * (k === "dubbing" ? i.dub_targets.length : 1); else unknown.add(i.id);   // a dubbing: each voice
    }
    if (k === "subtitles") s /= Math.max(1, p.concurrency || 1);     // several videos at once
    if (s) parts[k] = s;
  }
  const total = Object.values(parts).reduce((a, b) => a + b, 0);
  return total ? { total, parts, unknown: unknown.size, download: steps.includes("download") } : null;
}

/* "about 12 min" for the time left of a project. */
export const roughlyLeft = (s) => T("v2.work.left", { t: roughly(s) });

/* "≈ 1 h 05", "≈ 12 min": rounded up to the minute. */
export function roughly(s) {
  const m = Math.max(1, Math.ceil(s / 60)), h = Math.floor(m / 60);
  return h ? `${h} h ${String(m % 60).padStart(2, "0")}` : `${m} min`;
}

/* Next to Run: the time estimated, its detail in the bubble. */
export function RunEstimate({ p, steps }) {
  const e = runEstimate(p, steps);
  if (!e) return null;
  const detail = [
    ...Object.entries(e.parts).map(([k, s]) => T("v2.estimate.part", { step: T("v2.step." + k), t: roughly(s) })),
    ...(e.download ? [T("v2.estimate.no_download")] : []),
    ...(e.unknown ? [T("v2.estimate.unknown", { n: e.unknown })] : []),
    T("v2.estimate.how"),
  ].join(" · ");
  return html`<span class="help run-estimate">${T("v2.estimate.total", { t: roughly(e.total) })} <${Tip} text=${detail} /></span>`;
}

/* The buttons of a line for a step: those the service says (`actions`), each with its words, the call it makes and
   its icon; when the step lacks something, what it lacks instead. */
const ACTIONS = {
  run: (step) => ({ label: T("v2.line.run." + step), title: T("v2.line.run_title"), call: "run", icon: step }),
  translate_left: (step) => ({ label: T("v2.line.translate_left"), title: T("v2.line.translate_left_title"), call: "run", icon: step }),   // transcribed already
  translate: (step) => ({ label: T("v2.line.translate"), title: T("v2.line.translate_title"), call: "run", icon: step, primary: true }),
  retry: () => ({ label: T("v2.line.retry"), title: T("v2.line.retry_title"), call: "run", icon: "retry" }),
  cancel: () => ({ label: T("v2.line.cancel"), title: T("v2.line.cancel_title"), call: "cancel", icon: "cancel" }),
};

/* What the go-ahead for one video starts, read in the estimate of its task: the AI review when its cost is what it
   waits for, then the translation, each with its model and what it costs. */
function goAheadLines(p, item, e) {
  const lines = [];
  if (e.doubt) lines.push(T("v2.line.agree_doubt", { share: Math.round(e.doubt.share * 100), usual: Math.round(e.doubt.usual * 100) }));   // its language is doubtful
  const reviewer = ["language", "review"].includes(e.waits) && e.review ? e.review.translators.find((t) => t.id === e.review.engine) : null;
  if (reviewer) lines.push(T("v2.line.agree_review", { name: reviewer.name, cost: costText(reviewer, true) }));
  const id = item.overrides.translator || p.subtitles.translator;
  const translator = e.languages?.length ? e.translators.find((t) => t.id === id) : null;
  if (translator) lines.push(T("v2.line.agree_translation", { langs: languagesOf(e.languages), name: translator.name, cost: costText(translator, true) }));
  if (e.waits === "validation") lines.push(T("v2.sub.validation_help"));
  return lines;
}

/* The go-ahead for one video, from its line: what it starts and what it costs is asked for and shown first, and the
   click on the line only opens that question. A paid model never starts from a click that did not show its cost. */
function GoAheadButton({ p, item, step, a, icons }) {
  const [ask, setAsk] = useState(null);                  // the estimate of its task, while the question is open
  const open = async () => {
    const e = await busy("", () => api("GET", `/api/tasks/${item.stages[step].task}/estimate`));
    if (e) setAsk(e);
  };
  const go = () => { setAsk(null); act(`/api/projects/${p.id}/items/${item.id}/${a.call}`, { stage: step }); };
  const lines = ask ? goAheadLines(p, item, ask) : [];
  return html`
    ${icons
      ? html`<button type="button" class="small icon-act" title=${`${a.label} : ${a.title}`} aria-label=${a.label} onClick=${open}>${actIcon(a.icon)}</button>`
      : html`<button type="button" class="small" title=${a.title} onClick=${open}>${a.label}</button>`}
    ${ask && html`
      <${Confirm} title=${T("v2.line.agree_title")} yes=${T("v2.line.agree")} danger=${false} onYes=${go} onNo=${() => setAsk(null)}
                  text=${[item.title || item.source, ...lines].flatMap((l, n) => (n ? [html`<br />`, l] : [html`<strong>${l}</strong>`]))} />`}`;
}

export function LineButtons({ p, item, step, icons = false }) {
  const st = item.stages[step];
  if (!st.actions.length) return st.lacks ? html`<span class="help" title=${lackText(p, item, step)}>${lackShort(st.lacks)}</span>` : null;
  return st.actions.map((name) => {
    if (name === "play") return html`<${PlayButton} key=${name} item=${item} step=${step} icons=${icons} />`;
    if (name === "translate") return html`<${GoAheadButton} key=${name} p=${p} item=${item} step=${step} a=${ACTIONS[name](step)} icons=${icons} />`;
    if (name === "one_at_a_time") return html`
      <button key=${name} type="button" class="small" title=${T("v2.gpu.one_title")} onClick=${() => oneAtATime(p)}>${T("v2.gpu.one_at_a_time")}</button>`;
    const a = ACTIONS[name](step);
    const go = () => act(`/api/projects/${p.id}/items/${item.id}/${a.call}`, { stage: step });
    return icons
      ? html`<button key=${name} type="button" class="small icon-act" title=${`${a.label} : ${a.title}`} aria-label=${a.label} onClick=${go}>${actIcon(a.icon)}</button>`
      : html`<button key=${name} type="button" class=${`small ${a.primary ? "" : "secondary"}`} title=${a.title} onClick=${go}>${a.label}</button>`;
  });
}
