/* The Download screen: the quality of the project, and for each address a line that unfolds on what the site offers
   (read in the background: picture, author, date, heights, audio tracks, subtitles) and the choices of this video:
   its quality, its audio tracks (the original by default), the subtitles of the site to fetch. Once downloaded, the
   tracks not taken can still be added, without downloading the video again.

   A site may offer dozens of tracks: the original and those in the languages of the project and of the page come
   first, the others behind "Show the n others". */
/* global I18N */
import { useState } from "/static/vendor/preact-htm.js";
import { api, busy, duration, html, isAddress, languageName, originOf, T, update } from "./lib.js";

const HEIGHTS = [2160, 1440, 1080, 720, 480, 360];

async function post(path, body) {
  const p = await busy("", () => api("POST", path, body));
  if (p) update({ project: p });
}

const base = (code) => String(code || "").toLowerCase().split("-")[0];

/* Languages shown first: the spoken one, those wanted for the subtitles, the page's. */
function preferred(p, site) {
  return new Set([base(site?.language), ...(p.options.languages || []).map(base), base(I18N.language)].filter(Boolean));
}

export function QualityChoice({ value, heights, fallback, disabled, onChange, label }) {
  const options = (heights?.length ? heights : HEIGHTS);
  return html`
    <label class="inline-field">${label}
      <select value=${value ?? ""} disabled=${disabled} onChange=${(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">${fallback}</option>
        ${options.map((h) => html`<option value=${h}>${h}p</option>`)}
      </select>
    </label>`;
}

export function ProjectQuality({ p }) {
  return html`
    <div class="screen-settings">
      <${QualityChoice} label=${T("v2.dl.quality_project")} value=${p.options.max_height} fallback=${T("v2.dl.quality_config")}
                        onChange=${(v) => post(`/api/projects/${p.id}/settings`, { max_height: v })} />
    </div>`;
}

function trackLabel(t) {
  const kind = t.kind ? ` · ${T("v2.dl.kind." + t.kind)}` : "";
  return `${languageName(t.language) || t.language}${kind}`;
}

/* A list of choices where the preferred ones come first and the rest folds. */
function Choices({ entries, isChecked, isTaken, onToggle, preferredKeys, taken = T("v2.dl.taken") }) {
  const [all, setAll] = useState(false);
  const first = entries.filter((e) => preferredKeys(e) || isChecked(e) || isTaken(e));
  const shown = all || first.length === 0 ? entries : first;
  const hidden = entries.length - shown.length;
  return html`
    <div class="choices">
      ${shown.map((e) => html`
        <label key=${e.key} class=${`check ${isTaken(e) ? "taken" : ""}`}>
          <input type="checkbox" checked=${isChecked(e) || isTaken(e)} disabled=${isTaken(e)} onChange=${(ev) => onToggle(e, ev.target.checked)} />
          ${e.label}${isTaken(e) ? html` <span class="help">${taken}</span>` : ""}
        </label>`)}
      ${hidden > 0 && html`<button type="button" class="link" onClick=${() => setAll(true)}>${T("v2.dl.show_others", { n: hidden })}</button>`}
      ${all && first.length > 0 && first.length < entries.length
        && html`<button type="button" class="link" onClick=${() => setAll(false)}>${T("v2.dl.show_less")}</button>`}
    </div>`;
}

/* A video file: the address it comes from (read in the file or given by the user), to take tracks of its site. The
   site is asked only when the user wants it. Without an address: a field to give it. */
function FileOrigin({ p, item, itemPath }) {
  const origin = originOf(item);
  const [editing, setEditing] = useState(!origin);
  const [value, setValue] = useState(origin);
  const valid = /^https?:\/\/\S+$/.test(value.trim());
  const save = async (address) => { await post(`${itemPath}/origin`, { address }); setEditing(false); };
  if (editing) return html`
    <div class="details file-origin">
      <label class="origin-field">${T("v2.origin.label")} <span class="help">${T("v2.origin.optional")}</span>
        <span class="row"><input type="url" value=${value} placeholder="https://…" onInput=${(e) => setValue(e.target.value)}
                                 onKeyDown=${(e) => { if (e.key === "Enter" && valid) save(value.trim()); }} />
          <button type="button" class="small" disabled=${!valid} onClick=${() => save(value.trim())}>${T("v2.origin.save")}</button>
          ${origin && html`<button type="button" class="small secondary" onClick=${() => { setValue(origin); setEditing(false); }}>${T("ui.cancel")}</button>`}
        </span></label>
      <p class="help">${T(origin ? "v2.origin.change_help" : "v2.origin.unknown_help")}</p>
      ${item.facts.address_given && html`<button type="button" class="link" onClick=${() => save("")}>${T("v2.origin.forget")}</button>`}
    </div>`;
  return html`
    <div class="details file-origin">
      <p class="origin-line">${T("v2.origin.from")} <code>${origin}</code>
        <span class="help">${T(item.facts.address_given ? "v2.origin.given" : "v2.origin.read")}</span>
        <button type="button" class="link" onClick=${() => setEditing(true)}>${T("v2.origin.change")}</button></p>
      <p class="help">${T("v2.origin.what")}</p>
      <div class="row">
        <button type="button" class="small" onClick=${() => post(`${itemPath}/info`, {})}>${T("v2.origin.see")}</button>
        <span class="help">${T("v2.origin.asked_then")}</span>
      </div>
    </div>`;
}

export function DownloadDetails({ p, item }) {
  const site = item.facts.site;
  const st = item.stages.download;
  const file = !isAddress(item.source);                  // a video file: tracks of its site added to it
  const downloaded = st.state === "done" || file;
  const locked = st.state === "running";
  const [toAdd, setToAdd] = useState({ audio: [], subtitles: [] });     // after the download: tracks to add
  const itemPath = `/api/projects/${p.id}/items/${item.id}`;

  if (file && !site && !item.facts.site_error && !(item.facts.site_wanted && originOf(item))) {
    return html`<${FileOrigin} p=${p} item=${item} itemPath=${itemPath} />`;
  }
  if (!site) {
    return item.facts.site_error
      ? html`<div class="details"><p class="item-error">${T("v2.dl.info_failed")} ${item.facts.site_error}</p>
               <button type="button" class="secondary small" onClick=${() => post(`${itemPath}/info`, {})}>${T("v2.dl.info_again")}</button></div>`
      : html`<div class="details"><p class="help"><span class="spinner small inline" aria-hidden="true"></span> ${T("v2.dl.info_reading")}</p></div>`;
  }

  const prefer = preferred(p, site);
  const original = site.audio.find((a) => a.kind === "original")?.language;
  const audioChoice = item.overrides.audio || (original ? [original] : []);
  const subsChoice = item.overrides.site_subtitles || [];
  const taken = item.facts.tracks || { audio: [], subtitles: [] };
  const takenAudio = downloaded ? (taken.audio?.length ? taken.audio : [original]) : [];
  const subKey = (s) => `${s.language}${s.auto ? ":auto" : ""}`;
  const takenSubs = downloaded ? (taken.subtitles || []).map(subKey) : [];

  const audioEntries = site.audio.map((a) => ({ key: a.language, label: trackLabel(a), value: a.language, kind: a.kind }));
  const subEntries = [
    ...site.subtitles.map((l) => ({ key: l, label: languageName(l) || l, value: { language: l, auto: false } })),
    ...site.auto_subtitles.map((l) => ({ key: `${l}:auto`, label: `${languageName(l) || l} · ${T("v2.dl.automatic")}`, value: { language: l, auto: true } })),
  ];

  const setAudio = (e, on) => {
    if (downloaded) { setToAdd((x) => ({ ...x, audio: on ? [...x.audio, e.value] : x.audio.filter((v) => v !== e.value) })); return; }
    const next = on ? [...audioChoice, e.value] : audioChoice.filter((v) => v !== e.value);
    post(`${itemPath}/settings`, { audio: next.length === 1 && next[0] === original ? null : next });
  };
  const setSub = (e, on) => {
    if (downloaded) {
      setToAdd((x) => ({ ...x, subtitles: on ? [...x.subtitles, e.value] : x.subtitles.filter((v) => subKey(v) !== e.key) }));
      return;
    }
    const next = on ? [...subsChoice, e.value] : subsChoice.filter((v) => subKey(v) !== e.key);
    post(`${itemPath}/settings`, { site_subtitles: next.length ? next : null });
  };
  const checkedAudio = (e) => (downloaded ? toAdd.audio.includes(e.value) : audioChoice.includes(e.value));
  const checkedSub = (e) => (downloaded ? toAdd.subtitles.some((v) => subKey(v) === e.key) : subsChoice.some((v) => subKey(v) === e.key));
  const adding = item.facts.adding;

  return html`
    <div class="details">
      <div class="site">
        ${site.thumbnail && html`<img src=${site.thumbnail} alt="" loading="lazy" referrerpolicy="no-referrer" />`}
        <div>
          <strong>${site.title}</strong>
          <div class="help">${[site.uploader, site.upload_date && `${site.upload_date.slice(6, 8)}/${site.upload_date.slice(4, 6)}/${site.upload_date.slice(0, 4)}`,
                               duration(site.duration)].filter(Boolean).join(" · ")}</div>
          ${file && html`<div class="help origin-line"><code>${originOf(item)}</code>
            <button type="button" class="link" onClick=${() => post(`${itemPath}/origin`, { address: originOf(item) })}>${T("v2.origin.change")}</button></div>`}
        </div>
      </div>
      ${file ? html`<p class="help">${T("v2.origin.video_kept")}</p>` : html`
      <${QualityChoice} label=${T("v2.dl.quality")} value=${item.overrides.max_height} heights=${site.heights}
                        fallback=${T("v2.dl.quality_as_project", { q: p.options.max_height ? p.options.max_height + "p" : T("v2.dl.quality_config") })}
                        disabled=${downloaded || locked} onChange=${(v) => post(`${itemPath}/settings`, { max_height: v })} />`}
      <div class="choice-group">
        <h4>${T("v2.dl.audio")}</h4>
        ${audioEntries.length
          ? html`<${Choices} entries=${audioEntries} isChecked=${checkedAudio} isTaken=${(e) => takenAudio.includes(e.value)}
                             onToggle=${setAudio} preferredKeys=${(e) => e.kind === "original" || prefer.has(base(e.value))}
                             taken=${file ? T("v2.origin.in_file") : undefined} />`
          : html`<p class="help">${T("v2.dl.audio_single")}</p>`}
      </div>
      <div class="choice-group">
        <h4>${T("v2.dl.subtitles")}</h4>
        ${subEntries.length
          ? html`<${Choices} entries=${subEntries} isChecked=${checkedSub} isTaken=${(e) => takenSubs.includes(e.key)}
                             onToggle=${setSub} preferredKeys=${(e) => prefer.has(base(e.value.language))}
                             taken=${file ? T("v2.origin.taken") : undefined} />`
          : html`<p class="help">${T("v2.dl.no_subtitles")}</p>`}
      </div>
      ${downloaded && html`
        <div class="row add-tracks">
          <button type="button" class="small" disabled=${adding || !(toAdd.audio.length || toAdd.subtitles.length)}
                  onClick=${() => { post(`${itemPath}/tracks`, toAdd); setToAdd({ audio: [], subtitles: [] }); }}>${T(file ? "v2.origin.add_to_file" : "v2.dl.add_tracks")}</button>
          ${file && !adding && html`<span class="help">${T("v2.origin.not_again")}</span>`}
          ${adding && html`<span class="help"><span class="spinner small inline" aria-hidden="true"></span> ${T("v2.dl.adding")}</span>`}
          ${item.facts.tracks_error && html`<span class="item-error">${item.facts.tracks_error}</span>`}
        </div>`}
    </div>`;
}
