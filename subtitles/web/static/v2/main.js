/* The page: a header (the project, the settings of the service), the workshop (one list, each video with its own boxes
   for the steps, its own buttons and settings), and the button that runs the steps ticked for the project (Pause and
   Stop in its place while it runs), with the order of the work next to it.

   The project is created by the first video read (select.js); it is then read from the service as it changes. */
/* global loadTranslations, translatePage */
import { render, useEffect } from "/static/vendor/preact-htm.js";
import { HeaderButtons } from "./header.js";
import { useNotifications } from "./notify.js";
import { api, busy, getState, html, itemsOf, stateKey, T, tickedStages, update, useMenu, useStore } from "./lib.js";
import { Finishing, RunControls, RunEstimate } from "./stage.js";
import { StepMenu, WorkshopScreen } from "./workshop.js";
import { YtdlpNotice } from "./ytdlp.js";

/* The steps Run runs, said by the service (p.rules.run.steps): those ticked in the list, and the steps before them
   that ticked videos still need. Before the project exists: those ticked on the page. */
const runSteps = (s, hasAddresses) => s.project?.rules.run.steps || tickedStages(s, hasAddresses);

/* "Run" over "Download + Subtitles + Dubbing" (smaller): the steps ticked for the project, those the button runs. Two
   lines keep the button narrow, so the bar holds on one row. */
const runLabel = (s, hasAddresses) => {
  const all = runSteps(s, hasAddresses);
  const run = s.project?.rules.run;
  /* Named on the button: the steps with work left (stopped during the dubbing, downloads and subtitles done:
     "Resume: Dubbing"). All of them while nothing says otherwise. */
  const steps = run?.open.length ? run.open : all;
  const resume = !!run?.resume;                          // paused or stopped, with work left to resume
  if (nothingToRun(s)) return T("v2.nav.nothing");
  if (!steps.length) return T(resume ? "v2.project.resume" : "v2.nav.run");
  return html`<span class="run-verb">${T(resume ? "v2.project.resume" : "v2.nav.run_verb")}</span>
    <span class="run-steps">${steps.map((k) => T("v2.step." + k)).join(" + ")}</span>`;
};

async function run() {
  const s = getState();
  if (!s.project) return;
  const p = await busy("", () => api("POST", `/api/projects/${s.project.id}/start`, {}));   // the steps ticked, as the service says
  if (p) update({ project: p });
}

function newProject() {
  const s = getState();
  update({ stages: { download: true, subtitles: true, dubbing: !!s.config?.projects?.dubbing } });   // Settings, New projects
  update({ projectId: null, project: null, selection: [], pending: [], title: "", adding: null, location: { base: "", named: null }, editProject: false,
           notes: [], error: "" });
}

/* A project where nothing ticked is left to do (every box unticked, or the steps ticked all done): Run would start
   and stop at once, so it says so and waits for a box. */
const nothingToRun = (s) => !!s.project && !s.project.rules.run.open.length && !s.project.rules.run.resume;

/* Run, and next to it ▾: one step only, for the videos ticked for it ("Only subtitle (2 to do)"). */
function RunButton({ s, p, ticked, hasAddresses }) {
  const [menu, setMenu, box] = useMenu();
  return html`
    <span class="run-split" ref=${box}>
      <button type="button" class="primary run-main" disabled=${!p || !ticked || !!s.busy || nothingToRun(s)}
              title=${T(nothingToRun(s) ? "v2.nav.nothing_title" : "v2.nav.run_title")} onClick=${run}>${runLabel(s, hasAddresses)}</button>
      ${p && html`
        <button type="button" class="primary run-more" aria-haspopup="menu" aria-expanded=${menu} title=${T("v2.nav.one_step")}
                aria-label=${T("v2.nav.one_step")} disabled=${!!s.busy || p.state === "cancelled"} onClick=${() => setMenu(!menu)}>▾</button>
        ${menu && html`<${StepMenu} p=${p} close=${() => setMenu(false)} />`}`}
    </span>`;
}

function App() {
  const s = useStore();
  const items = itemsOf(s);
  const hasAddresses = items.some((i) => i.kind === "address");
  const p = s.project;
  useNotifications(p);
  useEffect(() => { if (!s.projectId || p) window.pageReady(); }, [!s.projectId || !!p]);   // shown with its project (boot.js)

  useEffect(() => {                                      // follows the project while it is open: pushed by the service
    if (!s.projectId) return undefined;
    let stop = false, timer, source = null;
    const gone = () => update({ projectId: null, project: null });
    if (window.EventSource) {
      source = new EventSource(`/api/projects/${s.projectId}/events`);
      source.onmessage = (e) => { try { update({ project: JSON.parse(e.data) }); } catch (err) { /* a broken event: the next one */ } };
      source.addEventListener("gone", () => { source.close(); gone(); });
      source.onerror = () => {                           // the browser tries again by itself; a project unknown stops it
        api("GET", `/api/projects/${s.projectId}`).catch((e) => { if (e.status === 404) { source.close(); gone(); } });
      };
      return () => { stop = true; source.close(); };
    }
    const poll = async () => {                           // without EventSource: asked every second while it works
      try {
        const d = await api("GET", `/api/projects/${s.projectId}`);
        if (!stop) update({ project: d });
        const working = d.rules.working || d.state === "awaiting_translation"                     // one video run on its own too
          || d.items.some((i) => i.facts.adding || (/^https?:/.test(i.source) && !i.facts.site && !i.facts.site_error));  // being read
        timer = setTimeout(poll, working ? 1000 : 5000);
      } catch (e) {                                     // project gone (service restarted without it)
        if (stop) return;
        if (e.status === 404) gone();
        else { update({ error: e.message }); timer = setTimeout(poll, 5000); }
      }
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [s.projectId]);

  useEffect(() => {                                      // the engines and languages of the service
    api("GET", "/api/config").then((config) => update({ config })).catch(() => {});
  }, []);

  useEffect(() => {                                      // the steps of a project read back: its own
    if (p) update({ stages: Object.fromEntries(["download", "subtitles", "dubbing"].map((k) => [k, p.stages.includes(k)])) });
  }, [p?.id]);

  const ticked = items.filter((i) => i.selected && !i.pending).length;
  const reading = items.some((i) => i.pending && !i.error);

  return html`
    <header>
      <h1>${T("ui.title")}</h1>
      ${p && html`<span class="project-title">${p.title || p.root}</span>`}
      <span class="spacer"></span>
      ${s.projectId && html`<button type="button" class="secondary small" onClick=${newProject}>${T("v2.project.new")}</button>`}
      <${HeaderButtons} notify=${s.config?.web?.notify_button !== false} />
    </header>
    <main>
      ${s.error && html`<p class="error">${s.error}</p>`}
      ${s.busy && html`<p class="busy">${s.busy}</p>`}
      <p class="visually-hidden" role="alert">${s.error || ""}</p>
      <p class="visually-hidden" role="status">${s.busy || ""}</p>
      <${YtdlpNotice} items=${items} />
      <${WorkshopScreen} s=${s} items=${items} />
      <div class="nav-bar">
        ${p && html`<span class=${`chip state-${stateKey(p)}`}>${T("v2.project.state." + stateKey(p))}</span>`}
        ${reading && html`<span class="help">${T("v2.nav.waiting_reading")}</span>`}
        <${Finishing} p=${p} />
        <span class="spacer"></span>
        ${p?.state !== "running" && html`<${RunEstimate} p=${p} steps=${runSteps(s, hasAddresses)} />`}
        ${p?.state === "running" ? html`<${RunControls} p=${p} />`
          : html`<${RunButton} s=${s} p=${p} ticked=${ticked} hasAddresses=${hasAddresses} />`}
      </div>
    </main>`;
}

(async () => {
  await loadTranslations();
  translatePage(document.querySelector("footer.signature"));
  render(html`<${App} />`, document.getElementById("app"));
})();
