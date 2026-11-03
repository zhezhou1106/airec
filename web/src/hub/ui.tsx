// Vendored from MyHome (components/ui.tsx) by scripts/sync-hub-ui.mjs. Don't edit here.
import { createContext, useCallback, useContext, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cx } from "./format";
import type { Status } from "./types";

export function Card({
  title,
  icon,
  action,
  children,
  className,
  bodyClass,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
}) {
  return (
    <section className={cx("glass rounded-card rise flex min-w-0 flex-col", className)}>
      {(title || action) && (
        <header className="flex items-center gap-2 px-5 pt-4 pb-1">
          {icon && <span className="text-fg3">{icon}</span>}
          <h2 className="card-title text-[13px] font-semibold tracking-wide text-fg2 uppercase">{title}</h2>
          <div className="ml-auto flex items-center gap-1">{action}</div>
        </header>
      )}
      <div className={cx("min-h-0 flex-1 px-5 pt-2 pb-5", bodyClass)}>{children}</div>
    </section>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  const styles: Record<Variant, string> = {
    primary: "bg-accent text-on-accent hover:brightness-105 active:brightness-95 font-semibold",
    secondary: "bg-material-strong text-fg border border-hairline hover:bg-material",
    ghost: "text-fg2 hover:bg-material-soft",
    danger: "bg-material-strong text-danger border border-hairline hover:bg-material",
  };
  return (
    <button
      className={cx(
        "focus-ring inline-flex items-center justify-center gap-1.5 rounded-ctl transition-[filter,background] duration-200 disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "h-7 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        styles[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function IconButton({
  label,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={cx(
        "focus-ring inline-flex size-8 items-center justify-center rounded-full text-fg2 transition-colors hover:bg-material-soft hover:text-fg disabled:opacity-40",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

const STATUS: Record<Status, { label: string; dot: string; text: string; icon: string }> = {
  ok: { label: "OK", dot: "bg-ok", text: "text-ok", icon: "✓" },
  failed: { label: "Failed", dot: "bg-danger", text: "text-danger", icon: "✕" },
  timeout: { label: "Timed out", dot: "bg-warn", text: "text-warn", icon: "⏱" },
  running: { label: "Running", dot: "bg-accent pulse-dot", text: "text-accent-text", icon: "●" },
  starting: { label: "Starting", dot: "bg-warn pulse-dot", text: "text-warn", icon: "●" },
  cancelled: { label: "Cancelled", dot: "bg-fg3", text: "text-fg3", icon: "■" },
  paused: { label: "Paused", dot: "bg-sky", text: "text-fg2", icon: "⏸" },
  lost: { label: "Lost", dot: "bg-fg3", text: "text-fg3", icon: "?" },
  unknown: { label: "Unknown", dot: "bg-fg3", text: "text-fg3", icon: "·" },
};

export function StatusDot({ status, className }: { status: Status; className?: string }) {
  return <span className={cx("inline-block size-2 shrink-0 rounded-full", STATUS[status]?.dot, className)} />;
}

export function StatusLabel({ status }: { status: Status }) {
  const s = STATUS[status] ?? STATUS.unknown;
  return (
    <span className={cx("inline-flex items-center gap-1.5 text-[13px] font-medium", s.text)}>
      <span aria-hidden>{s.icon}</span>
      {s.label}
    </span>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  size = "md",
}: {
  value: T;
  options: { value: T; label: ReactNode }[];
  onChange: (v: T) => void;
  size?: "sm" | "md";
}) {
  return (
    <div role="tablist" className="inline-flex rounded-ctl border border-hairline bg-material-soft p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={cx(
            "focus-ring rounded-[8px] font-medium transition-all duration-200",
            size === "sm" ? "px-2.5 py-1 text-[12px]" : "px-3.5 py-1.5 text-[13px]",
            o.value === value ? "bg-material-strong text-fg shadow-sm" : "text-fg3 hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center text-sm text-fg3">
      {icon && <div className="text-2xl opacity-70">{icon}</div>}
      {children}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cx("inline-block size-4 animate-spin rounded-full border-2 border-hairline border-t-accent", className)}
      aria-label="Loading"
    />
  );
}

export const inputClass =
  "focus-ring h-9 w-full rounded-ctl border border-hairline bg-material-strong px-3 text-sm text-fg placeholder:text-fg3";

export function Field({ label, help, children }: { label: ReactNode; help?: ReactNode; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[13px] font-medium text-fg2">{label}</span>
      {children}
      {help && <span className="text-xs text-fg3">{help}</span>}
    </label>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cx(
        "focus-ring relative h-6 w-10 shrink-0 rounded-full transition-colors duration-200",
        checked ? "bg-accent" : "bg-hairline",
      )}
    >
      <span
        className={cx(
          "absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform duration-200 ease-spring",
          checked && "translate-x-4",
        )}
      />
    </button>
  );
}

export function Slider({
  value,
  min,
  max,
  step,
  onChange,
  label,
  format,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  label: string;
  format?: (v: number) => string;
}) {
  return (
    <label className="flex items-center gap-3 text-[13px] text-fg2">
      <span className="w-12 shrink-0 font-medium">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1 flex-1 cursor-pointer accent-[var(--accent)]"
      />
      <span className="tabular w-12 text-right text-fg3">{format ? format(value) : value}</span>
    </label>
  );
}

// ── toasts ──────────────────────────────────────────────────────────────
type Toast = { id: number; text: string; kind: "info" | "error" };
const ToastCtx = createContext<(text: string, kind?: Toast["kind"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex max-w-sm flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cx(
              "glass-strong rise pointer-events-auto rounded-2xl px-4 py-3 text-sm",
              t.kind === "error" ? "text-danger" : "text-fg",
            )}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);
