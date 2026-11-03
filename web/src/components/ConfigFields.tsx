import { Plus, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cx } from "../hub/format";
import { Button, Toggle, inputClass } from "../hub/ui";
import type { Value } from "../lib/types";

/** Field errors from the server, by dotted path ("schedule.hour", "sections.ai_news.feeds.2.url"). */
export type Errors = Record<string, string>;

export const textareaClass =
  "focus-ring w-full resize-y rounded-ctl border border-hairline bg-material-strong px-3 py-2 text-sm leading-relaxed text-fg placeholder:text-fg3";

/** A labelled row: label and help on the left on wide screens, the control on the right. */
export function Row({ label, help, error, children, wide }: { label: ReactNode; help?: ReactNode; error?: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={cx("grid grid-cols-1 gap-x-6 gap-y-1.5 border-t border-hairline py-3.5 first:border-t-0 first:pt-1", !wide && "sm:grid-cols-[220px_minmax(0,1fr)]")}>
      <div className="min-w-0">
        <div className="font-mono text-[13px] font-medium text-fg2">{label}</div>
        {help && <div className="mt-0.5 text-xs text-fg3">{help}</div>}
      </div>
      <div className="min-w-0">
        {children}
        {error && <div className="mt-1.5 text-xs text-danger">{error}</div>}
      </div>
    </div>
  );
}

export function TextInput({ value, onChange, placeholder, nullable, mono }: { value: string | null; onChange: (v: string | null) => void; placeholder?: string; nullable?: boolean; mono?: boolean }) {
  return (
    <input
      className={cx(inputClass, mono && "font-mono text-[13px]")}
      value={value ?? ""}
      placeholder={placeholder}
      onChange={(e) => onChange(nullable && e.target.value === "" ? null : e.target.value)}
    />
  );
}

export function NumberInput({ value, onChange, step, min, max }: { value: number | null; onChange: (v: number | null) => void; step?: number; min?: number; max?: number }) {
  return (
    <input
      type="number"
      className={cx(inputClass, "tabular max-w-40")}
      value={value ?? ""}
      step={step ?? 1}
      min={min}
      max={max}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
    />
  );
}

export function Switch({ value, onChange, label }: { value: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <div className="flex h-9 items-center gap-3">
      <Toggle checked={value} onChange={onChange} label={label} />
      <span className="text-sm text-fg3">{value ? "On" : "Off"}</span>
    </div>
  );
}

/** A list of short strings as removable chips plus an input (Enter or comma adds). */
export function Chips({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState("");
  const add = () => {
    const items = text.split(",").map((t) => t.trim()).filter((t) => t && !value.includes(t));
    if (items.length) onChange([...value, ...items]);
    setText("");
  };
  return (
    <div className="focus-within:ring-accent/50 flex min-h-9 flex-wrap items-center gap-1.5 rounded-ctl border border-hairline bg-material-strong px-2 py-1.5">
      {value.map((item, i) => (
        <span key={`${item}-${i}`} className="inline-flex items-center gap-1 rounded-full bg-sky/35 py-0.5 pr-1 pl-2.5 text-xs text-fg2 dark:bg-sky/15">
          {item === "" ? <em className="text-fg3">(empty)</em> : item}
          <button type="button" className="focus-ring rounded-full p-0.5 text-fg3 hover:text-danger" aria-label={`Remove ${item || "empty item"}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
            <X size={11} />
          </button>
        </span>
      ))}
      <input
        className="min-w-32 flex-1 bg-transparent px-1 text-sm text-fg outline-none placeholder:text-fg3"
        value={text}
        placeholder={placeholder ?? "Add…"}
        onChange={(e) => setText(e.target.value)}
        onBlur={add}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          } else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
        }}
      />
    </div>
  );
}

type Row = Record<string, Value>;

/** A list of flat records (feeds) as an editable table. Columns come from the rows; booleans are checkboxes. */
export function Table({ value, onChange, columns, errors, path }: { value: Row[]; onChange: (v: Row[]) => void; columns?: string[]; errors: Errors; path: string }) {
  const cols = columns ?? [...new Set(value.flatMap((r) => Object.keys(r)))];
  const bools = new Set(cols.filter((c) => value.some((r) => typeof r[c] === "boolean")));
  const set = (i: number, c: string, v: Value) => onChange(value.map((r, j) => (j === i ? { ...r, [c]: v } : r)));
  const blank = (): Row => Object.fromEntries(cols.map((c) => [c, bools.has(c) ? false : ""]));
  return (
    <div className="flex flex-col gap-2">
      <div className="report-table m-0">
        <table className="w-full text-[13px]">
          <thead>
            <tr>
              {cols.map((c) => <th key={c} className={cx("px-3 py-2 text-left font-mono text-xs font-semibold text-fg2", bools.has(c) && "w-20 text-center")}>{c}</th>)}
              <th className="w-10" />
            </tr>
          </thead>
          <tbody>
            {value.map((r, i) => (
              <tr key={i} className="border-t border-hairline">
                {cols.map((c) => {
                  const err = errors[`${path}.${i}.${c}`];
                  return (
                    <td key={c} className={cx("px-1.5 py-1", bools.has(c) && "text-center")} title={err}>
                      {bools.has(c) ? (
                        <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={r[c] === true} aria-label={`${c} for row ${i + 1}`} onChange={(e) => set(i, c, e.target.checked)} />
                      ) : (
                        <input
                          className={cx("focus-ring h-8 w-full rounded-[6px] bg-transparent px-1.5 text-fg hover:bg-material-soft", c === "url" && "font-mono text-[12px]", err && "ring-1 ring-danger")}
                          value={r[c] == null ? "" : String(r[c])}
                          aria-label={`${c} for row ${i + 1}`}
                          onChange={(e) => set(i, c, e.target.value)}
                        />
                      )}
                    </td>
                  );
                })}
                <td className="px-1 text-right">
                  <button type="button" className="focus-ring rounded-ctl p-1.5 text-fg3 hover:text-danger" aria-label={`Remove row ${i + 1}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
                    <X size={13} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <Button size="sm" variant="ghost" onClick={() => onChange([...value, blank()])}><Plus size={13} /> Add row</Button>
      </div>
    </div>
  );
}

/** An editor picked from the value's own shape (for the untyped section blocks). */
export function AnyField({ value, original, onChange, errors, path }: { value: Value; original: Value; onChange: (v: Value) => void; errors: Errors; path: string }) {
  if (typeof original === "boolean") return <Switch value={value === true} onChange={onChange} label={path} />;
  if (typeof original === "number") return <NumberInput value={value as number | null} onChange={onChange} step={Number.isInteger(original) ? 1 : 0.05} />;
  if (Array.isArray(original)) {
    if (original.every((x) => typeof x === "string")) return <Chips value={value as string[]} onChange={onChange} />;
    if (original.every((x) => x && typeof x === "object" && !Array.isArray(x))) return <Table value={value as Row[]} onChange={onChange} errors={errors} path={path} />;
  }
  if (original === null || typeof original === "string") return <TextInput value={value as string | null} onChange={onChange} nullable={original === null} />;
  return <pre className="rounded-ctl border border-hairline bg-material-strong p-2 font-mono text-xs text-fg3">{JSON.stringify(original, null, 2)}</pre>;
}
