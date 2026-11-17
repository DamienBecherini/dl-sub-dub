/* The Subtitles screen: the settings of the project (transcription and translation engines, languages wanted,
   spoken language, pause before a paid translation, AI review (none, the names, every subtitle) and the model that
   reviews, where the files go), the choices of each video (its spoken language, its own languages), what the review
   proposes to check, and the review and the translation waiting for the go-ahead with their estimated cost. The settings apply to the videos not started yet: they can change between two runs. */
/* global label */
import { useEffect, useState } from "/static/vendor/preact-htm.js";
import { act, api, busy, capital, Confirm, getState, html, languageName, languagesOf, siteLanguage, T, Tip, update } from "./lib.js";

export const LANGUAGES = ["fr", "en", "de", "es", "it", "pt", "nl"];   // offered as boxes; the others are typed

async function post(path, body) {
  const p = await busy("", () => api("POST", path, body));
  if (p) update({ project: p });
  return p;
}

const engineName = (m) => m?.name || (m ? label("engine.", m.id) : "?");

/* Boxes of the usual languages, and a field for the others (codes, comma separated). */
/* `origin`: the spoken language, greyed: unticked for the dubbing (a video is not dubbed into its own language),
   ticked for the subtitles (`made`: those of the spoken language are always made). */
/* few: only the languages ticked, the interface's, the browser's and English, the rest behind "+ N other languages"
   (the quick settings, always on screen). */
export function LanguageChoice({ value, onChange, disabled, origin = "", made = false, few = false }) {
  /* global I18N */
  const others = value.filter((c) => !LANGUAGES.includes(c));
  const [typed, setTyped] = useState(others.join(", "));
  const [all, setAll] = useState(false);
  useEffect(() => setTyped(others.join(", ")), [others.join(",")]);
  const toggle = (c, on) => onChange(on ? [...value, c] : value.filter((x) => x !== c));
  const near = new Set([...value, I18N.language, ...(navigator.languages || []).map((l) => l.slice(0, 2).toLowerCase()), "en"]);
  const shown = few && !all ? LANGUAGES.filter((c) => near.has(c) || c === origin) : LANGUAGES;
  const hidden = LANGUAGES.length - shown.length;
  return html`
    <div class="choices">
      ${shown.map((c) => html`
        <label class=${`check ${c === origin ? "origin" : ""}`} title=${c === origin ? T(made ? "v2.sub.origin_title" : "v2.dub.origin_title") : undefined}>
          <input type="checkbox" checked=${c === origin ? made : value.includes(c)} disabled=${disabled || c === origin}
                 onChange=${(e) => toggle(c, e.target.checked)} /> ${languageName(c)}${c === origin ? html` <span class="help">${T("v2.dub.origin")}</span>` : ""}</label>`)}
      ${(!few || all || others.length > 0) && html`
      <label class="check others">${T("v2.sub.others")}
        <input type="text" class="codes" value=${typed} disabled=${disabled} placeholder="pl, ru…"
               onInput=${(e) => setTyped(e.target.value)}
               onChange=${(e) => onChange([...value.filter((c) => LANGUAGES.includes(c)),
                                           ...e.target.value.split(/[\s,;]+/).map((c) => c.trim().toLowerCase()).filter(Boolean)])} />
      </label>`}
      ${few && (hidden > 0 || all) && html`
        <button type="button" class="link more-languages" aria-expanded=${all} onClick=${() => setAll(!all)}>
          ${all ? T("v2.quick.fewer") : T("v2.quick.more", { n: hidden })}</button>`}
    </div>`;
}

export function SpokenLanguage({ value, onChange, auto, disabled }) {
  return html`
    <select value=${value || ""} disabled=${disabled} onChange=${(e) => onChange(e.target.value)}>
      <option value="">${auto}</option>
      ${LANGUAGES.map((c) => html`<option value=${c}>${languageName(c)}</option>`)}
    </select>`;
}

const CODE = /^[a-z]{2,3}(-[a-z0-9]{2,8})*$/;
const codesOf = (v) => (Array.isArray(v) ? v : v ? [v] : []).filter((c) => typeof c === "string" && CODE.test(c));

/* A language taken off the subtitles that is also dubbed: its translation is still made for the voice. Asked: take
   it off the dubbing too, keep the dubbing, or cancel. `ask` {languages, removed}; `apply(languages, dubLeft|undefined)`. */
function DubbedToo({ ask, apply, close }) {
  if (!ask) return null;
  const langs = languagesOf(ask.removed);
  return html`<${Confirm} title=${T("v2.sub.dubbed_too_title", { n: ask.removed.length, langs: capital(langs) })}
                          text=${T("v2.sub.dubbed_too", { n: ask.removed.length, langs })}
                          yes=${T("v2.sub.dubbed_remove_both", { n: ask.removed.length })} danger=${false}
                          onYes=${() => { close(); apply(ask.languages, ask.dubbed.filter((c) => !ask.removed.includes(c))); }}
                          other=${T("v2.sub.dubbed_keep", { n: ask.removed.length })} onOther=${() => { close(); apply(ask.languages); }} onNo=${close} />`;
}

/* The languages of the project, among its quick settings: the subtitles of every video in each, and their dubbing
   (when ticked) in each but the video's own. Taking off one also dubbed on its own asks first (DubbedToo). */
export function ProjectLanguages({ p }) {
  const s = p.subtitles;
  const set = (change) => post(`/api/projects/${p.id}/settings`, change);
  const [ask, setAsk] = useState(null);
  const dubbed = codesOf(p.dubbing?.languages);
  const choose = (v) => {
    const removed = s.languages.filter((c) => !v.includes(c) && dubbed.includes(c));
    if (removed.length) setAsk({ languages: v, removed, dubbed });
    else set({ languages: v });
  };
  const apply = (languages, dubLeft) => set(dubLeft === undefined ? { languages } : { languages, dub_language: dubLeft.length ? dubLeft : null });
  return html`
    <${LanguageChoice} value=${s.languages} onChange=${choose} few=${true} disabled=${p.rules.locked} />
    ${p.rules.locked && html`<p class="help">${T("v2.languages.locked")}</p>`}
    <${DubbedToo} ask=${ask} apply=${apply} close=${() => setAsk(null)} />`;
}

/* The AI review of the project, among its quick settings: none, the names, every subtitle. Under it, who reviews and
   whether it costs (a paid model always asks first); "change the model" unfolds its choice in place. */
export function ReviewQuick({ p, cfg }) {
  const s = p.subtitles;
  const [open, setOpen] = useState(false);
  const set = (change) => post(`/api/projects/${p.id}/settings`, change);
  const reviewers = (cfg?.translators || []).filter((m) => (m.type || "openai") !== "none");
  const model = reviewers.find((m) => m.id === s.reviewer);
  return html`
    <span class="order-switch">
      <span class="mode-switch" role="group" aria-label=${T("v2.review.label")}>
        ${["", "names", "full"].map((m) => html`
          <button type="button" aria-pressed=${s.review === m} class=${s.review === m ? "current" : ""}
                  onClick=${() => { if (s.review !== m) set({ review: m }); }}>${T("v2.review.short." + (m || "none"))}</button>`)}
      </span>
      <${Tip} text=${T("v2.sub.review_title")} />
    </span>
    ${s.review && html`
      <p class="help review-by">
        ${model ? html`${T("v2.review.by", { name: engineName(model) })} · <span class=${s.reviewer_paid ? "paid" : ""}>${T(s.reviewer_paid ? "v2.review.paid" : model.type === "claude_code" ? "v2.review.subscription" : "v2.review.free")}</span>`
                : T("v2.sub.review_no_model")}
        ${" "}<button type="button" class="link" aria-expanded=${open} onClick=${() => setOpen(!open)}>${T(open ? "v2.review.fold" : "v2.review.change")}</button>
      </p>
      ${open && html`
        <label class="review-model"><span>${T("v2.sub.review_engine")} <${Tip} text=${T("v2.sub.review_engine_title")} /></span>
          <select value=${s.review_engine} onChange=${(e) => set({ review_engine: e.target.value })}>
            <option value="">${s.reviewer && !s.review_engine ? T("v2.sub.review_auto", { name: engineName(model) }) : T("v2.sub.review_auto_plain")}</option>
            ${reviewers.map((m) => html`<option value=${m.id}>${engineName(m)}</option>`)}
          </select>
        </label>`}`}`;
}

export function SubtitleSettings({ p, cfg }) {
  const s = p.subtitles;
  const set = (change) => post(`/api/projects/${p.id}/settings`, change);
  const busyNow = p.items.some((i) => i.stages.subtitles.state === "running");
  const translators = cfg?.translators || [];
  const transcriptions = cfg?.transcriptions || [];
  return html`
    <div class="screen-settings grid-settings">
      <label>${T("v2.sub.translator")}
        <select value=${p.options.translator || ""} onChange=${(e) => set({ translator: e.target.value || null })}>
          <option value="">${T("v2.sub.active", { name: engineName(translators.find((m) => m.id === cfg?.active_translator)) })}</option>
          ${translators.map((m) => html`<option value=${m.id}>${engineName(m)}</option>`)}
        </select>
      </label>
      <label>${T("v2.sub.transcription")}
        <select value=${p.options.transcription || ""} onChange=${(e) => set({ transcription: e.target.value || null })}>
          <option value="">${T("v2.sub.active", { name: engineName(transcriptions.find((m) => m.id === cfg?.active_transcription)) })}</option>
          ${transcriptions.map((m) => html`<option value=${m.id}>${engineName(m)}</option>`)}
        </select>
      </label>
      <label>${T("v2.sub.spoken")} <${Tip} text=${T("v2.tip.spoken")} />
        <${SpokenLanguage} value=${p.options.language} auto=${T("ui.auto_detect")} onChange=${(v) => set({ language: v || null })} />
      </label>
      <div class="wide checks">
        <label class="check">
          <input type="checkbox" checked=${s.pause} onChange=${(e) => set({ pause: e.target.checked ? true : null })} />
          ${T("v2.sub.pause")} <${Tip} text=${T("v2.sub.pause_title")} />
        </label>
        ${s.spend_agreed && html`
          <label class="check">
            <input type="checkbox" checked=${true} onChange=${() => set({ spend_agreed: false })} /> ${T("v2.sub.spend_agreed")} <${Tip} text=${T("v2.sub.spend_agreed_title")} />
          </label>`}
        <label class="check">
          <input type="checkbox" checked=${s.destination.next_to_video}
                 onChange=${(e) => set({ destination: { ...s.destination, next_to_video: e.target.checked } })} /> ${T("v2.sub.next_to_video")} <${Tip} text=${T("v2.tip.next_to")} />
        </label>
        <label class="check">
          <input type="checkbox" checked=${s.destination.output_dir}
                 onChange=${(e) => set({ destination: { ...s.destination, output_dir: e.target.checked } })} /> ${T("v2.sub.output_dir")} <${Tip} text=${T("v2.tip.copies")} />
        </label>
      </div>
      ${busyNow && html`<p class="help wide">${T("v2.sub.applies_next")}</p>`}
    </div>`;
}

/* The choices of one video: its spoken language, its own languages (otherwise the project's). */
/* An engine for this video only: "as the project (its engine)" by default, or one of the settings. */
function VideoEngine({ p, item, kind, path }) {
  const cfg = getState().config;
  const list = cfg?.[kind === "transcription" ? "transcriptions" : "translators"] || [];
  const active = cfg?.[kind === "transcription" ? "active_transcription" : "active_translator"];
  const project = list.find((m) => m.id === (p.options[kind] || active));
  const running = item.stages.subtitles.state === "running";
  return html`
    <label class="inline-field">${T(kind === "transcription" ? "v2.sub.transcription" : "v2.sub.translator")}
      <select value=${item.overrides[kind] || ""} disabled=${running} onChange=${(e) => post(path, { [kind]: e.target.value || null })}>
        <option value="">${T("v2.sub.as_project", { v: engineName(project) })}</option>
        ${list.map((m) => html`<option value=${m.id}>${engineName(m)}</option>`)}
      </select>
      <${Tip} text=${T(kind === "transcription" ? "v2.tip.video_transcription" : "v2.tip.video_translator")} />
    </label>`;
}

export function SubtitleDetails({ p, item }) {
  const path = `/api/projects/${p.id}/items/${item.id}/settings`;
  const own = item.overrides.languages;
  const detected = item.facts.detected_language;
  const site = !detected && siteLanguage(item);              // before the transcription: what its site says
  const [ask, setAsk] = useState(null);
  const ownDub = codesOf(item.overrides.dub_language);
  const dubbed = ownDub.length ? ownDub : codesOf(p.dubbing?.languages);
  const choose = (v) => {
    const removed = (own || []).filter((c) => !v.includes(c) && dubbed.includes(c));
    if (removed.length) setAsk({ languages: v, removed, dubbed });
    else post(path, { languages: v });
  };
  // Back to the project's languages: one of its own that is dubbed and that the project lacks asks first, as unticking it
  // would (`languages` null: the project's).
  const asProject = () => {
    const removed = (own || []).filter((c) => !p.subtitles.languages.includes(c) && dubbed.includes(c));
    if (removed.length) setAsk({ languages: null, removed, dubbed });
    else post(path, { languages: null });
  };
  const apply = async (languages, dubLeft) => {
    if (dubLeft === undefined) { post(path, { languages }); return; }
    if (dubLeft.length) { post(path, { languages, dub_language: dubLeft }); return; }
    // No language left to dub: its own dubbed languages go too (they stayed ticked in its Dubbing tab), and its
    // dubbing step is unticked: it is not dubbed any more.
    await post(path, { languages, dub_language: null });
    act(`/api/projects/${p.id}/select`, { items: { [item.id]: false }, stage: "dubbing" });
  };
  return html`
    <div class="details">
      <${VideoEngine} p=${p} item=${item} kind="transcription" path=${path} />
      <${VideoEngine} p=${p} item=${item} kind="translator" path=${path} />
      ${(detected || site || item.facts.count) && html`
        <p class="help">${[detected && T("v2.sub.detected", { lang: languageName(detected) }),
                           site && T("v2.sub.site_says", { lang: languageName(site) || site }),
                           item.facts.count && T("v2.sub.count", { n: item.facts.count })].filter(Boolean).join(" · ")}</p>`}
      <label class="inline-field">${T("v2.review.label")}
        <select value=${item.overrides.review || ""} disabled=${item.stages.subtitles.state === "running"} onChange=${(e) => post(path, { review: e.target.value || null })}>
          <option value="">${T("v2.sub.as_project", { v: T("v2.review.short." + (p.subtitles.review || "none")) })}</option>
          ${["none", "names", "full"].map((m) => html`<option value=${m}>${T("v2.review.short." + m)}</option>`)}
        </select>
        <${Tip} text=${T("v2.tip.video_review")} />
      </label>
      <label class="inline-field">${T("v2.sub.spoken_video")}
        <${SpokenLanguage} value=${item.overrides.language} auto=${p.options.language ? T("v2.sub.as_project", { v: languageName(p.options.language) }) : T("ui.auto_detect")}
                           onChange=${(v) => post(path, { language: v || null })} />
      </label>
      <div>
        <label class="check"><input type="checkbox" checked=${!own} disabled=${item.locked}
                 onChange=${(e) => (e.target.checked ? asProject() : post(path, { languages: p.subtitles.languages }))} />
          ${T("v2.sub.languages_as_project", { v: languagesOf(p.subtitles.languages) || "—" })}</label>
        ${own && html`<${LanguageChoice} value=${own} origin=${item.spoken} made=${true} onChange=${choose} disabled=${item.locked} />`}
        ${item.locked && html`<p class="help">${T("v2.languages.locked_video")}</p>`}
        <${DubbedToo} ask=${ask} apply=${apply} close=${() => setAsk(null)} />
      </div>
    </div>`;
}

/* What the videos transcribed wait for, with what it will take: the go-ahead for an AI review that costs (its
   estimate, with each model that can review, or no review), a decision on what the review proposes (the panel
   below), the go-ahead for the translation (its estimate, the translator and the languages). One button for all. */
export function TranslationPanel({ p, undecided = false }) {
  const [translator, setTranslator] = useState(p.subtitles.translator);
  const [languages, setLanguages] = useState(p.subtitles.languages);
  const [reviewer, setReviewer] = useState(null);         // null: the model of the project; "": no review
  const [noMore, setNoMore] = useState(false);            // not to be asked again before a paid model, in this project
  const [estimate, setEstimate] = useState(null);
  useEffect(() => {
    let gone = false;
    api("GET", `/api/projects/${p.id}/estimate?languages=${encodeURIComponent(languages.join(","))}`)
      .then((e) => { if (!gone) setEstimate(e); }).catch(() => {});
    return () => { gone = true; };
  }, [p.id, languages.join(","), p.counts?.prepared]);
  const cost = (t, unknown) => (t.subscription ? T("v2.sub.subscription", { n: Number(t.tokens).toLocaleString(I18N.language) })         // Claude Code: nothing billed
    : t.cost_usd != null ? (t.cost_usd === 0 ? T("v2.sub.free") : `≈ ${t.cost_usd.toFixed(2)} $`) : unknown ? T("v2.sub.unknown_price") : "")
    + (t.duration_s ? ` · ≈ ${Math.max(1, Math.round(t.duration_s / 60))} min` : "");
  const rv = estimate?.review;
  const chosen = reviewer ?? rv?.engine ?? "";
  const go = () => post(`/api/projects/${p.id}/translate`,
    { translator, languages, ...(rv ? (chosen === "" ? { review: "" } : { review_engine: chosen }) : {}), ...(noMore ? { spend_agreed: true } : {}) });
  return html`
    <div class="translation-panel">
      <h3>${T(rv ? "v2.sub.waiting_review_title" : "v2.sub.waiting_title")}</h3>
      ${rv && html`
        <p>${T(rv.mode === "full" ? "v2.sub.review_estimate_full" : "v2.sub.review_estimate_names", { videos: rv.videos, n: rv.count, names: rv.names })}</p>
        <div class="choices column">
          ${rv.translators.map((t) => html`
            <label class="check"><input type="radio" name="v2-reviewer" checked=${chosen === t.id} onChange=${() => setReviewer(t.id)} />
              <strong>${t.name}</strong> <span class="help">${cost(t, true)}</span></label>`)}
          <label class="check"><input type="radio" name="v2-reviewer" checked=${chosen === ""} onChange=${() => setReviewer("")} />
            ${T("v2.sub.review_skip")}</label>
        </div>
        <p class="help">${T("v2.sub.review_then")}</p>`}
      ${undecided && html`<p class="help">${T("v2.sub.validation_help")}</p>`}
      ${estimate && estimate.languages?.length
        ? html`<p>${T("v2.sub.estimate", { videos: estimate.videos, n: estimate.count, langs: languagesOf(estimate.languages) })}</p>
               <div class="choices column">
                 ${estimate.translators.map((t) => html`
                   <label class="check"><input type="radio" name="v2-translator" checked=${translator === t.id} onChange=${() => setTranslator(t.id)} />
                     <strong>${t.name}</strong> <span class="help">${cost(t)}</span></label>`)}
               </div>`
        : html`<p class="help">${estimate ? T("v2.sub.nothing_to_translate") : T("v2.sub.estimating")}</p>`}
      <${LanguageChoice} value=${languages} onChange=${setLanguages} />
      <label class="check no-more"><input type="checkbox" checked=${noMore} onChange=${(e) => setNoMore(e.target.checked)} />
        ${T("v2.sub.no_more")} <${Tip} text=${T("v2.sub.spend_agreed_title")} /></label>
      <div class="row">
        <button type="button" class="primary small" onClick=${go}>${T(rv && chosen !== "" ? "v2.sub.review_go" : "v2.sub.translate")}</button>
      </div>
    </div>`;
}

const clock = (s) => {
  const t = Math.floor(s || 0), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60);
  return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(t % 60).padStart(2, "0");
};

/* What the AI review proposes, decided here: the names (accepted with their spelling, and a French form if any, for
   the whole project) and, video by video, the lines of the full review (the fix, which can be retouched, replaces the
   line before the translation; unticked, the line stays as it is). A doubtful fix comes unticked. */
export function NamesPanel({ p, onCount = null, onLater = null }) {
  const [names, setNames] = useState(null);
  const [lines, setLines] = useState([]);
  useEffect(() => { setNames(null); setLines([]); }, [p.id]);                // another project: not known yet
  useEffect(() => { onCount?.(names === null ? null : names.length + lines.length); }, [names, lines]);
  useEffect(() => {
    api("GET", `/api/projects/${p.id}/review`).then((r) => {
      setNames(r.proposals.map((x) => ({ ...x, accept: true, french: x.french || "" })));
      setLines((r.corrections || []).flatMap((v) => v.corrections.map((c) => ({ ...c, item: v.item, title: v.title, accept: c.confidence === "high" }))));
    }).catch(() => { setNames([]); setLines([]); });
  }, [p.id, p.counts?.prepared, p.review_stamp]);           // review_stamp: proposals of an assistant (MCP)
  if (!names?.length && !lines.length) return null;
  const change = (i, field, v) => setNames(names.map((n, j) => (j === i ? { ...n, [field]: v } : n)));
  const changeLine = (c, field, v) => setLines(lines.map((x) => (x === c ? { ...x, [field]: v } : x)));
  const validate = async () => {
    const r = await busy("", () => api("POST", `/api/projects/${p.id}/validate`,
      { decisions: names, lines: lines.map(({ item, id, accept, corrected }) => ({ item, id, accept, corrected })) }));
    if (r) { update({ project: r.project }); setNames([]); setLines([]); }
  };
  const videos = [...new Set(lines.map((c) => c.item))];
  return html`
    <div class="names-panel">
      ${onLater && html`<p class="first-step">${T("v2.sub.decide_first")}</p>`}
      ${names.length > 0 && html`
        <h3>${T("v2.sub.names_title", { n: names.length })}</h3>
        <p class="help">${T("v2.sub.names_help")}</p>
        <table class="names">
          <thead><tr><th></th><th>${T("v2.sub.names_heard")}</th><th>${T("v2.sub.names_spelling")}</th><th>${T("v2.sub.names_french")}</th><th></th></tr></thead>
          <tbody>
            ${names.map((n, i) => html`
              <tr key=${n.transcribed}>
                <td><input type="checkbox" checked=${n.accept} onChange=${(e) => change(i, "accept", e.target.checked)} /></td>
                <td>${n.transcribed}</td>
                <td><input type="text" value=${n.spelling} onInput=${(e) => change(i, "spelling", e.target.value)} /></td>
                <td><input type="text" value=${n.french} onInput=${(e) => change(i, "french", e.target.value)} /></td>
                <td class="help">${n.occurrences ? "×" + n.occurrences : ""}</td>
              </tr>`)}
          </tbody>
        </table>`}
      ${lines.length > 0 && html`
        <h3>${T("v2.sub.lines_title", { n: lines.length })}</h3>
        <p class="help">${T("v2.sub.lines_help")}</p>
        ${videos.map((id) => {
          const mine = lines.filter((c) => c.item === id);
          return html`
            <details class="lines-video" open=${videos.length < 4} key=${id}>
              <summary>${mine[0].title} <span class="help">(${mine.filter((c) => c.accept).length} / ${mine.length})</span></summary>
              <p class="lines-all">
                <button type="button" class="link" onClick=${() => setLines(lines.map((x) => (x.item === id ? { ...x, accept: true } : x)))}>${T("v2.sub.lines_all")}</button>
                <button type="button" class="link" onClick=${() => setLines(lines.map((x) => (x.item === id ? { ...x, accept: false } : x)))}>${T("v2.sub.lines_none")}</button>
              </p>
              <table class="names lines">
                <tbody>
                  ${mine.map((c) => html`
                    <tr key=${c.id}>
                      <td><input type="checkbox" checked=${c.accept} aria-label=${T("v2.sub.lines_accept")} onChange=${(e) => changeLine(c, "accept", e.target.checked)} /></td>
                      <td class="help time">${clock(c.start)}</td>
                      <td>
                        <div class="line-before">${c.original}</div>
                        <input type="text" value=${c.corrected} placeholder=${T("v2.sub.lines_removed")} aria-label=${T("v2.sub.lines_after")}
                               onInput=${(e) => changeLine(c, "corrected", e.target.value)} />
                        <div class="help">${c.confidence !== "high" ? html`<span class="doubt">${T(c.confidence === "low" ? "v2.sub.lines_low" : "v2.sub.lines_medium")}</span> ` : ""}${c.reason}</div>
                      </td>
                    </tr>`)}
                </tbody>
              </table>
            </details>`;
        })}`}
      <div class="row">
        <button type="button" class="primary small" onClick=${validate}>${T(onLater ? "v2.sub.names_validate_next" : "v2.sub.names_validate")}</button>
        ${onLater && html`<button type="button" class="secondary small" onClick=${onLater}>${T("v2.sub.decide_later")}</button>
                          <${Tip} text=${T("v2.sub.decide_later_title")} />`}
      </div>
    </div>`;
}
