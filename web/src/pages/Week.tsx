import { AlertTriangle, ArrowLeft, ChevronLeft, ChevronRight, FolderOpen } from "lucide-react";
import { ReportDoc } from "../components/ReportDoc";
import { Button, Card, Empty, ErrorState, IconButton, Loading, useToast } from "../hub/ui";
import { post } from "../lib/api";
import { useWeek } from "../lib/hooks";

/** One week's report, drawn exactly like the hub's Outputs viewer, with week-to-week navigation and the run notes. */
export default function Week({ week, weeks, go }: { week: string; weeks: string[]; go: (path: string) => void }) {
  const { data, error, isLoading, isError, refetch } = useWeek(weeks.includes(week) ? week : undefined);
  const toast = useToast();
  const at = weeks.indexOf(week);
  const earlier = at >= 0 ? weeks[at + 1] : undefined;
  const later = at > 0 ? weeks[at - 1] : undefined;
  const notes = data?.warnings ?? [];

  const bar = (
    <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 week-bar rounded-card border border-hairline px-2 py-1.5 shadow-sm">
      <Button size="sm" variant="ghost" onClick={() => go("weeks")}><ArrowLeft size={14} /> Weeks</Button>
      <span className="h-4 w-px bg-hairline" aria-hidden />
      <IconButton className="size-7" label="Earlier week" title={earlier ? `Earlier: ${earlier}` : "No earlier week"} disabled={!earlier} onClick={() => earlier && go(`weeks/${earlier}`)}>
        <ChevronLeft size={15} />
      </IconButton>
      <span className="tabular font-mono text-sm font-semibold text-fg">{week}</span>
      {at === 0 && <span className="text-xs text-fg3">latest</span>}
      <IconButton className="size-7" label="Later week" title={later ? `Later: ${later}` : "No later week"} disabled={!later} onClick={() => later && go(`weeks/${later}`)}>
        <ChevronRight size={15} />
      </IconButton>
      <div className="ml-auto flex items-center gap-1.5">
        {data && (
          <Button size="sm" variant="ghost" title="Jump to the run notes" onClick={() => document.getElementById("run-notes")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
            <AlertTriangle size={14} className={notes.length ? "text-warn" : undefined} /> Run notes{notes.length ? ` · ${notes.length}` : ""}
          </Button>
        )}
        <Button size="sm" variant="ghost" title="Show the report file" disabled={at < 0} onClick={() => post("/api/reveal", { week }).catch((e) => toast(`Couldn't show the report file: ${(e as Error).message}`, "error"))}>
          <FolderOpen size={14} /> Show file
        </Button>
      </div>
    </div>
  );

  if (at < 0) {
    return (
      <div className="flex flex-col gap-3">
        {bar}
        <Card><Empty icon={<AlertTriangle size={22} />}>No report for {week}.</Empty></Card>
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {bar}
      {isLoading ? (
        <Loading />
      ) : isError || !data ? (
        <Card><ErrorState what="this report" error={error} onRetry={() => refetch()} /></Card>
      ) : (
        <>
          {data.markdown ? <ReportDoc markdown={data.markdown} /> : <Card><Empty>The report file is empty.</Empty></Card>}
          <div id="run-notes" className="scroll-mt-16">
            <Card title={notes.length ? `Run notes · ${notes.length}` : "Run notes"} icon={<AlertTriangle size={15} />}>
              {notes.length ? (
                <ul className="flex flex-col gap-2 text-[13px] text-fg2">
                  {notes.map((w, i) => (
                    <li key={i} className="flex gap-2"><span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-warn" />{w}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-fg3">Every source answered this week.</p>
              )}
              <p className="mt-3 truncate text-xs text-fg3" title={data.file}>{data.file.replace(/^\/Users\/[^/]+/, "~")}</p>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
