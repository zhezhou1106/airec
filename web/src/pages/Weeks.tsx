import { AlertTriangle, ChevronRight, Newspaper } from "lucide-react";
import { useState } from "react";
import { Card, Empty, Spinner } from "../hub/ui";
import { useWeeks } from "../lib/hooks";
import type { WeekSummary } from "../lib/types";

/** Every report, newest first, one full-width row each. A row opens that week's page. */
export default function Weeks() {
  const { data, error, isLoading } = useWeeks();
  if (isLoading) return <div className="py-10"><Spinner /></div>;
  if (error || !data) return <Card><Empty icon={<AlertTriangle size={22} />}>{(error as Error)?.message ?? "Couldn't load the weeks."}</Empty></Card>;
  if (!data.weeks.length) {
    return (
      <Card>
        <Empty icon={<Newspaper size={22} />}>
          <span className="text-[15px] font-medium text-fg">No report yet</span>
          <span>The first one arrives on schedule. To make one now, run <code className="text-fg2">rec run</code>.</span>
        </Empty>
      </Card>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {data.weeks.map((w) => <WeekRow key={w.week} week={w} />)}
    </div>
  );
}

function WeekRow({ week }: { week: WeekSummary }) {
  const [broken, setBroken] = useState(false);
  const cover = week.cover && !broken ? week.cover : null;
  return (
    <a href={`#/weeks/${week.week}`} className="week-row focus-ring block rounded-card" aria-label={`Open ${week.week}`}>
      <Card bodyClass="p-4 sm:p-5">
        <div className="flex items-start gap-5">
          <div className="week-thumb hidden w-44 shrink-0 sm:block">
            {cover ? (
              <img src={cover} alt="" loading="lazy" className="size-full object-cover" onError={() => setBroken(true)} />
            ) : (
              <div className="flex size-full flex-col items-center justify-center font-mono text-fg3">
                <span className="text-[11px] tracking-wider uppercase">{week.week.slice(0, 4)}</span>
                <span className="text-3xl font-semibold text-fg2">{week.week.slice(5)}</span>
              </div>
            )}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="week-label font-mono text-lg font-semibold text-fg transition-colors">{week.week}</span>
              {week.window && <span className="tabular text-sm text-fg3">{week.window}</span>}
              {week.latest && (
                <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-accent-text uppercase">Latest</span>
              )}
            </div>
            {week.highlights.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1 text-[14px] text-fg2">
                {week.highlights.map((h, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-[9px] size-1 shrink-0 rounded-full bg-fg3" />
                    <span className="line-clamp-1">{h}</span>
                  </li>
                ))}
              </ul>
            )}
            {week.sections.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
                {week.sections.map((s) => (
                  <span key={s.title} className="flex gap-1.5 rounded-full bg-sky/35 px-2.5 py-0.5 dark:bg-sky/15">
                    <span className="text-fg2">{s.title}</span>
                    {s.count > 0 && <span className="tabular font-medium text-fg3">{s.count}</span>}
                  </span>
                ))}
              </div>
            )}
          </div>
          <ChevronRight size={18} className="mt-1 shrink-0 self-center text-fg3" aria-hidden />
        </div>
      </Card>
    </a>
  );
}
