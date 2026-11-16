/* yt-dlp too old: video sites change often and an old yt-dlp fails on them. The page says so past 60 days (or past 14
   when a download of the project failed), and offers the update: yt-dlp's own builds update themselves (`yt-dlp -U`,
   run by the service, its log shown live); installed otherwise (pip, a package), the command to run is shown. "Later"
   hides the notice for this version. */
import { useEffect, useRef, useState } from "/static/vendor/preact-htm.js";
import { api, html, T } from "./lib.js";

const HIDDEN = "ytdlp-hidden";                         // the version whose notice was put off
const hiddenFor = () => { try { return localStorage.getItem(HIDDEN); } catch (e) { return null; } };

function UpdateDialog({ close, done }) {
  const ref = useRef(null);
  const log = useRef(null);
  const [u, setU] = useState({ state: "running", lines: [] });
  const [error, setError] = useState("");
  const [after, setAfter] = useState(null);
  useEffect(() => {
    ref.current.showModal();
    let stop = false, timer;
    const poll = async () => {
      try {
        const d = await api("GET", "/api/ytdlp/update");
        if (stop) return;
        setU(d);
        if (d.state === "running") timer = setTimeout(poll, 700);
        else { const i = await api("GET", "/api/ytdlp?fresh=1"); if (!stop) { setAfter(i); done(i); } }
      } catch (e) { if (!stop) setError(e.message); }
    };
    api("POST", "/api/ytdlp/update", {}).then((d) => { setU(d); poll(); }).catch((e) => setError(e.message));
    return () => { stop = true; clearTimeout(timer); };
  }, []);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [u.lines.length]);
  const end = () => { ref.current.close(); close(); };
  return html`
    <dialog ref=${ref} class="ytdlp-dialog" onClose=${close} aria-labelledby="ytdlp-title">
      <div class="head dialog-bar">
        <h2 id="ytdlp-title" tabindex="-1">${T("v2.ytdlp.update_title")}</h2>
        <button type="button" class="close" aria-label=${T("ui.close")} onClick=${end}>×</button>
      </div>
      ${u.command && html`<p class="help"><code>${u.command}</code></p>`}
      <pre class="terminal" ref=${log} aria-live="polite">${u.lines.join("\n") || " "}</pre>
      ${error ? html`<p class="error" role="alert">${error}</p>`
        : u.state === "running" ? html`<p class="busy"><span class="spinner inline"></span> ${T("v2.ytdlp.updating")}</p>`
        : u.state === "done" ? html`<p role="status">${T("v2.ytdlp.updated", { version: after?.version || "…" })}</p>`
        : html`<p class="error" role="alert">${T("v2.ytdlp.failed", { code: u.code })}</p>`}
      <div class="actions"><button type="button" class="secondary" onClick=${end}>${T("ui.close")}</button></div>
    </dialog>`;
}

export function YtdlpNotice({ items }) {
  const [info, setInfo] = useState(null);
  const [open, setOpen] = useState(false);
  const [, redraw] = useState(0);
  useEffect(() => { api("GET", "/api/ytdlp").then(setInfo).catch(() => {}); }, []);
  if (!info?.found) return null;
  const failed = items.some((i) => i.stages?.download?.state === "error");
  const show = info.old || (failed && info.age_days > 14);
  if (!open && (!show || (!failed && hiddenFor() === info.version))) return null;      // open: until the window is closed
  const later = () => { try { localStorage.setItem(HIDDEN, info.version); } catch (e) { /* this page only */ } redraw((n) => n + 1); };
  return html`
    <div class="attention ytdlp-notice" role="status">
      <p>${T(failed && !info.old ? "v2.ytdlp.failed_download" : "v2.ytdlp.old", { version: info.version, days: info.age_days })}</p>
      ${info.self_update
        ? html`<p class="row">
            <button type="button" class="primary small" onClick=${() => setOpen(true)}>${T("v2.ytdlp.update")}</button>
            ${!failed && html`<button type="button" class="secondary small" onClick=${later}>${T("v2.ytdlp.later")}</button>`}
          </p>`
        : html`<p class="help">${info.hint}</p>`}
      ${open && html`<${UpdateDialog} close=${() => setOpen(false)} done=${setInfo} />`}
    </div>`;
}
