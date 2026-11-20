/* Notifications of the browser: a bell in the header, off by default. The browser asks its permission only on a click
   on the bell (never by itself, never again once refused: the bell then says how to allow it in the browser). When on,
   the page notifies, while it is not in sight, the end of a run of the project, a video in error and a pause waiting
   for a decision (the cost of a paid translation). The bell can be hidden in the Settings (web.notify_button). */
import { useEffect, useRef, useState } from "/static/vendor/preact-htm.js";
import { html, stateKey, T } from "./lib.js";

const KEY = "notify";
const supported = () => typeof window.Notification === "function";
const permission = () => (supported() ? Notification.permission : "denied");
const wanted = () => { try { return localStorage.getItem(KEY) === "on"; } catch (e) { return false; } };
const want = (on) => { try { if (on) localStorage.setItem(KEY, "on"); else localStorage.removeItem(KEY); } catch (e) { /* this page only */ } };
export const notifying = () => wanted() && permission() === "granted";

const bell = (off) => html`
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
    <path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4l2-2zM10 20a2 2 0 0 0 4 0" />
    ${off && html`<path d="M4 4l16 16" />`}
  </svg>`;

export function NotifyButton() {
  const [, redraw] = useState(0);
  const [said, setSaid] = useState("");
  if (!supported()) return null;
  const blocked = permission() === "denied";
  const on = notifying();
  const title = blocked ? T("v2.notify.blocked") : on ? T("v2.notify.on") : T("v2.notify.off");
  const click = async () => {
    if (on) want(false);
    else if (blocked) { setSaid(T("v2.notify.blocked")); setTimeout(() => setSaid(""), 8000); }
    else {
      const answer = permission() === "granted" ? "granted" : await Notification.requestPermission();
      want(answer === "granted");
      if (answer !== "granted") { setSaid(T("v2.notify.refused")); setTimeout(() => setSaid(""), 8000); }
    }
    redraw((n) => n + 1);
  };
  return html`
    <button type="button" class=${`secondary icon notify ${on ? "on" : ""}`} title=${title} aria-label=${title} aria-pressed=${on}
            onClick=${click}>${bell(!on)}</button>
    ${said && html`<span class="notify-said" role="status">${said}</span>`}`;
}

function send(title, body, tag) {
  try {
    const n = new Notification(title, { body, tag, icon: document.querySelector("link[rel=icon]")?.href });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (e) { /* a browser that refuses notifications from a page (Android Chrome): nothing */ }
}

/* What changed between two summaries of the project that deserves a notification. */
export function changes(before, p) {
  const out = [];
  const name = p.title || p.root;
  const errors = (q) => new Set(q.items.flatMap((i) => Object.entries(i.stages).filter(([, st]) => st.state === "error").map(([k]) => `${i.id}|${k}`)));
  const was = errors(before);
  for (const key of errors(p)) {
    if (was.has(key)) continue;
    const [id, step] = key.split("|");
    const i = p.items.find((x) => x.id === id);
    out.push([T("v2.notify.error_title", { step: T("v2.step." + step) }), (i?.title || i?.source || "") + " · " + name, "error-" + key]);
  }
  const working = (q) => q.rules.working;
  if (p.rules.go_ahead && !before.rules.go_ahead) out.push([T("v2.notify.waiting_title"), name, "wait-" + p.id]);   // a video run on its own too
  else if (working(before) && !working(p) && stateKey(p) === "done") out.push([T("v2.notify.done_title"), name, "done-" + p.id]);
  return out;
}

/* Watches the project shown; notifies its changes while the page is not in sight. */
export function useNotifications(p) {
  const last = useRef(null);
  useEffect(() => {
    const before = last.current;
    last.current = p;
    if (!p || !before || before.id !== p.id || !notifying()) return;
    if (document.visibilityState === "visible" && document.hasFocus()) return;
    for (const [title, body, tag] of changes(before, p)) send(title, body, tag);
  }, [p]);
}
