/* The Dubbing screen: the engine of the settings (or what is missing to use it), the dubbed language, an MKV with
   every track or the dubbed track alone, the MKV in place of the original video; for each video, its own dubbed
   language and what was made. The dubbing takes the graphics card, one video at a time, after the subtitles of the
   video (a video not subtitled yet goes through its subtitles first). */
import { useEffect, useState } from "/static/vendor/preact-htm.js";
import { api, busy, Confirm, html, languageName, languagesOf, T, Tip, update } from "./lib.js";
import { LanguageChoice } from "./subtitles.js";

async function post(path, body) {
  const p = await busy("", () => api("POST", path, body));
  if (p) update({ project: p });
  return p;
}

/* Same test as dubbing.available on the service, on the settings read by the page. */
export const dubbingReady = (cfg) => !!(cfg?.dubbing?.enabled && cfg.dubbing.command?.length && cfg.dubbing.model);

/* The languages that can be dubbed: those of the subtitles, but the spoken one. */
function dubbable(languages, spoken) {
  return languages.filter((c) => c !== spoken);
}

/* The dubbed languages of a setting: a code or a list; only real codes (a value spoiled by an older service is left
   out); null when none. */
const CODE = /^[a-z]{2,3}(-[a-z0-9]{2,8})*$/;
const dubCodes = (v) => {
  const codes = (Array.isArray(v) ? v : v ? [v] : []).filter((c) => typeof c === "string" && CODE.test(c));
  return codes.length ? codes : null;
};

/* The subtitles with the dubbed languages added (a voice reads its translation): ticked there too, so it is seen. */
const withDubbed = (subtitles, dubbed, spoken) => [...subtitles, ...dubbed.filter((c) => c !== spoken && !subtitles.includes(c))];

const names = languagesOf;

/* The dubbed languages not among those of the subtitles: they are translated first (said under the choice). */
function AddedToSubtitles({ chosen, subtitles, spoken }) {
  const added = chosen.filter((c) => c !== spoken && !subtitles.includes(c));
  return added.length ? html`<p class="help">${T("v2.dub.added_to_subtitles", { langs: names(added) })}</p>` : null;
}

async function refresh(p) {
  const fresh = await api("GET", `/api/projects/${p.id}`).catch(() => null);
  if (fresh) update({ project: fresh });
}

/* "One AI at a time" for the card (after a dubbing that ran out of its memory, or chosen here). */
export async function oneAtATime(p) {
  await busy("", () => api("POST", "/api/dubbing/gpu", { sharing: "one_at_a_time" }));
  await refresh(p);
}

/* The graphics card, shared or not by the local translation model and the dubbing: the setting, how the card is
   freed, and the check that measures whether both fit (a minute or two). */
export function GpuSettings({ p }) {
  const g = p.dubbing.gpu;
  const [check, setCheck] = useState(null);                 // {state, step, result, error} of the check under way
  useEffect(() => {
    let gone = false, timer;
    const poll = async () => {
      const c = await api("GET", "/api/dubbing/gpu-check").catch(() => null);
      if (gone || !c) return;
      setCheck(c);
      if (c.state === "running") timer = setTimeout(poll, 1500);
      else if (c.state === "done") refresh(p);
    };
    if (check?.state === "running") timer = setTimeout(poll, 1500);
    else if (check === null) poll();
    return () => { gone = true; clearTimeout(timer); };
  }, [check?.state]);
  const start = async () => {
    const c = await busy("", () => api("POST", "/api/dubbing/gpu-check", { project: p.id }));
    if (c) setCheck(c);
  };
  const setSharing = async (sharing) => {
    await busy("", () => api("POST", "/api/dubbing/gpu", { sharing }));
    await refresh(p);
  };
  const t = g.test;
  const how = !g.local ? T("v2.gpu.not_local", { name: g.translator || "—" })
    : g.sharing === "together" ? T("v2.gpu.together_note", { name: g.translator })
      : T("v2.gpu.free." + (g.free || "ask"), { name: g.translator });
  const running = check?.state === "running";
  return html`
    <div class="wide gpu-settings">
      <label class="inline-field">${T("v2.gpu.label")}
        <select value=${g.setting} onChange=${(e) => setSharing(e.target.value)}>
          <option value="auto">${T("v2.gpu.auto", { v: T("v2.gpu." + g.sharing) })}</option>
          <option value="together">${T("v2.gpu.together")}</option>
          <option value="one_at_a_time">${T("v2.gpu.one_at_a_time")}</option>
        </select>
      </label>
      <p class="help">${how}</p>
      <div class="row">
        <button type="button" class="secondary small" disabled=${running || !p.dubbing.available} onClick=${start}
                title=${T("v2.gpu.check_title")}>${T("v2.gpu.check")}</button>
        ${running && html`<span class="help"><span class="spinner small inline" aria-hidden="true"></span> ${T("v2.gpu.step." + (check.step || "start"))}</span>`}
        ${check?.state === "error" && html`<span class="item-error-inline">${check.error}</span>`}
      </div>
      ${t && !running && html`
        <p class=${t.fits === false ? "attention" : "help"}>
          ${T(t.fits === true ? "v2.gpu.fits" : t.fits === false ? "v2.gpu.does_not_fit" : "v2.gpu.unknown", { date: t.date })}
          ${t.peak_mib ? " " + T("v2.gpu.measures", { peak: t.peak_mib, total: t.total_mib, s: t.seconds ?? "?" }) : ""}
          ${t.model_mib ? " " + T("v2.gpu.model", { mib: t.model_mib }) : ""}
          ${t.detail ? ` (${t.detail})` : ""}
        </p>`}
    </div>`;
}

/* Shown on every step screen while the dubbing waits for the user to free the card. */
export function GpuWait({ p }) {
  if (!p.dubbing?.gpu?.wait) return null;
  return html`
    <div class="attention gpu-wait" role="status">
      ${T("v2.gpu.wait", { name: p.dubbing.gpu.translator })}
      <button type="button" class="small" onClick=${() => post(`/api/projects/${p.id}/gpu-freed`, {})}>${T("v2.gpu.freed")}</button>
    </div>`;
}

export function DubbingSettings({ p }) {
  const d = p.dubbing;
  const [askReplace, setAskReplace] = useState(false);   // the original video goes: asked before the box is ticked
  const set = (change) => post(`/api/projects/${p.id}/settings`, change);
  const spoken = p.options.language || "";               // chosen for the project; otherwise detected per video
  const heard = [...new Set(p.items.map((i) => i.spoken))];             // chosen, detected, or given by the site
  const origin = spoken || (heard.length === 1 ? heard[0] : "");            // every video speaks it: greyed
  const spokenBy = (c) => p.items.filter((i) => i.spoken === c).length;
  const skipped = (dubCodes(d.languages) || []).filter((c) => c !== origin && spokenBy(c) > 0);
  const languages = dubbable(p.subtitles.languages, spoken);
  const first = spoken ? languages[0] : "";
  return html`
    <div class="screen-settings grid-settings">
      <div class="wide">
        ${d.available
          ? html`<p class="help" title=${d.model}>${T("v2.dub.engine", { model: d.model.split(/[\/]/).filter(Boolean).pop(), steps: d.steps ?? "?" })}${" · "}<a href="/settings">${T("v2.dub.engine_settings")}</a></p>`
          : html`<p class="attention">${T(d.enabled ? "v2.dub.incomplete" : "v2.dub.not_configured")}
                   <a href="/settings">${T("v2.dub.engine_settings")}</a></p>`}
      </div>
      <div class="wide dub-languages">
        <span class="field-label">${T("v2.dub.languages")} <${Tip} text=${T("v2.dub.languages_title")} /></span>
        <${LanguageChoice} value=${dubCodes(d.languages) || []} origin=${origin} disabled=${p.rules.locked} onChange=${(v) => {
          const subs = withDubbed(p.subtitles.languages, v, spoken);
          set(subs.length > p.subtitles.languages.length ? { dub_language: v.length ? v : null, languages: subs } : { dub_language: v.length ? v : null });
        }} />
        ${p.rules.locked && html`<p class="help">${T("v2.languages.locked")}</p>`}
        ${(dubCodes(d.languages) || []).length
          ? html`<${AddedToSubtitles} chosen=${d.languages} subtitles=${p.subtitles.languages} spoken=${spoken} />`
          : html`<p class="help">${first ? T("v2.dub.first", { lang: names(languages) }) : languages.length ? T("v2.dub.first_plain") : T("v2.dub.none_translated")}</p>`}
        ${skipped.map((c) => html`<p class="help" key=${c}>${T("v2.dub.skipped_for", { lang: languageName(c) || c, n: spokenBy(c) })}</p>`)}
      </div>
      <div class="wide checks">
        <label class="check">
          <input type="checkbox" checked=${d.mkv} onChange=${(e) => set({ mkv: e.target.checked })} /> ${T("v2.dub.mkv")} <${Tip} text=${T("v2.dub.mkv_title")} />
        </label>
        <label class="check">
          <input type="checkbox" checked=${d.replace && d.mkv} disabled=${!d.mkv}
                 onChange=${(e) => {
                   if (!e.target.checked) { set({ replace: false }); return; }
                   e.target.checked = false;                // ticked only once confirmed
                   setAskReplace(true);
                 }} /> ${T("v2.dub.replace")} <${Tip} text=${T("v2.dub.replace_title")} />
        </label>
        ${askReplace && html`<${Confirm} title=${T("v2.dub.replace_confirm_title")} text=${T("v2.dub.replace_confirm")}
                                       yes=${T("v2.dub.replace_confirm_yes")}
                                       onYes=${() => { setAskReplace(false); set({ replace: true }); }} onNo=${() => setAskReplace(false)} />`}
      </div>
      ${!languages.length && html`<p class="attention wide">${T("v2.dub.needs_translation")}</p>`}
      <p class="help wide">${T("v2.dub.one_at_a_time")}${p.dubbing_s ? " " + T("v2.dub.left", { min: Math.max(1, Math.round(p.dubbing_s / 60)) }) : ""}</p>
    </div>`;
}

/* One video: its dubbed language (otherwise the project's), and what was made. */
export function DubbingDetails({ p, item }) {
  const path = `/api/projects/${p.id}/items/${item.id}/settings`;
  const spoken = item.spoken;                              // detected, chosen, or given by the site (before transcription)
  const origin = spoken;
  const languages = dubbable(item.overrides.languages || p.subtitles.languages, spoken);
  const projectLangs = (p.dubbing.languages || []).filter((c) => c !== spoken);
  const project = projectLangs.length ? names(projectLangs) : spoken && languages.length ? names(languages)
    : spoken ? T("v2.dub.nothing_short", { lang: languageName(spoken) || spoken }) : T("v2.dub.first_short");
  const ownDub = dubCodes(item.overrides.dub_language);
  // Its own choice open with nothing ticked yet: the project has no language to give it (a French video, French
  // subtitles only), so the list shows empty; a language ticked there is added to its subtitles too.
  const [choosing, setChoosing] = useState(false);
  const asProject = !ownDub && !choosing;
  const makeOwn = (on) => {
    if (!on) { setChoosing(false); post(path, { dub_language: null }); return; }
    const start = projectLangs.length ? projectLangs : languages.slice(0, 1);
    setChoosing(true);
    if (start.length) post(path, { dub_language: start });
  };
  const made = item.facts.muxed ? T("v2.dub.made_mkv") : item.facts.dubbed ? T("v2.dub.made_track") : "";
  const [askReplace, setAskReplace] = useState(false);
  const own = item.overrides;
  const mkv = own.mkv ?? p.dubbing.mkv;                                   // what this video will make
  const yesNo = (v) => T(v ? "v2.own.yes" : "v2.own.no");
  return html`
    <div class="details">
      <div class="dub-languages">
        <span class="field-label">${T("v2.dub.languages_video")}</span>
        <label class="check"><input type="checkbox" checked=${asProject} disabled=${item.locked} onChange=${(e) => makeOwn(!e.target.checked)} />
          ${T("v2.dub.languages_as_project", { v: project })}</label>
        ${item.locked && html`<p class="help">${T("v2.languages.locked_video")}</p>`}
        ${!asProject && html`
          <${LanguageChoice} value=${ownDub || []} origin=${origin} disabled=${item.locked} onChange=${(v) => {
            const current = item.overrides.languages || p.subtitles.languages;
            const subs = withDubbed(current, v, spoken);
            post(path, subs.length > current.length ? { dub_language: v.length ? v : null, languages: subs } : { dub_language: v.length ? v : null });
          }} />
          <${AddedToSubtitles} chosen=${ownDub || []} subtitles=${item.overrides.languages || p.subtitles.languages} spoken=${spoken} />`}
      </div>
      <label class="inline-field">${T("v2.dub.final_video")}
        <select value=${own.mkv == null ? "" : own.mkv ? "mkv" : "voice"}
                onChange=${(e) => post(path, { mkv: e.target.value === "" ? null : e.target.value === "mkv" })}>
          <option value="">${T("v2.sub.as_project", { v: T(p.dubbing.mkv ? "v2.dub.final_mkv" : "v2.dub.final_voice") })}</option>
          <option value="mkv">${T("v2.dub.final_mkv")}</option>
          <option value="voice">${T("v2.dub.final_voice")}</option>
        </select>
      </label>
      <label class="inline-field">${T("v2.dub.replace_video")}
        <select value=${own.replace == null ? "" : own.replace ? "yes" : "no"} disabled=${!mkv}
                onChange=${(e) => {
                  const v = e.target.value;
                  if (v === "yes") { e.target.value = own.replace == null ? "" : own.replace ? "yes" : "no"; setAskReplace(true); return; }   // once confirmed
                  post(path, { replace: v === "" ? null : false });
                }}>
          <option value="">${T("v2.sub.as_project", { v: yesNo(p.dubbing.replace && p.dubbing.mkv) })}</option>
          <option value="no">${T("v2.own.no")}</option>
          <option value="yes">${T("v2.own.yes")}</option>
        </select>
      </label>
      ${askReplace && html`<${Confirm} title=${T("v2.dub.replace_confirm_title")} text=${T("v2.dub.replace_confirm_video")}
                                     yes=${T("v2.dub.replace_confirm_yes_video")}
                                     onYes=${() => { setAskReplace(false); post(path, { replace: true }); }} onNo=${() => setAskReplace(false)} />`}
      ${made && html`
        <p class="help">${made}
          <button type="button" class="secondary small" onClick=${() => api("POST", "/api/open", { path: item.video })}>
            ${T("v2.dub.open_folder")}</button></p>`}
    </div>`;
}
