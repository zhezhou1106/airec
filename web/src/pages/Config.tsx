import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, Cpu, FileText, Image, Info, Layers, RotateCcw, Save } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { AnyField, NumberInput, Row, Switch, TextInput, textareaClass, type Errors } from "../components/ConfigFields";
import { cx } from "../hub/format";
import { Button, Card, ErrorState, Loading, Pill, Segmented, StatusDot, inputClass, useToast } from "../hub/ui";
import { ApiError, put } from "../lib/api";
import { useConfig } from "../lib/hooks";
import type { ConfigData, Value } from "../lib/types";
import Directions from "./Directions";

export const SUBTABS = [
  { value: "directions", label: "Directions" },
  { value: "report", label: "Report" },
  { value: "schedule", label: "Schedule" },
  { value: "sections", label: "Sections" },
  { value: "models", label: "Models" },
  { value: "cover", label: "Cover" },
] as const;
export type Sub = (typeof SUBTABS)[number]["value"];

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** Configuration: the directions, then one sub-tab per block of config.yaml, each with its own Save. */
export default function Config({ sub, go }: { sub: Sub; go: (path: string) => void }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="overflow-x-auto scroll-thin">
        <Segmented size="sm" value={sub} options={[...SUBTABS]} onChange={(s) => go(`config/${s}`)} />
      </div>
      {sub === "directions" ? <Directions /> : <Editor sub={sub} />}
    </div>
  );
}

function Editor({ sub }: { sub: Exclude<Sub, "directions"> }) {
  const { data, error, isLoading, isError, refetch } = useConfig();
  if (isLoading) return <Loading />;
  if (isError || !data) return <Card><ErrorState what="config.yaml" error={error} onRetry={() => refetch()} /></Card>;
  const Tab = { report: ReportTab, schedule: ScheduleTab, sections: SectionsTab, models: ModelsTab, cover: CoverTab }[sub];
  return <Tab key={sub} data={data} />;
}

// ── save plumbing ────────────────────────────────────────────────────────────

/** A draft of one tab's values: reset when the saved config changes, dirty when it differs. */
function useDraft<T>(saved: T) {
  const [draft, setDraft] = useState<T>(saved);
  const key = JSON.stringify(saved);
  useEffect(() => setDraft(saved), [key]);
  return { draft, setDraft, dirty: JSON.stringify(draft) !== key, reset: () => setDraft(saved) };
}

function useSave(part: string) {
  const qc = useQueryClient();
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [problem, setProblem] = useState("");
  const save = async (values: unknown) => {
    setSaving(true);
    setErrors({});
    setProblem("");
    try {
      const res = await put<{ changed: boolean; config: ConfigData }>(`/api/config/${part}`, { values });
      qc.setQueryData(["config"], res.config);
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["directions"] });
      toast(res.changed ? "Saved to config.yaml. The next run uses it." : "Nothing changed.");
    } catch (e) {
      if (e instanceof ApiError && e.errors.length) {
        setErrors(Object.fromEntries(e.errors.map((x) => [x.loc.join("."), x.msg])));
        setProblem("Nothing was saved. Fix the marked fields.");
      } else setProblem((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return { save, saving, errors, problem };
}

function EditorCard({ title, icon, dirty, saving, onSave, onReset, problem, children, aside }: {
  title: string; icon: ReactNode; dirty: boolean; saving: boolean; onSave: () => void; onReset: () => void;
  problem?: string; children: ReactNode; aside?: ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
      <div className="flex min-w-0 flex-col gap-4 lg:col-span-8">
        <Card
          title={title}
          icon={icon}
          action={
            <>
              {dirty && <Button size="sm" variant="ghost" disabled={saving} onClick={onReset}><RotateCcw size={13} /> Revert</Button>}
              <Button size="sm" variant="primary" disabled={!dirty || saving} onClick={onSave}><Save size={13} /> {saving ? "Saving…" : dirty ? "Save" : "Saved"}</Button>
            </>
          }
        >
          {problem && <div className="mb-3 flex items-start gap-2 rounded-ctl border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] text-danger"><AlertTriangle size={14} className="mt-0.5 shrink-0" />{problem}</div>}
          {children}
        </Card>
      </div>
      <aside className="flex min-w-0 flex-col gap-4 lg:col-span-4">{aside}</aside>
    </div>
  );
}

function FileNote({ data, children }: { data: ConfigData; children?: ReactNode }) {
  return (
    <Card title="About" icon={<Info size={15} />}>
      <div className="flex flex-col gap-2 text-[13px] text-fg2">
        {children}
        <p className="text-fg3">
          Saving writes only the values you changed into <code className="text-fg2">config.yaml</code>; its comments and layout stay as
          they are, and the previous file is kept as a backup next to it.
        </p>
        <p className="truncate font-mono text-xs text-fg3" title={data.file}>{data.file.replace(/^\/Users\/[^/]+/, "~")}</p>
      </div>
    </Card>
  );
}

// ── tabs ─────────────────────────────────────────────────────────────────────

function ReportTab({ data }: { data: ConfigData }) {
  const { draft, setDraft, dirty, reset } = useDraft(data.report);
  const { save, saving, errors, problem } = useSave("report");
  const set = <K extends keyof typeof draft>(k: K, v: (typeof draft)[K]) => setDraft({ ...draft, [k]: v });
  const e = (k: string) => errors[`report.${k}`];
  return (
    <EditorCard title="Report" icon={<FileText size={15} />} dirty={dirty} saving={saving} onSave={() => save(draft)} onReset={reset} problem={problem} aside={<FileNote data={data} />}>
      <Row label="title" help="The report's name, in its heading and notifications." error={e("title")}><TextInput value={draft.title} onChange={(v) => set("title", v ?? "")} /></Row>
      <Row label="reader" help="Who the report is for; tailors the news and repo sections." error={e("reader")}>
        <textarea className={cx(textareaClass, "min-h-32")} value={draft.reader} onChange={(ev) => set("reader", ev.target.value)} />
      </Row>
      <Row label="first_window_days" help="How far back the first report looks." error={e("first_window_days")}><NumberInput value={draft.first_window_days} min={1} onChange={(v) => set("first_window_days", v as number)} /></Row>
      <Row label="max_window_days" help="After missed weeks, catch up this far at most." error={e("max_window_days")}><NumberInput value={draft.max_window_days} min={1} onChange={(v) => set("max_window_days", v as number)} /></Row>
      <Row label="copy_to" help="Also write each report here, e.g. an Obsidian folder. Empty for none." error={e("copy_to")}><TextInput mono nullable value={draft.copy_to} placeholder="~/Documents/vault/AI Weekly" onChange={(v) => set("copy_to", v)} /></Row>
      <Row label="notify" help="A macOS notification when a report is ready." error={e("notify")}><Switch value={draft.notify} label="notify" onChange={(v) => set("notify", v)} /></Row>
      <Row label="link" help="What the notification opens; {path} is the report's path. Empty opens the file." error={e("link")}><TextInput mono nullable value={draft.link} placeholder="http://127.0.0.1:7777/#/app/airec/outputs/{path}" onChange={(v) => set("link", v)} /></Row>
    </EditorCard>
  );
}

function ScheduleTab({ data }: { data: ConfigData }) {
  const { draft, setDraft, dirty, reset } = useDraft(data.schedule);
  const { save, saving, errors, problem } = useSave("schedule");
  const e = (k: string) => errors[`schedule.${k}`];
  return (
    <EditorCard
      title="Schedule" icon={<CalendarClock size={15} />} dirty={dirty} saving={saving} onSave={() => save(draft)} onReset={reset} problem={problem}
      aside={
        <FileNote data={data}>
          <p className="flex items-center gap-2 font-medium text-fg">
            <StatusDot status={data.scheduled ? "ok" : "stopped"} /> {data.schedule_text}
          </p>
          <p className="text-fg3">
            {data.scheduled
              ? "The weekly agent wakes every hour and reads these settings each time, so a change applies from its next check. There is nothing to reinstall."
              : <>The weekly agent is not installed. Turn it on with <code className="text-fg2">rec schedule install</code>; it then follows these settings.</>}
          </p>
        </FileNote>
      }
    >
      <Row label="weekday" help="The day the report is due (ISO: 1 is Monday)." error={e("weekday")}>
        <select className={cx(inputClass, "max-w-56 cursor-pointer")} value={draft.weekday} onChange={(ev) => setDraft({ ...draft, weekday: Number(ev.target.value) })}>
          {WEEKDAYS.map((d, i) => <option key={d} value={i + 1}>{d}</option>)}
        </select>
      </Row>
      <Row label="hour" help="Local time. If the Mac is off then, the run happens at its next wake." error={e("hour")}>
        <select className={cx(inputClass, "tabular max-w-56 cursor-pointer")} value={draft.hour} onChange={(ev) => setDraft({ ...draft, hour: Number(ev.target.value) })}>
          {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{`${h}:00`}</option>)}
        </select>
      </Row>
      <Row label="max_attempts" help="Runs per week when a section keeps failing." error={e("max_attempts")}><NumberInput value={draft.max_attempts} min={1} onChange={(v) => setDraft({ ...draft, max_attempts: v as number })} /></Row>
    </EditorCard>
  );
}

function CoverTab({ data }: { data: ConfigData }) {
  const { draft, setDraft, dirty, reset } = useDraft(data.cover);
  const { save, saving, errors, problem } = useSave("cover");
  const e = (k: string) => errors[`cover.${k}`];
  return (
    <EditorCard title="Cover" icon={<Image size={15} />} dirty={dirty} saving={saving} onSave={() => save(draft)} onReset={reset} problem={problem}
      aside={<FileNote data={data}><p>An overview image drawn by the Codex CLI after the text is written, shown under the report's title and in the Weeks gallery.</p></FileNote>}>
      <Row label="enabled" help="Draw a cover for each new report." error={e("enabled")}><Switch value={draft.enabled} label="enabled" onChange={(v) => setDraft({ ...draft, enabled: v })} /></Row>
      <Row label="model" help="Empty uses Codex's own default." error={e("model")}><TextInput mono nullable value={draft.model} placeholder="Codex default" onChange={(v) => setDraft({ ...draft, model: v })} /></Row>
      <Row label="reasoning" help="Codex reasoning effort, e.g. low, medium, high. Empty for its default." error={e("reasoning")}>
        <TextInput mono nullable value={draft.reasoning} placeholder="Codex default" onChange={(v) => setDraft({ ...draft, reasoning: v })} />
      </Row>
      <Row label="timeout" help="Seconds before the drawing is given up (60 or more)." error={e("timeout")}><NumberInput value={draft.timeout} min={60} onChange={(v) => setDraft({ ...draft, timeout: v as number })} /></Row>
    </EditorCard>
  );
}

function SectionsTab({ data }: { data: ConfigData }) {
  const saved = Object.fromEntries(data.sections.map((s) => [s.key, s.values]));
  const { draft, setDraft, dirty, reset } = useDraft(saved);
  const { save, saving, errors, problem } = useSave("sections");
  return (
    <EditorCard title="Sections" icon={<Layers size={15} />} dirty={dirty} saving={saving} onSave={() => save(draft)} onReset={reset} problem={problem}
      aside={<FileNote data={data}><p>Sections run and appear in the report in this order, as listed under <code className="text-fg2">sections</code> in config.yaml.</p></FileNote>}>
      <div className="flex flex-col gap-6">
        {data.sections.map((s) => (
          <section key={s.key} id={`section-${s.key}`}>
            <h3 className="mb-1 flex items-baseline gap-2 text-[15px] font-semibold text-fg">
              {s.title} <span className="font-mono text-xs font-normal text-fg3">{s.key}</span>
            </h3>
            {Object.keys(s.values).length === 0 && <p className="text-sm text-fg3">No settings; the section uses its defaults.</p>}
            {Object.entries(s.values).map(([field, original]) => {
              const path = `sections.${s.key}.${field}`;
              const wide = Array.isArray(original) && original.some((x) => x && typeof x === "object");
              return (
                <Row key={field} label={field} error={errors[path]} wide={wide}>
                  <AnyField
                    value={draft[s.key]?.[field] ?? null}
                    original={original}
                    errors={errors}
                    path={path}
                    onChange={(v: Value) => setDraft({ ...draft, [s.key]: { ...draft[s.key], [field]: v } })}
                  />
                </Row>
              );
            })}
          </section>
        ))}
      </div>
    </EditorCard>
  );
}

function ModelsTab({ data }: { data: ConfigData }) {
  const saved = {
    providers: Object.fromEntries(data.models.providers.map((p) => [p.name, { base_url: p.base_url, api_key_env: p.api_key_env }])),
    roles: Object.fromEntries(data.models.roles.map((r) => [r.name, { provider: r.provider, model: r.model, temperature: r.temperature }])),
  };
  const { draft, setDraft, dirty, reset } = useDraft(saved);
  const { save, saving, errors, problem } = useSave("models");
  const keySet = Object.fromEntries(data.models.providers.map((p) => [p.name, p.key_set]));
  const names = Object.keys(saved.providers);
  const setProvider = (name: string, field: string, v: string) =>
    setDraft({ ...draft, providers: { ...draft.providers, [name]: { ...draft.providers[name], [field]: v } } });
  const setRole = (name: string, field: string, v: string | number | null) =>
    setDraft({ ...draft, roles: { ...draft.roles, [name]: { ...draft.roles[name], [field]: v } } });
  const err = (...loc: string[]) => errors[["llm", ...loc].join(".")];
  return (
    <EditorCard title="Models" icon={<Cpu size={15} />} dirty={dirty} saving={saving} onSave={() => save(draft)} onReset={reset} problem={problem}
      aside={
        <FileNote data={data}>
          <p>Keys stay in <code className="text-fg2">~/.airec/.env</code> or the environment. Here you set only the <em>name</em> of the variable; the app checks whether it is set and never reads the key itself.</p>
        </FileNote>
      }>
      <h3 className="mb-1 text-[13px] font-semibold tracking-wide text-fg2 uppercase">Providers</h3>
      {names.map((name) => (
        <div key={name} className="mb-2 rounded-ctl border border-hairline p-3">
          <div className="mb-2 font-mono text-sm font-semibold text-fg">{name}</div>
          <Row label="base_url" error={err("providers", name, "base_url")}><TextInput mono value={draft.providers[name].base_url} onChange={(v) => setProvider(name, "base_url", v ?? "")} /></Row>
          <Row label="api_key_env" help="The environment variable that holds the key." error={err("providers", name, "api_key_env")}>
            <div className="flex flex-wrap items-center gap-3">
              <div className="max-w-72 flex-1"><TextInput mono value={draft.providers[name].api_key_env} placeholder="DEEPSEEK_API_KEY" onChange={(v) => setProvider(name, "api_key_env", v ?? "")} /></div>
              <Pill tone={keySet[name] ? "ok" : "warn"}>{keySet[name] ? "set" : "not set"}</Pill>
            </div>
          </Row>
        </div>
      ))}
      <h3 className="mt-5 mb-2 text-[13px] font-semibold tracking-wide text-fg2 uppercase">Roles</h3>
      <div className="report-table m-0">
        <table className="w-full text-[13px]">
          <thead>
            <tr>{["role", "provider", "model", "temperature"].map((c) => <th key={c} className="px-3 py-2 text-left font-mono text-xs font-semibold text-fg2">{c}</th>)}</tr>
          </thead>
          <tbody>
            {Object.entries(draft.roles).map(([name, r]) => (
              <tr key={name} className="border-t border-hairline align-top">
                <td className="px-3 py-2.5 font-mono font-semibold text-fg">{name}</td>
                <td className="px-1.5 py-1">
                  <select className={cx(inputClass, "h-8 cursor-pointer")} value={r.provider} onChange={(ev) => setRole(name, "provider", ev.target.value)}>
                    {[...new Set([...names, r.provider])].map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                  {err("roles", name, "provider") && <div className="mt-1 text-xs text-danger">{err("roles", name, "provider")}</div>}
                </td>
                <td className="px-1.5 py-1">
                  <input className={cx(inputClass, "h-8 font-mono text-[12.5px]")} value={r.model} onChange={(ev) => setRole(name, "model", ev.target.value)} />
                  {err("roles", name, "model") && <div className="mt-1 text-xs text-danger">{err("roles", name, "model")}</div>}
                </td>
                <td className="px-1.5 py-1">
                  <NumberInput value={r.temperature} step={0.05} min={0} max={2} onChange={(v) => setRole(name, "temperature", v)} />
                  {err("roles", name, "temperature") && <div className="mt-1 text-xs text-danger">{err("roles", name, "temperature")}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </EditorCard>
  );
}
