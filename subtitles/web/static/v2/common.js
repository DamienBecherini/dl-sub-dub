/* Pieces of the page: the order of the work, the notices that wait for the user (the translation to approve, the
   names to check, the graphics card to free). */
import { GpuWait } from "./dubbing.js";
import { act, html, T, Tip } from "./lib.js";
import { NamesPanel, TranslationPanel } from "./subtitles.js";

/* Step by step or video by video, with what each one means. */
export function OrderChoice({ p }) {
  const set = (order) => act(`/api/projects/${p.id}/settings`, { order });
  return html`
    <div class="order-choice" role="radiogroup" aria-label=${T("v2.order.label")}>
      ${["steps", "videos"].map((o) => html`
        <label class=${`order-option ${p.order === o ? "chosen" : ""}`}>
          <input type="radio" name=${"order-" + p.id} checked=${p.order === o} onChange=${() => set(o)} />
          <span><strong>${T("v2.order." + o)}</strong><span class="help">${T("v2.order." + o + "_help")}</span></span>
        </label>`)}
    </div>`;
}

/* The same choice, small, next to Run (it changes much of what happens): the same setting of the project, so the two
   always agree. Its bubble says what each way does. */
export function OrderSwitch({ p, disabled = false }) {
  const set = (order) => { if (p.order !== order && !disabled) act(`/api/projects/${p.id}/settings`, { order }); };
  const help = ["steps", "videos"].map((o) => `${T("v2.order." + o)} : ${T("v2.order." + o + "_help")}`).join("  ");
  return html`
    <span class="order-switch">
      <span class="mode-switch" role="group" aria-label=${T("v2.order.label")}>
        ${["steps", "videos"].map((o) => html`
          <button type="button" aria-pressed=${p.order === o} class=${p.order === o ? "current" : ""}
                  disabled=${disabled} onClick=${() => set(o)}>${T("v2.order." + o)}</button>`)}
      </span>
      <${Tip} text=${help} />
    </span>`;
}

/* What waits for the user, above the videos. */
export function Notices({ p }) {
  if (!p) return null;
  return html`
    <${GpuWait} p=${p} />
    ${p.state === "awaiting_translation" && html`<${TranslationPanel} p=${p} />`}
    ${p.subtitles.review && html`<${NamesPanel} p=${p} />`}
    ${p.blocked?.length > 0 && html`<p class="attention">${T("v2.project.blocked", { n: p.blocked.length })}</p>`}`;
}
