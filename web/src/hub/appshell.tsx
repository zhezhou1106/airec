// Vendored from MyHome (kit/appshell.tsx) by scripts/sync-hub-ui.mjs. Don't edit here.
/**
 * The page frame for apps that show their own UI inside the hub (service tabs). Apps vendor this file with the
 * rest of the design layer (index.css, ui.tsx, format.ts) so every app page has the same header, status chip,
 * tab bar and width as the hub's own pages. The hub itself doesn't import it.
 *
 *   <AppShell icon="🌐" name="Website" status={{ status: "ok", text: "Live", note: "my-site.dev" }}
 *             actions={<Button variant="primary">Publish</Button>}
 *             tabs={[{ value: "overview", label: "Overview" }]} tab={tab} onTab={setTab}>
 *     …cards…
 *   </AppShell>
 */
import type { ReactNode } from "react";
import { cx } from "./format";
import type { Status } from "./types";
import { Segmented, StatusDot } from "./ui";

export interface ShellStatus {
  status: Status;
  text: string;
  /** Muted text after the label, e.g. a URL or "2 drafts". */
  note?: string;
  onClick?: () => void;
  title?: string;
}

export function AppIcon({ icon }: { icon: string }) {
  return (
    <span
      className="flex size-8 shrink-0 items-center justify-center rounded-[9px] border border-hairline bg-gradient-to-b from-white/70 to-sky/60 text-lg shadow-sm dark:from-white/10 dark:to-ink/60"
      aria-hidden
    >
      {icon}
    </span>
  );
}

export function AppShell<T extends string>({
  icon, name, status, actions, tabs, tab, onTab, wide, children,
}: {
  icon: string;
  name: string;
  status?: ShellStatus | null;
  actions?: ReactNode;
  tabs?: { value: T; label: ReactNode }[];
  tab?: T;
  onTab?: (t: T) => void;
  /** Full width (reading views, galleries); default caps at the hub's 1280px. */
  wide?: boolean;
  children: ReactNode;
}) {
  const chip = status && (
    <>
      <StatusDot status={status.status} /> {status.text}
      {status.note && <span className="text-fg3">{status.note}</span>}
    </>
  );
  const chipClass = "glass inline-flex items-center gap-2 rounded-full px-3 py-1 text-[13px] font-medium text-fg";
  return (
    <>
      <div className="backdrop-plain fixed inset-0 -z-10" aria-hidden />
      <div className={cx("mx-auto flex w-full flex-col gap-5 px-4 pt-6 pb-16 sm:px-8", !wide && "max-w-[1280px]")}>
        <header className="flex flex-wrap items-center gap-3">
          <AppIcon icon={icon} />
          <h1 className="text-[22px] font-semibold tracking-tight text-fg">{name}</h1>
          {status && (status.onClick
            ? <button onClick={status.onClick} title={status.title} className={cx(chipClass, "focus-ring")}>{chip}</button>
            : <span title={status.title} className={chipClass}>{chip}</span>)}
          {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
        {tabs && tab !== undefined && onTab && tabs.length > 1 && (
          <div className="overflow-x-auto scroll-thin">
            <Segmented value={tab} options={tabs} onChange={onTab} />
          </div>
        )}
        {children}
      </div>
    </>
  );
}
