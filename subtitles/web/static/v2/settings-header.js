/* The buttons of the header on the Settings page (a page written without Preact): the language, the magnifiers, the
   mode and the palettes, the same as on the main page, without the bell nor the link to the Settings. `onLanguage(code)`
   is called once the language is changed and saved, for the page to translate itself. */
import { render } from "/static/vendor/preact-htm.js";
import { HeaderButtons } from "./header.js";
import { html } from "./lib.js";

export function mount(element, onLanguage) {
  render(html`<${HeaderButtons} notify=${false} settingsLink=${false} onLanguage=${onLanguage} />`, element);
}
