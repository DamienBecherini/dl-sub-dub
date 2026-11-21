/* The buttons of the header, the same as on the current page and sharing its choices: language of the interface
   (saved in the settings, for the log of the service too), light or dark mode and the palette of each mode, with the
   user's own themes (themes.js; kept in this browser, same keys), the size of the page (boot.js), and Settings. */
/* global I18N, autonym, pickLanguage, useLanguage */
import { useEffect, useLayoutEffect, useRef, useState } from "/static/vendor/preact-htm.js";
import { api, html, keepInside, T, update, useMenu } from "./lib.js";
import { FeedbackButton } from "./feedback.js";
import { NotifyButton } from "./notify.js";
import { applyTheme, remember, ThemeDialog } from "./themes.js";

const systemDark = () => matchMedia("(prefers-color-scheme: dark)").matches;
const currentMode = () => document.documentElement.dataset.theme || (systemDark() ? "dark" : "light");

/* mode: "light", "dark" or "system". */
function setMode(mode) {
  remember("theme", mode === "system" ? null : mode);
  applyTheme();
}

function useRedraw() {                              // the theme lives in the document, not in the state of the page
  const [, set] = useState(0);
  return () => set((n) => n + 1);
}

/* The language of the interface: a globe and the code of the language shown (FR, EN), its choices in a menu. */
function LanguageChoice({ onLanguage }) {
  const [open, setOpen, box] = useMenu();
  const list = useRef(null);
  useLayoutEffect(() => keepInside(list.current), [open]);
  if (I18N.available.length < 2) return null;
  const auto = pickLanguage(navigator.languages || [navigator.language], I18N.available);
  const change = async (setting) => {
    try {
      const config = await api("GET", "/api/config");
      await api("PUT", "/api/config", { ...config, ui_language: setting });
      await useLanguage(setting);
      update({});                                   // every text is redrawn in the new language
      if (onLanguage) onLanguage(setting);          // a page outside Preact (Settings) translates itself
    } catch (e) { update({ error: e.message }); }
  };
  const choices = [["", T("ui.lang_auto", { lang: autonym(auto) })], ...I18N.available.map((c) => [c, autonym(c)])];
  const label = `${T("ui.header.language")} : ${choices.find(([v]) => v === (I18N.setting || ""))?.[1] || ""}`;
  return html`
    <span class="lang-menu" ref=${box}>
      <button type="button" class="secondary lang-button" aria-haspopup="menu" aria-expanded=${open} title=${label} aria-label=${label}
              onClick=${() => setOpen(!open)}><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9s1.3-6.4 3.8-9z" /></svg><span>${I18N.language.toUpperCase()}</span></button>
      ${open && html`
        <div class="step-menu lang-list" ref=${list} role="menu" aria-label=${T("ui.header.language")}>
          ${choices.map(([v, name]) => html`
            <button key=${v} type="button" role="menuitemradio" aria-checked=${(I18N.setting || "") === v}
                    onClick=${() => { setOpen(false); change(v); }}><span class="lang-mark" aria-hidden="true">${(I18N.setting || "") === v ? "✓" : ""}</span>${name}</button>`)}
        </div>`}
    </span>`;
}

const zoomIcon = (plus) => html`
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
    <circle cx="10" cy="10" r="6.5" /><path d=${plus ? "M15 15l5.5 5.5M7 10h6M10 7v6" : "M15 15l5.5 5.5M7 10h6"} />
  </svg>`;

/* Two magnifiers, - and +: the size of the whole page (text, buttons and spaces; boot.js), kept in this browser. */
function ZoomButtons() {
  const redraw = useRedraw();
  useEffect(() => {
    addEventListener("textzoom", redraw);
    return () => removeEventListener("textzoom", redraw);
  }, []);
  const pct = Math.round(window.textZoom.get() * 100);
  return html`
    <button type="button" class="secondary icon zoom" title=${T("ui.zoom.out", { pct })} aria-label=${T("ui.zoom.out", { pct })}
            disabled=${!window.textZoom.canShrink()} onClick=${() => window.textZoom.step(-1)}>${zoomIcon(false)}</button>
    <button type="button" class="secondary icon zoom" title=${T("ui.zoom.in", { pct })} aria-label=${T("ui.zoom.in", { pct })}
            disabled=${!window.textZoom.canGrow()} onClick=${() => window.textZoom.step(1)}>${zoomIcon(true)}</button>`;
}

/* notify: the bell of the notifications is shown (web.notify_button of the settings, true by default); feedback: the
   button "Give my opinion" (web.feedback_button, true by default; it stays in the footer); settingsLink:
   the link to the Settings (not on the Settings page itself); onLanguage: called once the language changed. */
export function HeaderButtons({ notify = true, feedback = true, settingsLink = true, onLanguage }) {
  const redraw = useRedraw();
  const [themeOpen, setThemeOpen] = useState(false);
  useEffect(() => {                                 // the system's mode changes (evening): follow it
    const m = matchMedia("(prefers-color-scheme: dark)");
    const follow = () => { applyTheme(); redraw(); };
    m.addEventListener("change", follow);
    return () => m.removeEventListener("change", follow);
  }, []);
  const dark = currentMode() === "dark";
  const toggleTitle = dark ? T("ui.theme.to_light") : T("ui.theme.to_dark");
  return html`
    ${feedback && html`<${FeedbackButton} />`}
    <${ZoomButtons} />
    <button type="button" class="secondary theme" title=${toggleTitle} aria-label=${toggleTitle}
            onClick=${() => { setMode(dark ? "light" : "dark"); redraw(); }}><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2" /><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor" /></svg></button>
    <button type="button" class="secondary theme" title=${T("ui.theme.palettes")} aria-label=${T("ui.theme.palettes")}
            onClick=${() => setThemeOpen(true)}>🎨</button>
    ${notify && html`<${NotifyButton} />`}
    <${LanguageChoice} onLanguage=${onLanguage} />
    ${settingsLink && html`<a class="button-link secondary settings-link" href="/settings" title=${T("v2.settings_title")}
                             aria-label=${T("ui.settings")}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg></a>`}
    <${ThemeDialog} open=${themeOpen} close=${() => setThemeOpen(false)} redraw=${redraw} setMode=${setMode} />`;
}
