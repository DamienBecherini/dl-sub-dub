/* The Information tab of a line, for a video file (a file added, or an address once downloaded): a picture of it with
   its title and duration, its quality (definition, codec, frame rate, size), the audio tracks and the subtitles inside
   it, and the subtitle files next to it. Read by the service with ffprobe (GET …/media), the picture made by ffmpeg
   (GET …/thumbnail). */
/* global I18N */
import { useEffect, useState } from "/static/vendor/preact-htm.js";
import { api, duration, html, languageName, T } from "./lib.js";

const channels = (n) => (n === 1 ? T("v2.info.mono") : n === 2 ? T("v2.info.stereo") : n === 6 ? "5.1" : n === 8 ? "7.1"
  : n ? T("v2.info.channels", { n }) : "");
const lang = (code) => (code && code !== "und" ? `${languageName(code) || code}` : T("v2.info.no_language"));
const size = (bytes) => {
  const gb = bytes >= 2 ** 30;
  return new Intl.NumberFormat(I18N.language, { style: "unit", unit: gb ? "gigabyte" : "megabyte", maximumFractionDigits: gb ? 2 : 1 })
    .format(bytes / 2 ** (gb ? 30 : 20));
};
const upper = (s) => (s || "").toUpperCase();

export function InfoPanel({ p, item }) {
  const [m, setM] = useState(null);
  const [error, setError] = useState("");
  const [picture, setPicture] = useState(true);
  const base = `/api/projects/${p.id}/items/${item.id}`;
  useEffect(() => {
    let gone = false;
    setM(null);
    setError("");
    api("GET", `${base}/media`).then((d) => { if (!gone) setM(d); }).catch((e) => { if (!gone) setError(e.message); });
    return () => { gone = true; };
  }, [item.video]);
  if (error) return html`<div class="details"><p class="item-error">${item.facts.missing ? T("v2.line.missing_title", { path: item.video }) : error}</p></div>`;
  if (!m) return html`<div class="details"><p class="help"><span class="spinner small inline" aria-hidden="true"></span> ${T("v2.info.reading")}</p></div>`;
  const v = m.video;
  const quality = v && [v.width && v.height ? `${v.width}×${v.height} (${v.height}p)` : "", upper(v.codec),
    v.fps ? T("v2.info.fps", { n: new Intl.NumberFormat(I18N.language, { maximumFractionDigits: 2 }).format(v.fps) }) : ""].filter(Boolean).join(" · ");
  return html`
    <div class="details media-info">
      <div class="site">
        ${picture && html`<img src=${`${base}/thumbnail`} alt="" loading="lazy" onError=${() => setPicture(false)} />`}
        <div>
          <strong>${item.title || m.name.replace(/\.[^.]+$/, "")}</strong>
          <div class="help">${[duration(m.duration), size(m.size)].filter(Boolean).join(" · ")}</div>
          ${m.source && html`<div class="help info-source">${T("v2.info.source")} <a href=${m.source} target="_blank" rel="noopener noreferrer">${m.source}</a></div>`}
          <div class="help media-name">${T("v2.info.file")} <code>${m.path.replaceAll("\\", "/")}</code></div>
        </div>
      </div>
      <div class="choice-group">
        <h4>${T("v2.info.quality")}</h4>
        <p>${quality || T("v2.info.no_picture")}</p>
      </div>
      <div class="choice-group">
        <h4>${T("v2.info.audio", { n: m.audio.length })}</h4>
        ${m.audio.length
          ? html`<ul class="tracks">${m.audio.map((a) => html`
              <li>${lang(a.language)}${[upper(a.codec), channels(a.channels), a.title, a.default ? T("v2.info.default") : ""]
                .filter(Boolean).map((x) => html` <span class="help">· ${x}</span>`)}</li>`)}</ul>`
          : html`<p class="help">${T("v2.info.no_audio")}</p>`}
      </div>
      <div class="choice-group">
        <h4>${T("v2.info.subtitles_inside", { n: m.subtitles.length })}</h4>
        ${m.subtitles.length
          ? html`<ul class="tracks">${m.subtitles.map((s) => html`
              <li>${lang(s.language)}${[upper(s.codec), s.title].filter(Boolean).map((x) => html` <span class="help">· ${x}</span>`)}</li>`)}</ul>`
          : html`<p class="help">${T("v2.info.none")}</p>`}
      </div>
      <div class="choice-group">
        <h4>${T("v2.info.subtitles_next", { n: m.sidecar.length })}</h4>
        ${m.sidecar.length
          ? html`<ul class="tracks">${m.sidecar.map((s) => html`
              <li>${s.language === "?" ? T("v2.info.unknown_language") : lang(s.language)} <span class="help">· ${s.name}</span></li>`)}</ul>`
          : html`<p class="help">${T("v2.info.none")}</p>`}
      </div>
    </div>`;
}
