/* The Settings page, by categories as a phone's (a list, one page each; side by side on a computer; the page in the
   hash of the address): the defaults of a new project (languages, order, steps, pause, review, MKV), the translators
   and the transcription servers (each with its test), the dubbing, the downloads, the layout of the subtitles, the
   folders and their cleanup, the application. Everything is saved at once in the user's settings (PUT /api/config,
   which refuses a plain key). */
/* global I18N, T, label, translatePage, formatNumber, loadTranslations */
"use strict";

const $ = (s) => document.querySelector(s);
let config = null;
let savedState = "";

async function api(method, path, body) {
  let r;
  try {
    r = await fetch(path, { method, headers: { "X-Language": I18N.language, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
                            body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch (e) {               // no answer at all (the service stopped, or restarts): said plainly
    throw new Error(T("ui.service_down"));
  }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `${r.status} ${r.statusText}`);
  return d;
}

let dialogOpen = false;                    // one dialog box at a time: the clicks while one is open are ignored
async function choosePath(type, startDir = "") {
  if (dialogOpen) return "";
  dialogOpen = true;
  try { return (await api("POST", "/api/dialog", { type, start: startDir })).path || ""; }
  catch (err) { $("#settings-message").textContent = err.message; return ""; }
  finally { dialogOpen = false; }
}

/* Everything the form (or one category of it) holds, to know whether something was changed and not saved (engines
   added or deleted included). The folder of the data is not part of it: it moves with its own button. */
const valuesOf = (root) => JSON.stringify([...root.querySelectorAll("input, select")].filter((e) => !e.closest("[data-no-save]"))
  .map((e) => (e.type === "checkbox" || e.type === "radio" ? e.checked : e.value)));
const formState = () => valuesOf($("#form-settings"));

/* The languages of a new project: a box each (the common ones), the others typed. */
const LANGUAGES = ["fr", "en", "de", "es", "it", "pt", "nl"];
function fillLanguages(codes) {
  const box = $("#cfg-target-chips");
  box.innerHTML = "";
  for (const c of LANGUAGES) {
    const l = document.createElement("label");
    l.className = "check";
    l.innerHTML = `<input type="checkbox" data-code="${c}"> `;
    l.append(languageName(c));
    l.querySelector("input").checked = codes.includes(c);
    box.appendChild(l);
  }
  const o = document.createElement("label");
  o.className = "check others";
  o.append(T("v2.sub.others"), " ");
  const input = document.createElement("input");
  input.type = "text"; input.id = "cfg-target-others"; input.placeholder = "pl, ru…"; input.spellcheck = false;
  input.value = codes.filter((c) => !LANGUAGES.includes(c)).join(", ");
  o.appendChild(input);
  box.appendChild(o);
}
const readLanguages = () => [...[...document.querySelectorAll("#cfg-target-chips input[data-code]")].filter((i) => i.checked).map((i) => i.dataset.code),
  ...$("#cfg-target-others").value.split(/[\s,;]+/).map((x) => x.trim().toLowerCase()).filter(Boolean)];

function fillSettings() {
  const c = config, s = c.subtitles || {};
  $("#cfg-output-dir").value = c.output_dir || ""; $("#cfg-language").value = c.language || "";
  const y = c.youtube || {};
  $("#cfg-youtube").value = JSON.stringify(y.command || ["yt-dlp"]);
  $("#cfg-download-dir").value = y.download_dir || "";
  const height = String(y.max_height ?? 1080), heights = $("#cfg-max-height");
  if (![...heights.options].some((o) => o.value === height)) heights.add(new Option(`${height}p`, height));
  heights.value = height;
  $("#cfg-ytdlp-extra").value = (y.extra_args || []).map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(" ");
  $("#cfg-chunk").value = (c.translation || {}).chunk_size || 25;
  $("#cfg-idle").value = (c.web || {}).idle_min ?? 60;
  $("#cfg-notify-button").checked = (c.web || {}).notify_button !== false;
  $("#cfg-feedback-button").checked = (c.web || {}).feedback_button !== false;
  $("#cfg-compact-meta").value = (c.web || {}).compact_meta === "inline" ? "inline" : "below";
  $("#cfg-cpl").value = s.chars_per_line ?? 42; $("#cfg-lines").value = s.lines ?? 2; $("#cfg-dmax").value = s.max_duration ?? 6;
  $("#cfg-dmin").value = s.min_duration ?? 1; $("#cfg-pause").value = s.split_pause ?? 0.6; $("#cfg-cps").value = s.reading_cps ?? 17;
  const n = { audio: true, subtitles: true, transcription: false, logs: false, logs_age_h: 48, dubbing: false, max_age_days: 0, max_size_mb: 0,
               ...(c.cleanup || {}) };
  $("#cfg-clean-audio").checked = !!n.audio; $("#cfg-clean-subtitles").checked = !!n.subtitles;
  $("#cfg-clean-transcription").checked = !!n.transcription; $("#cfg-clean-logs").checked = !!n.logs; $("#cfg-clean-dubbing").checked = !!n.dubbing;
  const hours = Number(n.logs_age_h) || 0;                // kept in hours; shown in months, days or hours
  const unit = hours > 0 && hours % 720 === 0 ? 720 : hours > 0 && hours % 24 === 0 ? 24 : 1;
  $("#cfg-clean-age").value = hours / unit; $("#cfg-clean-age-unit").value = String(unit);
  $("#cfg-work-days").value = n.max_age_days || 0; $("#cfg-work-mb").value = n.max_size_mb || 0;
  fillLanguages(c.target_languages || ["fr"]);
  const pr = c.projects || {};
  $(pr.order === "videos" ? "#cfg-proj-order-videos" : "#cfg-proj-order-steps").checked = true;
  $("#cfg-proj-dubbing").checked = !!pr.dubbing;
  $("#cfg-proj-review").value = pr.review === true ? "names" : ["names", "full"].includes(pr.review) ? pr.review : "";     // true: written before the full review
  const reviewer = $("#cfg-proj-review-engine");           // any translator that is a model; "": chosen by the service
  reviewer.replaceChildren(new Option(T("v2.sub.review_auto_plain"), ""),
    ...(c.translators || []).filter((m) => (m.type || "openai") !== "none").map((m) => new Option(m.name || m.id, m.id)));
  reviewer.value = [...reviewer.options].some((o) => o.value === pr.review_engine) ? pr.review_engine : "";
  $("#cfg-proj-review-auto").checked = pr.review_auto === true;
  $("#cfg-proj-pause").value = pr.pause === true ? "on" : "";      // false, of older versions ("never"): a paid model always asks now
  $("#cfg-proj-mkv").checked = pr.mkv !== false; $("#cfg-proj-replace").checked = !!pr.replace;
  $("#cfg-dest-next-to").checked = c.destination?.next_to_video ?? true;
  $("#cfg-dest-output").checked = c.destination?.output_dir ?? false;
  const d = c.dubbing || {};
  $("#cfg-dub-enabled").checked = !!d.enabled; $("#cfg-dub-wsl").checked = !!d.wsl;
  $("#cfg-dub-command").value = JSON.stringify(d.command || ["python"]); $("#cfg-dub-model").value = d.model || "";
  $("#cfg-dub-steps").value = d.steps ?? 32;
  $("#cfg-dub-device").value = ["cuda", "mps", "cpu"].includes(d.device) ? d.device : "auto";
  showCleanupSummary();
  const lt = $("#transcription-list"); lt.innerHTML = "";
  for (const m of c.transcriptions || []) lt.appendChild(engineRow("transcription", m, m.id === c.active_transcription));
  showDefault(lt);
  const ld = $("#translator-list"); ld.innerHTML = "";
  for (const m of c.translators || []) ld.appendChild(engineRow("translator", m, m.id === c.active_translator));
  showDefault(ld);
  offerClaudeCode();
  $("#settings-message").textContent = "";
  savedState = formState();
  for (const p of document.querySelectorAll(".cfg-page")) savedPages[p.dataset.cat] = valuesOf(p);
  showSummaries();
}

// --- the categories: their values in a line, a dot when changed, the page shown ---
const savedPages = {};
function showSummaries() {
  const v = (sel) => $(sel).value.trim();
  const names = (list) => [...document.querySelectorAll(`${list} .engine`)].map((e) => e.querySelector('[data-field="name"]').value.trim()
    || e.querySelector('[data-field="id"]').value.trim()).filter(Boolean).join(" · ") || "—";
  const langs = readLanguages().map((c) => languageName(c) || c);
  const sums = {
    projects: [langs.join(", ") || "—", T($("#cfg-proj-order-videos").checked ? "v2.order.videos" : "v2.order.steps").toLowerCase()].join(" · "),
    translation: names("#translator-list"),
    transcription: names("#transcription-list"),
    dubbing: $("#cfg-dub-enabled").checked ? T("ui.cfg.sum.dub_on", { n: v("#cfg-dub-steps") || 32 }) : T("ui.cfg.sum.off"),
    downloads: [$("#cfg-max-height").selectedOptions[0]?.textContent || "", v("#cfg-download-dir")].filter(Boolean).join(" · "),
    layout: T("ui.cfg.sum.layout", { n: Number(v("#cfg-lines")), lines: v("#cfg-lines"), cpl: v("#cfg-cpl") }),
    folders: v("#cfg-output-dir") || "—",
    app: [Number(v("#cfg-idle")) ? T("ui.cfg.sum.idle", { n: v("#cfg-idle") }) : T("ui.cfg.sum.no_idle"),
          $("#cfg-notify-button").checked ? T("ui.cfg.sum.notify") : "", $("#cfg-feedback-button").checked ? T("ui.cfg.sum.feedback") : ""].filter(Boolean).join(" · "),
  };
  for (const [cat, text] of Object.entries(sums)) {
    const el = document.querySelector(`.cfg-sum[data-sum="${cat}"]`);
    if (el) { el.textContent = text; el.title = text; }
  }
  let changed = false;
  for (const p of document.querySelectorAll(".cfg-page")) {
    const dot = document.querySelector(`.cfg-cat[data-cat="${p.dataset.cat}"] .cfg-dot`);
    if (dot) dot.hidden = valuesOf(p) === savedPages[p.dataset.cat];
    changed ||= dot ? !dot.hidden : false;
  }
  $(".cfg-legend").hidden = !changed;          // the legend of the dot, only when a dot is shown (alone, it read as a state)
}

/* The page of the hash (#translation); none on a phone: the list; none on a computer: the first one. */
const phone = matchMedia("(max-width: 760px)");
function showCategory(focus) {
  const pages = [...document.querySelectorAll(".cfg-page")];
  const want = decodeURIComponent(location.hash.slice(1));
  const cat = pages.some((p) => p.dataset.cat === want) ? want : phone.matches ? "" : pages[0].dataset.cat;
  $("#form-settings").classList.toggle("cfg-listing", !cat);
  for (const p of pages) p.hidden = p.dataset.cat !== cat;
  for (const a of document.querySelectorAll(".cfg-cat")) {
    if (a.dataset.cat === cat) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (focus && cat) {
    document.getElementById(`cat-${cat}-title`)?.focus({ preventScroll: true });
    if (phone.matches) scrollTo(0, 0);
  }
}

/* Claude Code installed on this machine and no translator that uses it yet: proposed above the list (it translates and
   reviews on the Claude subscription of the user, without an API key). */
let claudeCode = null;
async function offerClaudeCode() {
  try { claudeCode ??= await api("GET", "/api/claude-code"); } catch { claudeCode = { installed: false }; }
  const there = [...document.querySelectorAll("#translator-list [data-field=type]")].some((f) => f.value === "claude_code");
  $("#claude-code-offer").hidden = !claudeCode.installed || there;
}

function engineRow(kind, m, active) {
  const el = document.getElementById(kind === "transcription" ? "tpl-transcription" : "tpl-translator").content.firstElementChild.cloneNode(true);
  translatePage(el);
  for (const field of el.querySelectorAll("[data-field]")) {
    const k = field.dataset.field;
    if (k === "active") field.checked = active;
    else if (field.type === "checkbox") field.checked = !!m[k];
    else if (k === "command") field.value = m.command ? JSON.stringify(m.command) : "";
    else field.value = m[k] ?? "";
  }
  const title = () => { el.querySelector("[data-role=title]").textContent = readEngine(el).name || readEngine(el).id || T("ui.eng.unnamed"); };
  title();
  for (const f of el.querySelectorAll("[data-field=id], [data-field=name]")) f.addEventListener("input", title);
  el.querySelector("[data-action=default]").addEventListener("click", () => setDefault(el));
  el.querySelector("[data-action=delete]").addEventListener("click", () => {
    const list = el.parentElement;
    el.remove();
    showDefault(list);                                   // the default deleted: the first one takes its place
  });
  el.classList.toggle("is-default", active);
  el.querySelector("[data-action=test]").addEventListener("click", async (ev) => {
    const feedback = el.querySelector("[data-role=feedback]");
    feedback.textContent = T("ui.eng.testing"); ev.target.disabled = true;
    try {
      const r = await api("POST", "/api/test", { kind, engine: readEngine(el) });
      feedback.textContent = (r.ok ? "✓ " : "✗ ") + r.detail;
    } catch (err) { feedback.textContent = "✗ " + err.message; }
    finally { ev.target.disabled = false; }
  });
  return el;
}

/* The default engine of a list: the one used when a project chooses none. Its card carries the label, the others the
   button "Use by default"; with none chosen (the default deleted), the first one. */
function setDefault(el) {
  for (const other of el.parentElement.querySelectorAll(".engine")) other.querySelector("[data-field=active]").checked = other === el;
  showDefault(el.parentElement);
}

function showDefault(list) {
  const engines = [...list.querySelectorAll(".engine")];
  if (engines.length && !engines.some((e) => e.querySelector("[data-field=active]").checked)) engines[0].querySelector("[data-field=active]").checked = true;
  for (const e of engines) e.classList.toggle("is-default", e.querySelector("[data-field=active]").checked);
}

/* Options typed like on a command line: words separated by spaces, "quotes" around a word that holds some. */
function splitArguments(text) {
  return (text.match(/"[^"]*"|'[^']*'|\S+/g) || []).map((a) => (/^(["']).*\1$/s.test(a) ? a.slice(1, -1) : a));
}

function readEngine(el) {
  const m = {};
  for (const field of el.querySelectorAll("[data-field]")) {
    const k = field.dataset.field;
    if (k === "active") continue;
    if (field.type === "checkbox") m[k] = field.checked;
    else if (k === "command") { const v = field.value.trim(); if (v) m.command = JSON.parse(v); }
    else { const v = field.value.trim(); if (v || k === "id" || k === "name" || k === "key") m[k] = v; }
  }
  return m;
}

function readEngines(sel) {
  const list = [];
  let active = null;
  for (const el of document.querySelectorAll(sel + " .engine")) {
    const m = readEngine(el);
    if (!m.id) throw new Error(T("ui.cfg.id_required"));
    if (list.some((x) => x.id === m.id)) throw new Error(T("ui.cfg.duplicate_id", { id: m.id }));
    if (el.querySelector("[data-field=active]").checked) active = m.id;
    list.push(m);
  }
  return [list, active || list[0]?.id || null];
}

async function save() {
  const msg = $("#settings-message");
  try {
    const c = JSON.parse(JSON.stringify(config));
    c.output_dir = $("#cfg-output-dir").value.trim(); c.language = $("#cfg-language").value.trim();
    c.youtube = { ...(c.youtube || {}), command: JSON.parse($("#cfg-youtube").value), download_dir: $("#cfg-download-dir").value.trim(),
                  max_height: Number($("#cfg-max-height").value) || 0, extra_args: splitArguments($("#cfg-ytdlp-extra").value) };
    c.translation = { ...(c.translation || {}), chunk_size: Number($("#cfg-chunk").value) || 25 };
    c.web = { ...(c.web || {}), idle_min: Number($("#cfg-idle").value) || 0, notify_button: $("#cfg-notify-button").checked,
              feedback_button: $("#cfg-feedback-button").checked,
              compact_meta: $("#cfg-compact-meta").value };
    c.subtitles = { chars_per_line: Number($("#cfg-cpl").value), lines: Number($("#cfg-lines").value), max_duration: Number($("#cfg-dmax").value),
                    min_duration: Number($("#cfg-dmin").value), split_pause: Number($("#cfg-pause").value), min_gap: (c.subtitles || {}).min_gap ?? 0.08,
                    reading_cps: Number($("#cfg-cps").value) };
    c.cleanup = readCleanup();
    c.target_languages = readLanguages();
    const pause = $("#cfg-proj-pause").value;
    c.projects = { ...(c.projects || {}), order: $("#cfg-proj-order-videos").checked ? "videos" : "steps", dubbing: $("#cfg-proj-dubbing").checked,
                   review: $("#cfg-proj-review").value, replace: $("#cfg-proj-replace").checked };
    if ($("#cfg-proj-review-engine").value) c.projects.review_engine = $("#cfg-proj-review-engine").value; else delete c.projects.review_engine;
    if ($("#cfg-proj-review-auto").checked) c.projects.review_auto = true; else delete c.projects.review_auto;   // absent: every fix is decided
    if (pause === "on") c.projects.pause = true; else delete c.projects.pause;                 // absent: only before a paid model
    if ($("#cfg-proj-mkv").checked) delete c.projects.mkv; else c.projects.mkv = false;      // absent: the MKV
    c.destination = { next_to_video: $("#cfg-dest-next-to").checked, output_dir: $("#cfg-dest-output").checked };
    c.dubbing = { ...(c.dubbing || {}), enabled: $("#cfg-dub-enabled").checked, wsl: $("#cfg-dub-wsl").checked,
                  command: JSON.parse($("#cfg-dub-command").value || '["python"]'), model: $("#cfg-dub-model").value.trim(),
                  steps: Number($("#cfg-dub-steps").value) || 32, device: $("#cfg-dub-device").value };
    [c.transcriptions, c.active_transcription] = readEngines("#transcription-list");
    [c.translators, c.active_translator] = readEngines("#translator-list");
    config = await api("PUT", "/api/config", c);
    fillSettings();
    msg.textContent = T("ui.cfg.saved");
    savedState = formState();                            // saved: back to the page (an error keeps the Settings open)
    location.href = "/";
  } catch (err) { msg.textContent = T("log.error", { error: err.message }); }
}

// --- cleanup of the working folders ---
function readCleanup() {
  return { audio: $("#cfg-clean-audio").checked, subtitles: $("#cfg-clean-subtitles").checked, transcription: $("#cfg-clean-transcription").checked,
           logs: $("#cfg-clean-logs").checked, logs_age_h: (Number($("#cfg-clean-age").value) || 0) * Number($("#cfg-clean-age-unit").value),
           dubbing: $("#cfg-clean-dubbing").checked, max_age_days: Number($("#cfg-work-days").value) || 0,
           max_size_mb: Number($("#cfg-work-mb").value) || 0 };
}

function formatBytes(o) {
  return o >= 1e9 ? T("ui.size.gb", { n: formatNumber(o / 1e9, { maximumFractionDigits: 2 }) })
    : o >= 1e6 ? T("ui.size.mb", { n: formatNumber(Math.round(o / 1e6)) }) : T("ui.size.kb", { n: formatNumber(Math.round(o / 1e3)) });
}

function showSummary(categories) {
  for (const el of document.querySelectorAll("[data-summary]")) {
    const b = categories[el.dataset.summary];
    el.textContent = b ? `(${T("ui.cfg.files", { n: b.files })}, ${formatBytes(b.bytes)})` : "";
  }
}

async function showCleanupSummary() {
  try { showSummary((await api("GET", "/api/cleanup?output_dir=" + encodeURIComponent($("#cfg-output-dir").value.trim()))).categories); }
  catch (err) { $("#cleanup-feedback").textContent = err.message; }
}

async function cleanNow(ev) {
  const feedback = $("#cleanup-feedback");
  ev.target.disabled = true; feedback.textContent = T("ui.cfg.cleaning");
  try {
    const r = await api("POST", "/api/cleanup", { options: readCleanup(), output_dir: $("#cfg-output-dir").value.trim() });
    const total = Object.values(r.deleted).reduce((a, b) => ({ files: a.files + b.files, bytes: a.bytes + b.bytes }), { files: 0, bytes: 0 });
    feedback.textContent = total.files ? T("ui.cfg.cleaned", { n: total.files, size: formatBytes(total.bytes) }) : T("ui.cfg.nothing_to_clean");
    showSummary(r.categories);
  } catch (err) { feedback.textContent = T("ui.failed", { error: err.message }); }
  finally { ev.target.disabled = false; }
}

/* The folder of the settings and the history: moved at once (not with Save), after a confirmation; a folder that
   already holds settings is offered to be used instead. The page is filled again from the settings now in use. */
async function showDataDir() {
  try { $("#cfg-data-dir").value = (await api("GET", "/api/data-location")).current; }
  catch (err) { $("#data-dir-feedback").textContent = err.message; }
}

async function moveDataDir(ev) {
  const feedback = $("#data-dir-feedback");
  if (formState() !== savedState) { feedback.textContent = T("ui.cfg.data_dir_unsaved"); return; }
  const path = $("#cfg-data-dir").value.trim();
  ev.target.disabled = true;
  try {
    const info = await api("POST", "/api/data-location/check", { path });
    if (info.error) { feedback.textContent = info.error; return; }
    if (info.same) { feedback.textContent = ""; return; }
    let action = "move";
    if (info.has_data) {
      if (!confirm(T("ui.cfg.data_dir_use", { path: info.path }))) return;
      action = "use";
    } else if (!confirm(T("ui.cfg.data_dir_confirm", { path: info.path }))) return;
    feedback.textContent = T("ui.cfg.data_dir_moving");
    const r = await api("POST", "/api/data-location", { path: info.path, action });
    $("#cfg-data-dir").value = r.current;
    config = await api("GET", "/api/config");
    fillSettings();
    savedState = formState();
    feedback.textContent = r.message;
  } catch (err) { feedback.textContent = T("ui.failed", { error: err.message }); }
  finally { ev.target.disabled = false; }
}

// --- the page ---
/* Back to where one came from (the page of the four steps), after asking if changes would be lost. */
function leave() {
  if (formState() !== savedState && !confirm(T("ui.cfg.discard"))) return;
  savedState = formState();
  location.href = "/";
}

(async () => {
  await loadTranslations();
  translatePage(document);
  document.title = `${T("ui.settings")} · dl-sub-dub`;
  config = await api("GET", "/api/config");
  fillSettings();
  api("GET", "/api/ytdlp").then((i) => {                  // version and age of yt-dlp, how to update it (the page offers it)
    $("#ytdlp-version").textContent = !i.found ? T("ui.cfg.ytdlp_missing")
      : T("ui.cfg.ytdlp_version", { version: `${i.channel} ${i.version}`, n: i.age_days }) + " " + (i.self_update ? T("ui.cfg.ytdlp_self") : i.hint);
  }).catch(() => {});
  for (const [button, field, type] of [["#btn-cfg-output-dir", "#cfg-output-dir", "folder"], ["#btn-cfg-download-dir", "#cfg-download-dir", "download_folder"]]) {
    $(button).addEventListener("click", async () => {
      const path = await choosePath(type, $(field).value.trim() || $("#cfg-output-dir").value.trim());
      if (path) $(field).value = path;
    });
  }
  $("#btn-add-transcription").addEventListener("click", () => {          // the first one of an empty list: the default
    $("#transcription-list").appendChild(engineRow("transcription", { id: "", name: "", url: "", key: "" }, false));
    showDefault($("#transcription-list"));
  });
  $("#btn-add-translator").addEventListener("click", () => {
    $("#translator-list").appendChild(engineRow("translator", { id: "", name: "", type: "openai", model: "", url: "", key: "" }, false));
    showDefault($("#translator-list"));
  });
  $("#btn-add-claude-code").addEventListener("click", () => {
    $("#translator-list").appendChild(engineRow("translator",
      { id: "claude-code", name: T("ui.cfg.claude_code_name"), type: "claude_code", model: "sonnet", review_effort: "medium", key: "" }, false));
    showDefault($("#translator-list"));
    offerClaudeCode();
    showSummaries();
  });
  $("#form-settings").addEventListener("submit", (e) => e.preventDefault());   // Enter in a field: nothing sent
  $("#btn-save-settings").addEventListener("click", save);
  $("#btn-cancel-settings").addEventListener("click", () => { savedState = formState(); location.href = "/"; });   // changes dropped
  $("#btn-clean").addEventListener("click", cleanNow);
  showDataDir();
  $("#btn-cfg-data-dir").addEventListener("click", async () => {
    const path = await choosePath("data_folder", $("#cfg-data-dir").value.trim());
    if (path) $("#cfg-data-dir").value = path;
  });
  $("#btn-move-data-dir").addEventListener("click", moveDataDir);
  addEventListener("hashchange", () => showCategory(true));
  phone.addEventListener("change", () => showCategory(false));
  showCategory(false);
  for (const type of ["input", "change"]) $("#form-settings").addEventListener(type, showSummaries);
  $("#form-settings").addEventListener("click", (e) => { if (e.target.closest("button")) setTimeout(showSummaries); });   // an engine added, deleted
  $("#cfg-proj-replace").addEventListener("change", (e) => {     // the original video goes to the recycle bin: asked first
    if (e.target.checked && !confirm(T("v2.dub.replace_confirm"))) e.target.checked = false;
  });
  $("#back-link").addEventListener("click", (e) => { e.preventDefault(); leave(); });
  addEventListener("beforeunload", (e) => { if (formState() !== savedState) e.preventDefault(); });
  const headerHeight = () => {                            // sticky header (wide screens): the bar of the form goes under it
    const h = document.querySelector("header");
    document.documentElement.style.setProperty("--header-h", getComputedStyle(h).position === "sticky" ? `${h.offsetHeight}px` : "0px");
  };
  headerHeight();
  addEventListener("resize", headerHeight);
  addEventListener("textzoom", headerHeight);
  import("/static/v2/settings-header.js").then((m) => m.mount($("#header-buttons"), (code) => {
    config.ui_language = code;                           // what Save sends: not the language before the change
    translatePage(document);
    document.title = `${T("ui.settings")} · dl-sub-dub`;
    headerHeight();
  })).catch(() => {}).finally(() => { window.pageReady(); headerHeight(); });   // shown with its header (boot.js)
})();
