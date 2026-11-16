/* A download under way, on its line: one segment per part yt-dlp downloads (the video, each audio track, or one
   file holding both), then the finishing (merging the streams, the metadata, the subtitles and the thumbnail
   embedded); under it, the step in plain words, the percentage, the size, the speed and the time left.
   And its log, as a terminal shows it (a progress line replaces the one before), in the unfolded line. */
import { useEffect, useRef, useState } from "/static/vendor/preact-htm.js";
import { api, html, languageName, T } from "./lib.js";

const KINDS = { video: "v2.dlp.video", audio: "v2.dlp.audio", both: "v2.dlp.both" };

/* "5.99MiB/s" → "5.99 MiB/s": yt-dlp's units, made readable. */
export const units = (s) => (s || "").replace(/^~?\s*([\d.]+)\s*([KMGT]i?B)/, "$1 $2");

export function phaseText(d) {
  if (d.phase === "subtitles") return T("v2.dlp.subtitles", { lang: languageName(d.language) || d.language || "?" });
  if (KINDS[d.phase]) {
    const many = d.parts.filter((x) => x.kind === d.phase).length > 1;
    const n = d.parts.slice(0, d.part || 0).filter((x) => x.kind === d.phase).length;
    return T(KINDS[d.phase]) + (many ? ` ${n}/${d.parts.filter((x) => x.kind === d.phase).length}` : "");
  }
  return T("v2.dlp." + (d.phase || "starting"));
}

export function DownloadBar({ d }) {
  if (!d) return html`<div class="dl-bar"><div class="dl-text help">${T("v2.dlp.starting")}</div></div>`;
  const facts = [d.percent != null && `${Math.floor(d.percent)} %`, d.size && units(d.size), d.speed && units(d.speed),
                 d.eta && T("v2.dlp.eta", { eta: d.eta })].filter(Boolean);
  return html`
    <div class="dl-bar" role="progressbar" aria-label=${phaseText(d)} aria-valuemin="0" aria-valuemax="100"
         aria-valuenow=${d.percent != null ? Math.floor(d.percent) : undefined}>
      <div class="dl-segments">
        ${d.parts.map((x, i) => html`
          <span key=${i} class=${`dl-segment ${x.done ? "done" : ""} ${d.part === i + 1 ? "active" : ""}`} title=${T(KINDS[x.kind] || "v2.dlp.both")}>
            <span class="dl-fill" style=${`width: ${x.percent}%`}></span>
          </span>`)}
        <span class=${`dl-segment finish ${d.finishing ? "active" : ""}`} title=${T("v2.dlp.finish")}>
          <span class="dl-fill" style=${`width: ${d.finishing ? 100 : 0}%`}></span>
        </span>
      </div>
      <div class="dl-text"><strong>${phaseText(d)}</strong>${facts.length ? html` · ${facts.join(" · ")}` : ""}</div>
    </div>`;
}

/* The log of a step of a video: of its download (yt-dlp), or of its task (transcription, translation, dubbing).
   Read again every second while it runs; kept at the bottom unless one scrolls up. */
async function readLog(p, item, step) {
  if (step === "download") return (await api("GET", `/api/projects/${p.id}/items/${item.id}/log`)).lines;
  const task = item.stages[step].task;
  return task ? (await api("GET", `/api/tasks/${task}`)).log || [] : [];
}

export function StepLog({ p, item, step, running }) {
  const [lines, setLines] = useState(null);
  const box = useRef(null);
  const pinned = useRef(true);
  useEffect(() => {
    let gone = false, timer;
    const read = async () => {
      try {
        const r = await readLog(p, item, step);
        if (!gone) setLines(r);
      } catch (e) { /* the next read will tell */ }
      if (!gone && running) timer = setTimeout(read, 1000);
    };
    read();
    return () => { gone = true; clearTimeout(timer); };
  }, [p.id, item.id, step, running, item.stages[step].task]);
  useEffect(() => {
    if (box.current && pinned.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines]);
  const onScroll = (e) => {
    const b = e.target;
    pinned.current = b.scrollHeight - b.scrollTop - b.clientHeight < 24;
  };
  if (lines === null) return html`<p class="help">${T("v2.dlp.reading_log")}</p>`;
  if (!lines.length) return html`<p class="help">${T(step === "download" ? "v2.dlp.no_log" : "v2.log.none")}</p>`;
  return html`<pre class="terminal" ref=${box} onScroll=${onScroll} tabindex="0" aria-label=${T("v2.dlp.log")}>${lines.join("\n")}</pre>`;
}
