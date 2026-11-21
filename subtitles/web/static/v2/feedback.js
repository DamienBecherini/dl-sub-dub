/* « Give my opinion »: a note out of five, a kind (opinion, bug, idea, security flaw), a message, all optional. The
   tool sends nothing: the user sends it, as an issue of the repository (public, a GitHub account) or by e-mail (no
   account), or copies the text. What will be sent is shown before. Nothing of the user's work goes with it: no video,
   no project, no path, no address of an engine. The version always goes; the system, Python, the window or the
   browser and the kinds of the engines (GET /api/about) only when the box is ticked.

   The issue is the form .github/ISSUE_TEMPLATE/feedback.yml, filled in by its address: the ids of its fields (rating,
   type, message, version, environment) are those `fields` gives. A security flaw never goes to that public form: its
   button opens the private report of the repository (the message copied, to paste there), and the mail stays.
   The same window as vault-watch and ia-on-prem, written for Preact. */
import { useEffect, useRef, useState } from "/static/vendor/preact-htm.js";
import { api, html, T } from "./lib.js";

const MAX = { issue: 8000, mail: 1800 };          // the longest address an issue form, a mail program takes
const STARS = { 5: "★★★★★ 5/5", 4: "★★★★☆ 4/5", 3: "★★★☆☆ 3/5", 2: "★★☆☆☆ 2/5", 1: "★☆☆☆☆ 1/5" };
const TYPES = ["opinion", "bug", "idea", "security"];
const TYPE_NAMES = { opinion: "Opinion", bug: "Bug", idea: "Idea", security: "Security" };   // as the issue form names them
const LABELS = { rating: "Rating", type: "Type", message: "Message", version: "Version", environment: "Environment" };
const flaw = (f) => f.type === "security";       // never a public issue: the private report of the repository, or a mail

let about = null;                                 // GET /api/about, read once
let draft = null;                                 // what is being written, kept while the page is open

/* The technical information, as it will be sent: no path, no address, no name. */
export function diagnostic(a) {
  if (!a) return "";
  return [a.system, a.python && `Python ${a.python}`, a.app ? "window" : "browser", a.transcription && `transcription ${a.transcription}`,
          a.translator && `translator ${a.translator}`, `dubbing ${a.dubbing ? "on" : "off"}`].filter(Boolean).join(" · ");
}

/* The fields of the issue form, by id; an answer not given is not sent. */
export function fields(f, a, message = f.message.trim()) {
  const out = {};
  if (f.rating && !flaw(f)) out.rating = STARS[f.rating];                     // a flaw is not rated
  if (f.type) out.type = TYPE_NAMES[f.type];
  if (message) out.message = message;
  out.version = a?.version || "?";
  if (f.diagnostic) out.environment = diagnostic(a);
  return out;
}

/* The text of the message (the mail, the copy, the preview): in English, the language of the repository and its form. */
export function text(f, a, message) {
  const o = fields(f, a, message);
  const head = ["rating", "type"].filter((k) => k in o).map((k) => `${LABELS[k]}: ${o[k]}`).join("\n");
  const tail = ["version", "environment"].filter((k) => k in o).map((k) => `${LABELS[k]}: ${o[k]}`).join("\n");
  return [head, o.message ? `${LABELS.message}:\n${o.message}` : "", tail].filter(Boolean).join("\n\n");
}

const title = (f, a) => [`[Feedback] ${f.type ? TYPE_NAMES[f.type] : "Opinion"}`, ...(f.rating && !flaw(f) ? [`${f.rating}/5`] : []), `v${a?.version || "?"}`].join(" · ");
const subject = (f, a) => (flaw(f) ? `Security flaw in dl-sub-dub ${a?.version || "?"}` : `Feedback on dl-sub-dub ${a?.version || "?"}`);

/* The longest address under `max`: the whole message when it fits, else cut (by halving) with a note. */
function fit(message, build, max) {
  if (build(message).length <= max || !message) return { url: build(message), cut: false };
  const chars = Array.from(message), note = "\n\n[…]";
  let low = 0, high = chars.length - 1;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (build(chars.slice(0, mid).join("").trimEnd() + note).length <= max) low = mid; else high = mid - 1;
  }
  return { url: build(chars.slice(0, low).join("").trimEnd() + note), cut: true };
}

export function issueUrl(f, a) {
  return fit(f.message.trim(), (message) => {
    const q = new URLSearchParams({ template: "feedback.yml", title: title(f, a), labels: "feedback", ...fields(f, a, message) });
    return `${a.repository}/issues/new?${q.toString().replace(/\+/g, "%20")}`;
  }, MAX.issue);
}

export function mailUrl(f, a) {
  const enc = (s) => encodeURIComponent(s.replace(/\r?\n/g, "\r\n"));
  return fit(f.message.trim(), (message) => `mailto:${a.feedback_email}?subject=${enc(subject(f, a))}&body=${enc(text(f, a, message))}`, MAX.mail);
}

// Something to send: a note, a kind or a message; for a flaw, a message (its kind alone says nothing)
export const ready = (f) => (flaw(f) ? Boolean(f.message.trim()) : Boolean(f.rating || f.type || f.message.trim()));

const ICON = html`<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /><path d="M12 13.2 9.4 10.7a1.7 1.7 0 0 1 2.4-2.4l.2.2.2-.2a1.7 1.7 0 0 1 2.4 2.4z" /></svg>`;

/* The window. */
function FeedbackDialog({ close }) {
  const ref = useRef(null);
  const [a, setAbout] = useState(about);
  const [f, setF] = useState(draft || { rating: 0, type: "", message: "", diagnostic: false, touched: false });
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    ref.current.showModal();
    if (!about) api("GET", "/api/about").then((x) => { about = x; setAbout(x); }).catch(() => {});
  }, []);
  useEffect(() => { draft = f; setCopied(false); }, [f]);
  const change = (more) => setF({ ...f, ...more });
  const chooseType = (type) => change({ type, ...(f.touched ? {} : { diagnostic: type === "bug" || type === "security" }) });
  const ok = ready(f), isFlaw = flaw(f);
  const issue = a ? issueUrl(f, a) : null, mail = a?.feedback_email ? mailUrl(f, a) : null;
  const copy = async () => {
    try { await navigator.clipboard.writeText(`${subject(f, a)}\n\n${text(f, a)}`); setCopied(true); }
    catch (e) { const p = ref.current.querySelector(".feedback-preview"); p.closest("details").open = true; getSelection().selectAllChildren(p); }
  };
  const link = (cls, href, label, id, onClick) => (ok && href
    ? html`<a class=${`button-link ${cls}`} id=${id} href=${href} target=${href.startsWith("mailto:") ? undefined : "_blank"} rel="noopener" onClick=${onClick}>${label}</a>`
    : html`<span class=${`button-link ${cls} disabled`} id=${id} aria-disabled="true">${label}</span>`);
  return html`
    <dialog ref=${ref} class="feedback-dialog" aria-labelledby="feedback-title" onClose=${close}
            onClick=${(e) => { if (e.target === ref.current) ref.current.close(); }}>
      <div class="head dialog-bar">
        <h2 id="feedback-title">${T("v2.feedback.title")}</h2>
        <button type="button" class="close" aria-label=${T("ui.close")} onClick=${() => ref.current.close()}>×</button>
      </div>
      <p class="help">${T("v2.feedback.intro")}</p>
      ${!isFlaw && html`
        <fieldset class="stars"><legend>${T("v2.feedback.rating")}</legend>
          ${[1, 2, 3, 4, 5].map((n) => html`
            <label key=${n} title=${T("v2.feedback.rating_of", { n })}>
              <input type="radio" name="feedback-rating" value=${n} checked=${f.rating === n} onChange=${() => change({ rating: n })} />
              <span class=${`star ${n <= f.rating ? "on" : ""}`} aria-hidden="true">★</span><span class="visually-hidden">${T("v2.feedback.rating_of", { n })}</span>
            </label>`)}
          <span class="help stars-said">${f.rating ? T("v2.feedback.rating_of", { n: f.rating }) : T("v2.feedback.no_rating")}</span>
          ${f.rating > 0 && html`<button type="button" class="link" onClick=${() => change({ rating: 0 })}>${T("v2.feedback.clear")}</button>`}
        </fieldset>`}
      <fieldset class="feedback-type"><legend>${T("v2.feedback.type")}</legend>
        <span class="mode-switch" role="radiogroup" aria-label=${T("v2.feedback.type")}>
          ${TYPES.map((k) => html`
            <button key=${k} type="button" role="radio" aria-checked=${f.type === k} class=${f.type === k ? "current" : ""}
                    onClick=${() => chooseType(k)}>${T("v2.feedback.type." + k)}</button>`)}
        </span>
      </fieldset>
      ${isFlaw && html`<p class="attention">${T("v2.feedback.security_intro")}</p>`}
      <label class="field" for="feedback-message">${T("v2.feedback.message")}</label>
      <textarea id="feedback-message" rows="5" value=${f.message} placeholder=${T("v2.feedback.message_help." + (f.type || "opinion"))}
                onInput=${(e) => change({ message: e.target.value })}></textarea>
      <label class="check"><input type="checkbox" checked=${f.diagnostic} onChange=${(e) => change({ diagnostic: e.target.checked, touched: true })} />
        ${T("v2.feedback.diagnostic")}</label>
      <p class="help small">${T("v2.feedback.diagnostic_what", { what: diagnostic(a) || "…" })}</p>
      <details open=${ok}><summary>${T("v2.feedback.preview")}</summary><pre class="feedback-preview">${a ? text(f, a) : ""}</pre></details>
      <div class="feedback-send">
        ${isFlaw
          ? html`<div>${link("primary", a && `${a.repository}/security/advisories/new`, `${T("v2.feedback.private")} ↗`, "feedback-private",
                              () => navigator.clipboard?.writeText(text(f, a)).catch(() => {}))}
                   <p class="help small">${T("v2.feedback.private_help")}</p></div>`
          : html`<div>${link("primary", issue?.url, `${T("v2.feedback.github")} ↗`, "feedback-issue")}
                   <p class="help small">${T("v2.feedback.github_help")}</p></div>`}
        ${mail && html`<div>${link("secondary", mail.url, T("v2.feedback.email"), "feedback-mail")}
                         <p class="help small">${T(isFlaw ? "v2.feedback.email_private" : "v2.feedback.email_help", { address: a.feedback_email })}</p></div>`}
        <div><button type="button" class="secondary" id="feedback-copy" disabled=${!ok || !a} onClick=${copy}>${T(copied ? "v2.feedback.copied" : "v2.feedback.copy")}</button>
          <p class="help small">${T("v2.feedback.copy_help")}</p></div>
      </div>
      ${!ok && html`<p class="help attention-text" role="status">${T("v2.feedback.needs")}</p>`}
      ${ok && ((!isFlaw && issue?.cut) || mail?.cut) && html`<p class="help attention-text" role="status">${T("v2.feedback.cut")}</p>`}
      ${!isFlaw && html`<p class="help feedback-security">${T("v2.feedback.security")} <button type="button" class="link" onClick=${() => chooseType("security")}>${T("v2.feedback.security_link")}</button></p>`}
    </dialog>`;
}

/* The button: in the header (it can be hidden in the Settings: web.feedback_button) and in the footer, quieter. */
export function FeedbackButton({ quiet = false }) {
  const [open, setOpen] = useState(false);
  return html`
    <button type="button" class=${`feedback-button ${quiet ? "quiet" : "secondary"}`} title=${T("v2.feedback.open")} onClick=${() => setOpen(true)}>
      ${ICON}<span>${T("v2.feedback.open")}</span></button>
    ${open && html`<${FeedbackDialog} close=${() => setOpen(false)} />`}`;
}
