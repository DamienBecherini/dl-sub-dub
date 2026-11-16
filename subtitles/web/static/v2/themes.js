/* The Theme window: the mode (system, light, dark), then the palettes of each mode, built-in and the user's own.
   Every palette can be changed: its 4 colours (background, text, secondary text, accent; the others are derived, see
   boot.js), with a check of the contrasts against WCAG shown while choosing (AAA, AA, large text only, not enough);
   saving is allowed whatever the result. A built-in palette changed has "Back to factory"; a theme of the user can be
   deleted. Everything is kept in this browser (localStorage, keys in boot.js). */
import { useEffect, useRef, useState } from "/static/vendor/preact-htm.js";
import { html, T } from "./lib.js";

export const PALETTES = { light: ["slate", "sand", "sage", "lavender", "raspberry"], dark: ["night", "warm", "olive", "embers"] };
const COLORS = ["bg", "text", "muted", "accent"];

function read(key, fallback) {
  try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
}
function write(key, value) {
  try { if (value == null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* this page only */ }
}
export function remember(key, value) {
  try { if (value == null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch (e) { /* this page only */ }
}
const remembered = (key) => { try { return localStorage.getItem(key); } catch (e) { return null; } };

export const customThemes = () => read("custom-themes", {});
const overrides = () => read("palette-overrides", {});
const modeOfColors = (c) => (window.themes.isDark(c.bg) ? "dark" : "light");

/* The palettes of a mode: built-in ones, then the user's themes whose background belongs to that mode. */
export function palettesOf(mode) {
  const own = Object.entries(customThemes()).filter(([, t]) => modeOfColors(t.colors) === mode).map(([id]) => id);
  return [...PALETTES[mode], ...own];
}
export const paletteOf = (mode) => {
  const id = remembered("palette-" + mode);
  return palettesOf(mode).includes(id) ? id : PALETTES[mode][0];
};
export const applyTheme = () => window.themes.apply();

/* The colours of a built-in palette as style.css has them (read from a hidden swatch). */
function factory(id) {
  const probe = document.createElement("span");
  probe.className = "swatch";
  probe.dataset.palette = id;
  probe.hidden = true;
  document.body.appendChild(probe);
  const s = getComputedStyle(probe);
  const c = Object.fromEntries(COLORS.map((k) => [k, s.getPropertyValue("--" + k).trim()]));
  probe.remove();
  return c;
}
const colorsOf = (id) => window.themes.colorsOf(id) || factory(id);
const nameOf = (id) => customThemes()[id]?.name || T(`ui.palette.${id}`);

/* The contrasts that matter, from the colours derived: each pair with its ratio and its level, and the level of the
   whole. Text: AAA from 7, AA from 4.5, large text only from 3. Borders of the fields: 3 is the only level (WCAG 1.4.11). */
const LEVELS = ["fail", "large", "AA", "AAA"];
export function check(colors) {
  const v = window.themes.tokens(colors);
  const pairs = [["text_bg", "--text", "--bg"], ["text_card", "--text", "--card"], ["muted_card", "--muted", "--card"],
    ["muted_bg", "--muted", "--bg"], ["accent_card", "--accent", "--card"], ["button", "--accent-text", "--accent"],
    ["field", "--field-border", "--card", true]];
  const rows = pairs.map(([key, a, b, ui]) => {
    const ratio = window.themes.contrast(v[a], v[b]);
    const level = ui ? (ratio >= 3 ? "AAA" : "fail") : ratio >= 7 ? "AAA" : ratio >= 4.5 ? "AA" : ratio >= 3 ? "large" : "fail";
    return { key, ratio, level, ui };
  });
  const level = LEVELS[Math.min(...rows.map((r) => LEVELS.indexOf(r.level)))];
  return { rows, level };
}
const levelText = (level, ui) => (ui ? T(level === "fail" ? "ui.theme.check.fail" : "ui.theme.check.ui_ok") : T("ui.theme.check." + level));
/* Edit a palette: a pencil, its name in its label and bubble ("Edit Slate"). */
const pencil = html`<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4z" /><path d="m13.5 6.5 4 4" /></svg>`;
const Badge = ({ level }) => html`<span class=${`level level-${level}`}>${T("ui.theme.check." + level)}</span>`;

/* colors: those of a palette changed or of the user, null for a built-in one as it is. */
function Swatch({ id, colors }) {
  const vars = colors ? window.themes.tokens(colors) : "";       // changed or the user's: its own colours over style.css
  return html`<span class="swatch" data-palette=${id} style=${vars} aria-hidden="true"><i></i><i></i><i></i></span>`;
}

const HEX = /^#[0-9a-f]{6}$/i;

function ColorField({ k, value, set }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const id = `theme-color-${k}`;
  return html`
    <div class="color-field">
      <label for=${id}>${T("ui.theme.color." + k)}</label>
      <input type="color" id=${id} value=${value} onInput=${(e) => set(e.target.value)} />
      <input type="text" class="hex" value=${text} spellcheck="false" maxlength="7" aria-label=${T("ui.theme.color." + k) + " (hex)"}
             onInput=${(e) => { setText(e.target.value); if (HEX.test(e.target.value)) set(e.target.value.toLowerCase()); }} />
    </div>`;
}

/* The editor of one palette: {id, builtin, name, colors, isNew}. */
function Editor({ edit, done }) {
  const [colors, setColors] = useState(edit.colors);
  const [name, setName] = useState(edit.name);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const result = check(colors);
  const v = window.themes.tokens(colors);
  const mode = modeOfColors(colors);
  const changedBuiltin = edit.builtin && !!overrides()[edit.id];

  const save = () => {
    if (edit.builtin) {
      const o = overrides();
      const f = factory(edit.id);
      if (COLORS.every((k) => f[k].toLowerCase() === colors[k].toLowerCase())) delete o[edit.id];
      else o[edit.id] = colors;
      write("palette-overrides", o);
    } else {
      const all = customThemes();
      all[edit.id] = { name: name.trim() || T("ui.theme.untitled"), colors };
      write("custom-themes", all);
      remember("palette-" + mode, edit.id);           // a new or changed theme of the user: chosen for its mode
    }
    applyTheme();
    done();
  };
  const reset = () => {
    const o = overrides();
    delete o[edit.id];
    write("palette-overrides", o);
    applyTheme();
    done();
  };
  const remove = () => {
    const all = customThemes();
    delete all[edit.id];
    write("custom-themes", all);
    applyTheme();
    done();
  };

  return html`
    <div class="theme-editor">
      <h3>${edit.isNew ? T("ui.theme.new") : T("ui.theme.edit_title", { name: nameOf(edit.id) })}</h3>
      ${!edit.builtin && html`
        <label class="theme-name">${T("ui.theme.name")}
          <input type="text" value=${name} maxlength="40" placeholder=${T("ui.theme.untitled")} onInput=${(e) => setName(e.target.value)} /></label>`}
      <div class="color-fields">
        ${COLORS.map((k) => html`<${ColorField} key=${k} k=${k} value=${colors[k]} set=${(c) => setColors({ ...colors, [k]: c })} />`)}
      </div>
      <div class="theme-preview" style=${v}>
        <div class="preview-card">
          <strong>${T("ui.theme.preview_title")}</strong>
          <span class="preview-muted">${T("ui.theme.preview_muted")}</span>
          <span class="preview-link">${T("ui.theme.preview_link")}</span>
          <span class="preview-field">${T("ui.theme.preview_field")}</span>
          <span class="preview-button">${T("ui.theme.preview_button")}</span>
        </div>
      </div>
      <div class="theme-check" aria-live="polite">
        <p><span>${T("ui.theme.check.title")}</span> <${Badge} level=${result.level} /></p>
        <p class="help">${T(`ui.theme.check.help_${result.level}`)}</p>
        <ul>
          ${result.rows.map((r) => html`
            <li class=${`level-row level-row-${r.level}`}>
              <span>${T("ui.theme.pair." + r.key)}</span>
              <span class="ratio">${r.ratio.toFixed(1)}:1</span>
              <span>${levelText(r.level, r.ui)}</span>
            </li>`)}
        </ul>
        ${edit.builtin && mode !== edit.mode && html`<p class="help">${T(`ui.theme.mode_mismatch_${mode}`)}</p>`}
        ${!edit.builtin && html`<p class="help">${T(`ui.theme.goes_to_${mode}`)}</p>`}
      </div>
      <div class="actions">
        <button type="button" class="primary" onClick=${save}>${T("ui.save")}</button>
        <button type="button" class="secondary" onClick=${done}>${T("ui.cancel")}</button>
        <span class="spacer"></span>
        ${edit.builtin && html`<button type="button" class="secondary" disabled=${!changedBuiltin} title=${T("ui.theme.reset_title")}
                                       onClick=${reset}>${T("ui.theme.reset")}</button>`}
        ${!edit.builtin && !edit.isNew && (confirmDelete
          ? html`<button type="button" class="secondary danger" onClick=${remove}>${T("ui.theme.delete_confirm")}</button>`
          : html`<button type="button" class="secondary danger" onClick=${() => setConfirmDelete(true)}>${T("ui.theme.delete")}</button>`)}
      </div>
    </div>`;
}

export function ThemeDialog({ open, close, redraw, setMode }) {
  const ref = useRef(null);
  const [edit, setEdit] = useState(null);
  useEffect(() => {
    const d = ref.current;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
    if (!open) setEdit(null);
  }, [open]);
  const mode = document.documentElement.dataset.theme || "system";
  const choose = (fn) => { fn(); redraw(); };
  const startEdit = (id, m) => setEdit({ id, mode: m, builtin: PALETTES[m].includes(id), name: customThemes()[id]?.name || "",
                                        colors: { ...colorsOf(id) }, isNew: false });
  const startNew = (m) => setEdit({ id: "theme-" + Date.now().toString(36), mode: m, builtin: false, name: "",
                                   colors: { ...colorsOf(paletteOf(m)) }, isNew: true });
  return html`
    <dialog ref=${ref} class="theme-dialog" onClose=${close}
            onClick=${(e) => { if (e.target === ref.current) close(); }}>
      <div class="head dialog-bar">
        <h2>${T("ui.theme.title")}</h2>
        <button type="button" class="close" aria-label=${T("ui.close")} onClick=${close}>×</button>
      </div>
      ${edit ? html`<${Editor} key=${edit.id} edit=${edit} done=${() => { setEdit(null); redraw(); }} />` : html`
        <h3>${T("ui.theme.mode")}</h3>
        <div class="checks">
          ${["system", "light", "dark"].map((m) => html`
            <label class="check"><input type="radio" name="v2-theme-mode" checked=${mode === m}
                   onChange=${() => choose(() => setMode(m))} /> ${T("ui.theme." + m)}</label>`)}
        </div>
        ${["light", "dark"].map((m) => html`
          <h3>${T(`ui.theme.${m}_palettes`)}
            <button type="button" class="secondary small" onClick=${() => startNew(m)}>${T("ui.theme.new_button")}</button></h3>
          <div class="palettes">
            ${palettesOf(m).map((id) => {
              const builtin = PALETTES[m].includes(id);
              const changed = builtin && !!overrides()[id];
              return html`
                <div class="palette-card">
                  <label class="palette">
                    <input type="radio" name=${`v2-palette-${m}`} checked=${paletteOf(m) === id}
                           onChange=${() => choose(() => { remember("palette-" + m, id); applyTheme(); })} />
                    <${Swatch} id=${id} colors=${window.themes.colorsOf(id)} />
                    <span><span class="name">${nameOf(id)}</span>
                      <span class="desc">${builtin ? T(`ui.palette.${id}_desc`) : T("ui.theme.yours")}${changed ? " · " + T("ui.theme.changed") : ""}</span></span>
                    <${Badge} level=${check(colorsOf(id)).level} />
                  </label>
                  <button type="button" class="secondary icon edit" aria-label=${T("ui.theme.edit_title", { name: nameOf(id) })}
                          title=${T("ui.theme.edit_title", { name: nameOf(id) })} onClick=${() => startEdit(id, m)}>${pencil}</button>
                </div>`;
            })}
          </div>`)}
        <p class="help">${T("ui.theme.help")}</p>`}
    </dialog>`;
}
