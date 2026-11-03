import { FolderOpen } from "lucide-react";
import { useEffect } from "react";
import { AppShell } from "./hub/appshell";
import { Button } from "./hub/ui";
import { post } from "./lib/api";
import { useHashRoute } from "./lib/hash";
import { useOverview } from "./lib/hooks";
import Config, { SUBTABS, type Sub } from "./pages/Config";
import Week from "./pages/Week";
import Weeks from "./pages/Weeks";

const TABS = [
  { value: "weeks", label: "Weeks" },
  { value: "config", label: "Configuration" },
] as const;
type Tab = (typeof TABS)[number]["value"];

/** Laid out like a hub app page (the same AppShell as the Website app): header row, Segmented tabs, cards.
 *  Routes: #/weeks (gallery), #/weeks/2026-W45 (one week), #/config/<directions|report|schedule|…>. */
export default function App() {
  const [route, go] = useHashRoute();
  const tab: Tab = route[0] === "config" ? "config" : "weeks";
  const sub: Sub = SUBTABS.some((s) => s.value === route[1]) ? (route[1] as Sub) : "directions";
  const week = tab === "weeks" && route[0] === "weeks" ? route[1] : undefined;
  const { data, error } = useOverview();
  const weeks = data?.weeks ?? [];
  const latest = weeks[0];

  // Old links: #/week, #/week/2026-W45, /?week=2026-W45, #/directions and /directions.
  useEffect(() => {
    const legacy = new URLSearchParams(window.location.search).get("week");
    if (legacy && !window.location.hash) go(`weeks/${legacy}`);
    else if (window.location.pathname === "/directions" && !window.location.hash) go("config/directions");
  }, []);
  useEffect(() => {
    if (route[0] === "week") window.location.replace(`#/weeks${route[1] ? `/${route[1]}` : ""}`);
    else if (route[0] === "directions") window.location.replace("#/config/directions");
  }, [route[0], route[1]]);
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [week]);

  useEffect(() => {
    const label = SUBTABS.find((s) => s.value === sub)?.label;
    document.title = tab === "config" ? `${label} · AI Weekly` : week ? `${week} · AI Weekly` : "Weeks · AI Weekly";
  }, [tab, sub, week]);

  const status = error
    ? { status: "failed" as const, text: "Can't reach airec" }
    : !data
      ? null
      : latest
        ? {
            status: (data.scheduled ? "ok" : "paused") as "ok" | "paused",
            text: `Latest ${latest}`,
            note: data.schedule,
            title: data.scheduled ? "The weekly run is scheduled" : "Turn it on with `rec schedule install`",
          }
        : { status: "unknown" as const, text: "No report yet", note: data.schedule };

  const folder = tab === "config" ? (sub === "directions" ? "directions" : null) : "reports";
  return (
    <AppShell
      icon="📰"
      name={data?.title ?? "AI Weekly"}
      status={status}
      wide={!!week}
      actions={
        folder && (
          <Button variant="ghost" aria-label={`Show the ${folder} folder`} title={`Show the ${folder} folder`} onClick={() => post("/api/reveal", { folder })}>
            <FolderOpen size={15} />
          </Button>
        )
      }
      tabs={[...TABS]}
      tab={tab}
      onTab={(t) => go(t === "config" ? `config/${sub}` : t)}
    >
      {tab === "weeks" && (week ? data ? <Week week={week} weeks={weeks} go={go} /> : null : <Weeks />)}
      {tab === "config" && <Config sub={sub} go={go} />}
    </AppShell>
  );
}
