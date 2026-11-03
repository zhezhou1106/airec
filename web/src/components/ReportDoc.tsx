import { ReportView } from "../hub/report";
import { openUrl } from "../lib/api";

// Report images live next to the reports (assets/2026-W45.png); the server serves them under /api/assets.
const image = (src: string) => {
  const local = /^(?:\.\/)?assets\/([\w.-]+)$/.exec(decodeURI(src));
  return local ? `/api/assets/${local[1]}` : src;
};

/** A report file drawn by the hub's own renderer (vendored kit/report.tsx), so it looks exactly like the hub's
 *  Outputs viewer. Links leave through /api/open (the hub opens them). */
export function ReportDoc({ markdown }: { markdown: string }) {
  return <ReportView openLink={(url) => void openUrl(url)} resolveImage={image}>{markdown}</ReportView>;
}
