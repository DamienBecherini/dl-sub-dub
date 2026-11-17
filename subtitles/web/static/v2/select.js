/* Step 1, Select: addresses (videos, playlists), video files, folders and .txt lists, mixed, in one list where each
   video can be ticked or not. Before the project exists, the list lives in the page; afterwards, in the project
   (adding, ticking and removing go through the API, and the videos being worked on cannot be removed).

   An entry added shows at once as a line that is being read: its title comes within a fraction of a second for the
   sites that give it (YouTube, Dailymotion, Vimeo), then yt-dlp reads its details (several seconds per address, a
   few entries at a time) and the line becomes its video, or the videos of its playlist or folder. */
/* global I18N */
import { api, busy, duration, getState, html, isAddress, T, tickedStages, Tip, update } from "./lib.js";
import { useEffect, useRef, useState } from "/static/vendor/preact-htm.js";

/* A phone or a narrow window: the hint of the address field kept short (Shift+Enter means nothing on a phone, and
   the long one ran under the field). */
const narrow = () => matchMedia("(max-width: 560px), (pointer: coarse)").matches;

const READ_AT_ONCE = 3;                        // entries read by yt-dlp at the same time
const sameSource = (a, b) => a.replace(/\\/g, "/").toLowerCase() === b.replace(/\\/g, "/").toLowerCase();
let counter = 0;

async function refreshProject(id) {
  update({ project: await api("GET", `/api/projects/${id}`) });
}

/* The line being read, before the project exists in the selection (so the videos take its place), afterwards in
   `pending` (they go at the end of the project). */
function changeLine(key, change) {
  const apply = (list) => list.map((i) => (i.key === key ? { ...i, ...change } : i));
  update((st) => ({ selection: apply(st.selection), pending: apply(st.pending) }));
}

function dropLine(key) {
  update((st) => ({ selection: st.selection.filter((i) => i.key !== key), pending: st.pending.filter((i) => i.key !== key) }));
}

let creating = null;                           // the creation of the project under way (other lines wait for it)

/* The first video read creates the project, suspended: its settings can be set at once, and it can still be renamed
   and moved while nothing has started. */
async function createWith(r, line) {
  const s = getState();
  const hasAddresses = r.items.some((i) => isAddress(i.source)) || s.selection.some((i) => isAddress(i.source));
  const p = await api("POST", "/api/projects", {
    items: r.items.map(({ source, title, duration }) => ({ source, title, duration, selected: true })),
    stages: tickedStages(s, hasAddresses), title: s.title.trim() || r.title || "", start: false,
    // a place chosen by the user; otherwise the service's default for the videos read (the page's may be late)
    location: s.location.base.trim() || s.location.named != null ? projectLocation(s) : null,
  });
  update((st) => ({ projectId: p.id, project: p, selection: [], title: "", location: { base: "", named: null },
                    pending: [...st.pending, ...st.selection.filter((i) => i.pending && i.key !== line.key)] }));
}

/* The details of a line (several read at once); null when it gave nothing. */
async function readLine(line) {
  let r;
  try { r = await api("POST", "/api/sources", { entries: [line.entry] }); }
  catch (e) { changeLine(line.key, { error: e.message }); return null; }
  if (!r.items.length) { changeLine(line.key, { error: r.errors[0]?.error || T("v2.add.nothing") }); return null; }
  return r;
}

let turn = Promise.resolve();                 // the lines go into the list in the order they were added

/* A line read becomes its videos: the first one creates the project, the others are added to it. */
async function placeLine(line, r) {
  if (!r) return;
  if (!getState().projectId && !creating) {
    creating = createWith(r, line).finally(() => { creating = null; });
    try { await creating; } catch (e) { changeLine(line.key, { error: e.message }); }
    return;
  }
  if (creating) await creating.catch(() => {});
  const s = getState();
  if (s.projectId) {
    try {
      const done = await api("POST", `/api/projects/${s.projectId}/items`, { items: r.items });
      dropLine(line.key);
      await refreshProject(s.projectId);
      if (done.already?.length) {              // nothing new on the screen: say why, and which line holds it
        const a = done.already[0];
        update({ error: T({ address: "v2.add.already_web", origin: "v2.add.already_file" }[a.kind] || "v2.add.already", { n: done.already.length, title: a.title }) });
      }
    } catch (e) { changeLine(line.key, { error: e.message }); }
    return;
  }
  update((st) => {
    const fresh = r.items.filter((i) => !st.selection.some((x) => !x.pending && sameSource(x.source, i.source)))
      .map((i) => ({ ...i, key: i.source, selected: true }));
    const at = st.selection.findIndex((i) => i.key === line.key);
    const selection = [...st.selection];
    selection.splice(at < 0 ? selection.length : at, at < 0 ? 0 : 1, ...fresh);
    return { selection, title: st.title || r.title || "" };
  });
}

/* A line that could not be read (the service stopped or restarting, a site that did not answer…), read again: its
   videos still take its place in the list. */
function retryLine(line) {
  changeLine(line.key, { error: "" });
  const read = readLine(line);
  turn = turn.then(() => read).then((r) => placeLine(line, r)).catch(() => {});
}

/* Entries typed, chosen or dropped → lines shown at once, then read. */
export function addEntries(entries) {
  entries = entries.map((e) => e.trim()).filter((e) => e && !e.startsWith("#"));
  if (!entries.length) return false;
  const lines = entries.map((entry) => ({
    key: `reading-${++counter}`, entry, source: entry, title: "", pending: true, error: "", selected: true,
    kind: isAddress(entry) ? "address" : "file",
  }));
  update((st) => (st.projectId ? { pending: [...st.pending, ...lines] } : { selection: [...st.selection, ...lines] }));
  for (const l of lines.filter((x) => x.kind === "address")) {
    api("POST", "/api/sources/title", { url: l.entry })
      .then((r) => { if (r.title) changeLine(l.key, { title: r.title }); })
      .catch(() => { /* the details will give it */ });
  }
  const read = lines.map(() => { let done; const promise = new Promise((resolve) => { done = resolve; }); return { promise, done }; });
  let next = 0;
  const reader = async () => { while (next < lines.length) { const i = next++; read[i].done(await readLine(lines[i])); } };
  Promise.all(Array.from({ length: Math.min(READ_AT_ONCE, lines.length) }, reader));
  lines.forEach((line, i) => { turn = turn.then(() => read[i].promise).then((r) => placeLine(line, r)).catch(() => {}); });
  return true;
}

async function choose(type) {
  const d = await busy("", () => api("POST", "/api/dialog", { type }));
  const paths = d?.paths?.length ? d.paths : d?.path ? [d.path] : [];       // several files chosen at once (Ctrl)
  if (paths.length) addEntries(paths);
}

/* Dropped files: a .txt is read here (one address per line), at once; the videos are returned, for the window that
   asks before copying them (DropDialog). */
async function dropFiles(files) {
  const entries = [], videos = [];
  for (const f of files) {
    if (f.name.toLowerCase().endsWith(".txt")) entries.push(...(await f.text()).split(/\r?\n/));
    else videos.push(f);
  }
  if (entries.length) addEntries(entries);
  return videos;
}

/* The videos dropped, copied by the service into the output folder (the browser gives their content, not their
   path), then added. */
async function copyFiles(files) {
  const entries = [];
  for (const f of files) {
    const d = await busy(T("v2.add.uploading", { name: f.name }), async () => {
      const r = await fetch(`/api/files?name=${encodeURIComponent(f.name)}`, { method: "PUT", body: f });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || r.statusText);
      return j;
    });
    if (!d?.path) break;                           // refused (no room): the others neither
    entries.push(d.path);
  }
  if (entries.length) addEntries(entries);
}

const bytes = (n) => {
  const gb = n >= 2 ** 30;
  return new Intl.NumberFormat(I18N.language, { style: "unit", unit: gb ? "gigabyte" : "megabyte", maximumFractionDigits: gb ? 1 : 0 })
    .format(n / 2 ** (gb ? 30 : 20));
};

/* Before a copy: the browser does not give the path of a dropped file, so using it means copying it (where, how
   much, room left). Instead: choose the same files in the system's dialog box, or paste their path (no copy); or
   cancel. */
function DropDialog({ files, close }) {
  const ref = useRef(null);
  const [room, setRoom] = useState(null);
  const [path, setPath] = useState("");
  const size = files.reduce((n, f) => n + f.size, 0);
  useEffect(() => {
    ref.current.showModal();
    api("GET", `/api/files/room?size=${size}`).then(setRoom).catch((e) => setRoom({ enough: false, error: e.message }));
  }, []);
  const end = (then) => { ref.current.close(); close(); if (then) then(); };
  const usePath = () => { const lines = path.split(/\r?\n/); if (lines.some((l) => l.trim())) end(() => addEntries(lines)); };
  return html`
    <dialog ref=${ref} class="drop-dialog" onClose=${close} aria-labelledby="drop-title">
      <div class="head dialog-bar">
        <h2 id="drop-title" tabindex="-1">${T("v2.drop.title", { n: files.length })}</h2>
        <button type="button" class="close" aria-label=${T("ui.close")} onClick=${() => end()}>×</button>
      </div>
      <p>${T("v2.drop.why")}</p>
      <ul class="drop-files">
        ${files.map((f) => html`<li><span class="name">${f.name}</span> <span class="help">${bytes(f.size)}</span></li>`)}
      </ul>
      ${!room ? html`<p class="busy"><span class="spinner inline"></span> ${T("v2.drop.checking")}</p>`
        : room.error ? html`<p class="error" role="alert">${room.error}</p>`
        : html`<p>${T("v2.drop.where", { folder: room.folder, size: bytes(size) })}${room.free != null ? " " + T("v2.drop.free", { free: bytes(room.free) }) : ""}</p>`}
      <div class="actions">
        <button type="button" class="primary" disabled=${!room?.enough} onClick=${() => end(() => copyFiles(files))}>${T("v2.drop.copy")}</button>
        <button type="button" class="secondary" title=${T("v2.drop.pick_title")} onClick=${() => end(() => choose("file"))}>${T("v2.drop.pick")}</button>
        <button type="button" class="secondary" onClick=${() => end()}>${T("ui.cancel")}</button>
      </div>
      <label class="drop-path">${T("v2.drop.path_label")}
        <span class="row">
          <input type="text" value=${path} spellcheck="false" placeholder=${T("v2.drop.path_placeholder")}
                 onInput=${(e) => setPath(e.target.value)} onKeyDown=${(e) => { if (e.key === "Enter") usePath(); }} />
          <button type="button" class="secondary" disabled=${!path.trim()} onClick=${usePath}>${T("v2.add.button")}</button>
        </span>
      </label>
    </dialog>`;
}

async function setSelected(changes) {           // {key: ticked}
  const s = getState();
  if (s.projectId) {
    await busy("", async () => {
      await api("POST", `/api/projects/${s.projectId}/select`, { items: changes });
      await refreshProject(s.projectId);
    });
  } else {
    update((st) => ({ selection: st.selection.map((i) => (i.key in changes && !i.pending ? { ...i, selected: changes[i.key] } : i)) }));
  }
}

async function remove(item) {
  const s = getState();
  if (item.pending) { dropLine(item.key); return; }
  if (s.projectId) {
    await busy("", async () => {
      await api("POST", `/api/projects/${s.projectId}/remove`, { ids: [item.key] });
      await refreshProject(s.projectId);
    });
  } else {
    update((st) => ({ selection: st.selection.filter((i) => i.key !== item.key) }));
  }
}

/* A project opened again from its file (dl-sub-dub.project.json, or an old batch file): it becomes the project of
   the page, suspended; `start` resumes it at once. */
async function openProject(file) {
  const p = await busy(T("v2.reopen.opening"), async () => {
    const d = await api("POST", "/api/projects/load", { file });
    return api("GET", `/api/projects/${d.id}`);
  });
  if (p) update({ projectId: p.id, project: p, selection: [], pending: [], error: "", adding: null });    // opened: Add videos open again
}

async function chooseProject() {
  const d = await busy("", () => api("POST", "/api/dialog", { type: "project" }));
  if (d?.path) openProject(d.path);
}

/* The project left unfinished at the previous session of the service, if any: resume it, open it to change its
   settings first, or forget it (its file stays, "Open a project…" finds it). */
export function LastProject() {
  const [last, setLast] = useState(null);
  useEffect(() => { api("GET", "/api/projects/last").then((d) => setLast(d.last)).catch(() => {}); }, []);
  if (!last) return null;
  const forget = async () => { await api("POST", "/api/projects/last/forget", {}).catch(() => {}); setLast(null); };
  const was = { suspended: "v2.reopen.was_paused", awaiting_translation: "v2.reopen.was_waiting" }[last.state] || "v2.reopen.was_interrupted";
  return html`
    <div class="attention last-project">
      <strong>${T(was)}</strong>
      <span class="help">${last.root} · ${T("ui.last_project.done", { done: last.done, n: last.total })}${last.updated ? " · " + T("ui.last_project.last_activity", { date: last.updated }) : ""}</span>
      <span class="row">
        <button type="button" class="small" title=${T("v2.reopen.resume_title")} onClick=${() => openProject(last.file)}>${T("v2.reopen.resume")}</button>
        <button type="button" class="secondary small" title=${T("v2.reopen.forget_title")} onClick=${forget}>${T("ui.last_project.ignore")}</button>
      </span>
    </div>`;
}

export function AddBox() {
  const [text, setText] = useState("");
  const [over, setOver] = useState(false);
  const [dropped, setDropped] = useState(null);         // videos dropped, waiting for the choice of DropDialog
  const submit = () => { if (addEntries(text.split(/\r?\n/))) setText(""); };
  return html`
    <div class=${`add-box ${over ? "over" : ""}`}
         onDragOver=${(e) => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
         onDrop=${async (e) => { e.preventDefault(); setOver(false); const v = await dropFiles([...e.dataTransfer.files]); if (v.length) setDropped(v); }}>
      <label for="v2-entries">${T("v2.add.label")}</label>
      <div class="row">
        <textarea id="v2-entries" rows=${Math.min(6, Math.max(1, text.split("\n").length))} value=${text}
                  placeholder=${T(narrow() ? "v2.add.placeholder_short" : "v2.add.placeholder")} onInput=${(e) => setText(e.target.value)}
                  onKeyDown=${(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}></textarea>
        <button type="button" class="primary" onClick=${submit} disabled=${!text.trim()}>${T("v2.add.button")}</button>
      </div>
      <div class="row add-buttons">
        <button type="button" class="secondary" onClick=${() => choose("file")}>${T("v2.add.file")}</button>
        <button type="button" class="secondary" onClick=${() => choose("source_folder")}>${T("v2.add.folder")}</button>
        <button type="button" class="secondary" onClick=${() => choose("addresses")}>${T("v2.add.list")}</button>
        <button type="button" class="secondary" title=${T("v2.reopen.choose_title")} onClick=${chooseProject}>${T("v2.reopen.choose")}</button>
        <span class="help">${T("v2.add.drop")}</span>
      </div>
      ${dropped && html`<${DropDialog} files=${dropped} close=${() => setDropped(null)} />`}
    </div>`;
}

/* --- where the project goes: a base folder (typed or chosen), a subfolder named after the project or not --- */

/* Same rule as source.folder_name on the service: no character Windows refuses. */
function folderName(title) {
  const s = title.replace(/[<>:"/\\|?*\x00-\x1f]+/g, " ").replace(/\s+/g, " ").replace(/^[ .]+|[ .]+$/g, "");
  return s.slice(0, 100).replace(/[ .]+$/, "") || "playlist";
}

/* What the page sends when it creates the project: the choice, or the default; null before the default is known
   (the service then takes its own). */
export function projectLocation(s) {
  const d = s.suggested;
  const base = s.location.base.trim() || d?.base || "";
  const named = s.location.named ?? d?.named;
  return base && named != null ? { base, named } : null;
}

function joinPath(base, name) {
  const sep = base.includes("\\") && !base.includes("/") ? "\\" : "/";
  return base.replace(/[\\/]+$/, "") + sep + name;
}

async function pickFolder(start) {
  const d = await busy("", () => api("POST", "/api/dialog", { type: "project_folder", start }));
  return d?.path || "";
}

/* The name and the place of a project: `title`, `base` and `named` as typed ("" and null: the default `d`, {base,
   named, name}); `locked`: the project has started, only its name changes. `id` keeps the labels apart when the
   panel of the header and that of Select are open together. */
function LocationFields({ id, title, base, named, d, set, commit = () => {}, locked = false, reset = true }) {
  const b = base.trim() || d.base;
  const n = named ?? d.named;
  const result = b ? (n ? joinPath(b, folderName(title.trim() || d.name || "playlist")) : b) : "";
  return html`
    <div class="location">
      <label for=${id + "-name"}>${T("v2.project.name")}</label>
      <span class="location-row">
        <input id=${id + "-name"} type="text" value=${title} placeholder=${T("v2.project.name_placeholder", { name: d.name })}
               onInput=${(e) => set({ title: e.target.value })} onChange=${(e) => commit({ title: e.target.value })} />
        <label class="check">
          <input type="checkbox" checked=${n} disabled=${locked} onChange=${(e) => { set({ named: e.target.checked }); commit({ named: e.target.checked }); }} />
          ${T("v2.location.named")} <${Tip} text=${T("v2.tip.named")} />
        </label>
      </span>
      <label for=${id + "-base"}>${T("v2.location.base")}</label>
      <span class="location-row">
        <input id=${id + "-base"} type="text" value=${base || d.base} spellcheck="false" disabled=${locked}
               onInput=${(e) => set({ base: e.target.value })} onChange=${(e) => commit({ base: e.target.value })} />
        <button type="button" class="secondary small" disabled=${locked}
                onClick=${async () => { const p = await pickFolder(b); if (p) { set({ base: p }); commit({ base: p }); } }}>${T("v2.location.browse")}</button>
        ${reset && !locked && base && base !== d.base && html`
          <button type="button" class="secondary small" onClick=${() => set({ base: "" })} title=${d.base}>${T("v2.location.reset")}</button>`}
      </span>
      ${locked ? html`<p class="location-result">${T("v2.location.started")} <code>${result}</code></p>`
        : result && html`<p class="location-result" title=${T("v2.location.files_stay")}>→ <code>${result}</code></p>`}
    </div>`;
}

/* Before the project exists: shown from the start, its default following the lines added (an address being read is
   enough to know it: no need to wait for its details). */
function Location({ s, sources }) {
  const key = sources.join("\n");
  useEffect(() => {
    api("POST", "/api/sources/location", { sources })
      .then((d) => update({ suggested: d }))
      .catch(() => { /* the service takes its default */ });
  }, [key]);
  const set = ({ title, ...place }) => update((st) => ({ ...(title !== undefined ? { title } : {}), location: { ...st.location, ...place } }));
  return html`<${LocationFields} id="v2-project" title=${s.title} base=${s.location.base} named=${s.location.named}
                                 d=${s.suggested || { base: "", named: true, name: "" }} set=${set} />`;
}

/* The base folder and the subfolder of a project read back from its folder. */
function splitRoot(p) {
  const root = p.root.replace(/[\\/]+$/, "");
  const last = root.split(/[\\/]/).pop();
  const named = !!p.title && last === folderName(p.title);
  return { base: named ? root.slice(0, root.length - last.length - 1) : root, named };
}

/* A project that exists: renamed at any time, moved while nothing has started (POST /api/projects/{id}/location).
   `live`: each field saved as it changes (left, Enter, a box ticked), without Save / Cancel. */
export function ProjectEditor({ p, id, onClose = () => {}, live = false }) {
  const now = splitRoot(p);
  const [v, setV] = useState({ title: p.title || "", base: now.base, named: now.named });
  const save = async (x) => {
    const body = p.started ? { title: x.title } : { title: x.title, base: x.base.trim() || now.base, named: x.named ?? now.named };
    const r = await busy("", () => api("POST", `/api/projects/${p.id}/location`, body));
    if (r) { update({ project: r }); onClose(); }
  };
  const commit = (patch) => { if (live) save({ ...v, ...patch }); };
  return html`
    <div class="project-editor">
      <${LocationFields} id=${id} title=${v.title} base=${v.base} named=${v.named} d=${{ ...now, name: p.title }} locked=${p.started}
                         reset=${false} set=${(patch) => setV((x) => ({ ...x, ...patch }))} commit=${commit} />
      ${!live && html`
        <span class="row">
          <button type="button" class="small" onClick=${() => save(v)}>${T("v2.location.save")}</button>
          <button type="button" class="secondary small" onClick=${onClose}>${T("v2.location.cancel")}</button>
        </span>`}
    </div>`;
}

/* Once the project exists: folded on one line, unfolded to change it. */
function ProjectPlace({ p }) {
  const [open, setOpen] = useState(false);
  if (open) return html`<${ProjectEditor} p=${p} id="v2-place" onClose=${() => setOpen(false)} />`;
  return html`
    <p class="project-place">
      <span class="help">${T("v2.location.project")}</span>
      <strong>${p.title || "—"}</strong>
      <span class="help">→</span>
      <code title=${T("v2.location.files_stay")}>${p.root}</code>
      <button type="button" class="secondary small" title=${T("v2.location.edit_title")} onClick=${() => setOpen(true)}>${T("v2.location.edit")}</button>
      <button type="button" class="secondary small" title=${T("v2.location.open_title")}
              onClick=${() => api("POST", "/api/open", { path: p.root }).catch((e) => update({ error: e.status === 404 ? T("v2.location.not_yet") : e.message }))}>${T("v2.location.open")}</button>
    </p>`;
}

function ReadingRow({ item }) {
  return html`
    <li class=${`item reading ${item.error ? "failed" : ""}`} aria-busy=${!item.error}>
      ${item.error ? html`<span class="reading-mark" aria-hidden="true">!</span>` : html`<span class="spinner small" aria-hidden="true"></span>`}
      <span class=${`kind ${item.kind}`}>${T("v2.kind." + item.kind)}</span>
      <span class="item-title" title=${item.entry}>${item.title || item.entry}</span>
      <span class=${item.error ? "item-error-inline" : "item-origin"} title=${item.error}>${item.error || T("v2.add.reading")}</span>
      ${item.error ? html`<button type="button" class="secondary small" onClick=${() => retryLine(item)}>${T("v2.line.retry")}</button>` : html`<span></span>`}
      <button type="button" class="close" title=${T("v2.list.remove")} aria-label=${T("v2.list.remove")}
              onClick=${() => remove(item)}>×</button>
    </li>`;
}

function ItemRow({ item }) {
  if (item.pending) return html`<${ReadingRow} item=${item} />`;
  const locked = item.busy;                   // a video being worked on stays in the project
  return html`
    <li class=${`item ${item.selected ? "" : "unticked"}`}>
      <input type="checkbox" checked=${item.selected} indeterminate=${!!item.partial} disabled=${locked}
             title=${item.partial ? T("v2.list.partly_ticked") : ""} aria-label=${item.title || item.source}
             onChange=${(e) => setSelected({ [item.key]: e.target.checked })} />
      <span class=${`kind ${item.kind}`}>${T("v2.kind." + item.kind)}</span>
      <span class="item-title" title=${item.source}>${item.title || item.source}</span>
      ${item.origin ? html`<span class="item-origin">${item.origin}</span>` : html`<span></span>`}
      <span class="item-duration">${duration(item.duration)}</span>
      <button type="button" class="close" disabled=${locked} title=${T("v2.list.remove")} aria-label=${T("v2.list.remove")}
              onClick=${() => remove(item)}>×</button>
    </li>`;
}

/* The videos of the project (or of the selection, before it exists), each ticked or not, and the lines being read. */
export function VideoList({ items }) {
  const videos = items.filter((i) => !i.pending);
  const reading = items.filter((i) => i.pending && !i.error).length;
  const ticked = videos.filter((i) => i.selected).length;
  const keys = (on) => Object.fromEntries(videos.map((i) => [i.key, on]));
  return html`
    <div class="list-head">
      <span>${videos.length ? T("v2.list.count", { n: videos.length, ticked }) : ""}${reading > 0 ? (videos.length ? " · " : "") + T("v2.add.reading_count", { n: reading }) : ""}</span>
      ${videos.length > 0 && html`
        <button type="button" class="secondary small" onClick=${() => setSelected(keys(true))}>${T("ui.check_all")}</button>
        <button type="button" class="secondary small" onClick=${() => setSelected(keys(false))}>${T("ui.uncheck_all")}</button>`}
    </div>
    ${items.length
      ? html`<ul class="items">${items.map((i) => html`<${ItemRow} key=${i.key} item=${i} />`)}</ul>`
      : html`<p class="help empty">${T("v2.list.empty")}</p>`}`;
}

/* The name and the folder of the project: the fields before it exists; afterwards, `live` (the project, while nothing
   has started) the same fields saved as they change, otherwise one line that unfolds to change them. */
export function ProjectHead({ s, items, live = false }) {
  const p = s.project;
  if (!p) return s.projectId ? null : html`<${Location} s=${s} sources=${items.map((i) => i.source)} />`;
  if (live && !p.started) return html`<${ProjectEditor} key=${p.id} p=${p} id="v2-live" live=${true} />`;
  return html`<${ProjectPlace} key=${p.id + p.root + p.title} p=${p} />`;
}
