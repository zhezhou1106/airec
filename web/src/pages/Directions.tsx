import { useQueryClient } from "@tanstack/react-query";
import { Compass, Pencil, Plus, Search, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { ConfirmButton } from "../components/ConfirmButton";
import { cx } from "../hub/format";
import { Button, Card, Empty, Field, Spinner, Toggle, useToast } from "../hub/ui";
import { del, openUrl, post, put } from "../lib/api";
import { useDirections } from "../lib/hooks";
import type { Direction, Preview } from "../lib/types";

const textareaClass =
  "focus-ring w-full resize-y rounded-ctl border border-hairline bg-material-strong px-3 py-2 text-sm leading-relaxed text-fg placeholder:text-fg3";

/** The paper directions: one card each, with the add form and the quality rules beside them. */
export default function Directions() {
  const { data, isLoading, error } = useDirections();
  if (isLoading) return <div className="py-10"><Spinner /></div>;
  if (error || !data) return <Card><Empty>{(error as Error)?.message ?? "Couldn't load the directions."}</Empty></Card>;
  const active = data.directions.filter((d) => !d.paused).length;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
      <div className="flex min-w-0 flex-col gap-4 lg:col-span-8">
        {data.directions.length === 0 ? (
          <Card>
            <Empty icon={<Compass size={22} />}>
              <span className="text-[15px] font-medium text-fg">No directions yet</span>
              <span>Add one, and the next report starts tracking it.</span>
            </Empty>
          </Card>
        ) : (
          data.directions.map((d) => <DirectionCard key={d.id} direction={d} />)
        )}
      </div>

      <aside className="flex min-w-0 flex-col gap-4 lg:col-span-4">
        <NewDirection maxPapers={data.papers.max_papers} active={active} />
        <Card title="Quality rules" icon={<ShieldCheck size={15} />}>
          <ol className="flex list-decimal flex-col gap-1.5 pl-5 text-[13px] text-fg2 marker:text-fg3">
            {data.papers.quality_rules.map((r, i) => <li key={i}>{r}</li>)}
          </ol>
          <p className="mt-3 text-xs text-fg3">
            Papers need a relevance score of {Math.round(data.papers.min_score)} or more to reach these rules. Change them
            under <code>papers</code> in config.yaml.
          </p>
        </Card>
      </aside>
    </div>
  );
}

function useRefresh() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["directions"] });
}

function NewDirection({ maxPapers, active }: { maxPapers: number; active: number }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const refresh = useRefresh();
  const toast = useToast();
  const add = async () => {
    setBusy(true);
    setProblem("");
    try {
      const { id } = await post<{ id: string }>("/api/directions", { description: text });
      setText("");
      await refresh();
      toast(`Added ${id}. The next report tracks it.`);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="New direction" icon={<Plus size={15} />}>
      <p className="mb-3 text-sm text-fg2">
        Describe what you follow. Each direction brings at most {maxPapers} papers a week, only if they pass the quality
        rules. {active} active now.
      </p>
      <Field label="Description" help={problem ? <span className="text-danger">{problem}</span> : undefined}>
        <textarea
          rows={6}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="What the direction is about, the questions you care about, and what should not count."
          className={textareaClass}
        />
      </Field>
      <div className="mt-3 flex gap-2">
        <Button variant="primary" disabled={busy || !text.trim()} onClick={add}>
          {busy ? <Spinner /> : <Plus size={14} />} Add direction
        </Button>
      </div>
    </Card>
  );
}

function DirectionCard({ direction: d }: { direction: Direction }) {
  const refresh = useRefresh();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(false);
  const long = d.description.length > 360;
  const [draft, setDraft] = useState(d.description);
  const [problem, setProblem] = useState("");
  const [preview, setPreview] = useState<{ state: "running" } | { state: "done"; result: Preview } | { state: "failed"; error: string } | null>(null);

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    try {
      await fn();
      await refresh();
      if (done) toast(done);
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    }
  };
  const save = async () => {
    setProblem("");
    try {
      await put(`/api/directions/${d.id}`, { description: draft });
      await refresh();
      setEditing(false);
      toast("Saved. The search plan is rebuilt on the next run or preview.");
    } catch (e) {
      setProblem((e as Error).message);
    }
  };
  const runPreview = async () => {
    setPreview({ state: "running" });
    try {
      setPreview({ state: "done", result: await post<Preview>(`/api/directions/${d.id}/preview`) });
    } catch (e) {
      setPreview({ state: "failed", error: (e as Error).message });
    }
  };

  return (
    <Card
      title={d.title}
      icon={<Compass size={15} />}
      action={
        <label className="flex items-center gap-2 text-[12px] text-fg3">
          {d.paused ? "Paused" : "Active"}
          <Toggle
            checked={!d.paused}
            label={d.paused ? "Resume this direction" : "Pause this direction"}
            onChange={(on) => act(() => post(`/api/directions/${d.id}/paused`, { paused: !on }), on ? "Resumed." : "Paused. Reports skip it until you resume.")}
          />
        </label>
      }
      className={cx(d.paused && "opacity-75")}
    >
      {editing ? (
        <div className="flex flex-col gap-3">
          <Field label="Description" help={problem ? <span className="text-danger">{problem}</span> : "Saving rebuilds the search plan on the next run or preview."}>
            <textarea rows={8} value={draft} onChange={(e) => setDraft(e.target.value)} className={textareaClass} autoFocus />
          </Field>
          <div className="flex gap-2">
            <Button variant="primary" size="sm" disabled={!draft.trim()} onClick={save}>Save</Button>
            <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setDraft(d.description); setProblem(""); }}>Cancel</Button>
          </div>
        </div>
      ) : (
        <>
          <p className={cx("text-sm leading-relaxed whitespace-pre-wrap text-fg2", long && !open && "line-clamp-4")}>{d.description}</p>
          {long && (
            <button onClick={() => setOpen(!open)} className="focus-ring mt-1 rounded text-[13px] text-accent-text hover:underline">
              {open ? "Show less" : "Show all"}
            </button>
          )}
        </>
      )}

      {!editing && (
        <div className="mt-3">
          {d.plan ? (
            <details className="group">
              <summary className="cursor-pointer text-[13px] font-medium text-fg3 select-none hover:text-fg">
                Search plan · {d.plan.phrases.length} phrases
              </summary>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {d.plan.phrases.map((p) => (
                  <span key={p} className="rounded-full border border-hairline bg-material-soft px-2.5 py-0.5 text-[12px] text-fg2">{p}</span>
                ))}
              </div>
              <p className="mt-2 text-xs text-fg3">Categories: {d.plan.categories.join(", ") || "all"}</p>
            </details>
          ) : (
            <p className="text-xs text-fg3">The search plan is built on the next run or preview.</p>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={preview?.state === "running"} onClick={runPreview}>
          {preview?.state === "running" ? <Spinner /> : <Search size={13} />} Preview
        </Button>
        {!editing && <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil size={13} /> Edit</Button>}
        <span className="ml-auto" />
        <ConfirmButton size="sm" variant="ghost" confirmLabel="Remove for good?" onConfirm={() => act(() => del(`/api/directions/${d.id}`), `Removed ${d.title}.`)}>
          Remove
        </ConfirmButton>
      </div>

      {preview && <PreviewResult preview={preview} onClose={() => setPreview(null)} />}
    </Card>
  );
}

function PreviewResult({ preview, onClose }: {
  preview: { state: "running" } | { state: "done"; result: Preview } | { state: "failed"; error: string };
  onClose: () => void;
}) {
  return (
    <div className="mt-4 rounded-ctl border border-hairline bg-material-soft p-4" aria-live="polite">
      {preview.state === "running" && (
        <p className="flex items-center gap-2 text-sm text-fg2"><Spinner /> Searching and judging the last two weeks. This takes a minute or two.</p>
      )}
      {preview.state === "failed" && <p className="text-sm text-danger">{preview.error}</p>}
      {preview.state === "done" && (
        <>
          <div className="flex items-start gap-2">
            <p className="flex-1 text-[13px] text-fg2">
              Last {preview.result.days} days: <span className="tabular text-fg">{preview.result.candidates}</span> candidates,{" "}
              <span className="tabular text-fg">{preview.result.relevant}</span> relevant,{" "}
              <span className="tabular text-fg">{preview.result.papers.length}</span> passed the quality gate.
            </p>
            <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
          </div>
          {preview.result.papers.length > 0 && (
            <ul className="mt-3 flex flex-col gap-3">
              {preview.result.papers.map((p) => (
                <li key={p.url} className="text-sm">
                  <div className="flex items-baseline gap-2">
                    <span className="tabular shrink-0 text-xs text-fg3">{p.score.toFixed(1)}</span>
                    <a
                      href={p.url}
                      onClick={(e) => { e.preventDefault(); openUrl(p.url); }}
                      className="font-medium text-accent-text hover:underline"
                    >
                      {p.title}
                    </a>
                  </div>
                  <p className="mt-0.5 pl-8 text-[13px] text-fg3">{p.reason}</p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
