/**
 * Follow the hub's style. The server already rendered <html data-style data-theme data-term> from the hub's
 * settings; the hub then sends { type: "myhome:appearance" } whenever it changes (and when we ask on load).
 */
export function followHubAppearance() {
  const apply = (style: string, theme: string, term: boolean) => {
    const root = document.documentElement;
    root.dataset.style = style;
    root.dataset.theme = theme;
    if (term) root.dataset.term = "";
    else delete root.dataset.term;
  };
  window.addEventListener("message", (e) => {
    const m = e.data as { type?: string; style?: string; theme?: string; term?: boolean } | null;
    if (m?.type === "myhome:appearance" && typeof m.style === "string" && typeof m.theme === "string") apply(m.style, m.theme, !!m.term);
  });
  if (window.parent !== window) window.parent.postMessage({ type: "myhome:get-appearance" }, "*");
  else if (!document.documentElement.dataset.style) {
    // Opened on its own (dev server): ask our backend, which asks the hub.
    fetch("/api/appearance").then((r) => r.json()).then((a) => apply(a.style, a.theme, a.term)).catch(() => {});
  }
}
