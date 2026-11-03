// Vendored from MyHome (components/ui.tsx) by scripts/sync-hub-ui.mjs. Don't edit here.
import { AlertTriangle, RotateCw, Search, X } from "lucide-react";
import {
  createContext, useCallback, useContext, useEffect, useState, type ButtonHTMLAttributes, type KeyboardEvent, type ReactNode, type Ref,
} from "react";
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

/** A run's status, plus "stopped" for something that is simply off (a web UI nobody started). */
export type Shown = Status | "stopped";

const STATUS: Record<Shown, { label: string; dot: string; text: string; icon: string }> = {
  ok: { label: "OK", dot: "bg-ok", text: "text-ok", icon: "✓" },
  failed: { label: "Failed", dot: "bg-danger", text: "text-danger", icon: "✕" },
  timeout: { label: "Timed out", dot: "bg-warn", text: "text-warn", icon: "⏱" },
  running: { label: "Running", dot: "bg-accent pulse-dot", text: "text-accent-text", icon: "●" },
  starting: { label: "Starting", dot: "bg-warn pulse-dot", text: "text-warn", icon: "●" },
  cancelled: { label: "Cancelled", dot: "bg-fg3", text: "text-fg3", icon: "■" },
  paused: { label: "Paused", dot: "bg-sky", text: "text-fg2", icon: "⏸" },
  lost: { label: "Lost", dot: "bg-fg3", text: "text-fg3", icon: "?" },
  unknown: { label: "Unknown", dot: "bg-fg3", text: "text-fg3", icon: "·" },
  stopped: { label: "Stopped", dot: "bg-fg3", text: "text-fg3", icon: "○" },
};

export function StatusDot({ status, className }: { status: Shown; className?: string }) {
  return <span className={cx("inline-block size-2 shrink-0 rounded-full", STATUS[status]?.dot, className)} />;
}

export function StatusLabel({ status }: { status: Shown }) {
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
            "focus-ring rounded-[calc(var(--r-ctl)-2px)] font-medium transition-all duration-200",
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

/** A centered spinner with the same padding everywhere, for a section that is still loading. */
export function Loading({ className }: { className?: string }) {
  return (
    <div className={cx("flex items-center justify-center py-8", className)}>
      <Spinner />
    </div>
  );
}

/** A section whose data couldn't be fetched: what failed, why, and (optionally) a retry. */
export function ErrorState({ what, error, onRetry, className }: { what: string; error?: unknown; onRetry?: () => void; className?: string }) {
  const message = error instanceof Error ? error.message : error ? String(error) : "";
  return (
    <div role="alert" className={cx("flex flex-col items-center justify-center gap-1.5 py-8 text-center text-sm", className)}>
      <AlertTriangle size={20} className="text-danger" aria-hidden />
      <div className="font-medium text-fg">Couldn't load {what}</div>
      {message && <div className="max-w-md text-xs break-words text-fg3">{message}</div>}
      {onRetry && (
        <Button size="sm" variant="ghost" className="mt-1" onClick={onRetry}>
          <RotateCw size={13} /> Try again
        </Button>
      )}
    </div>
  );
}

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";

const BAR: Record<Tone, string> = { neutral: "bg-fg3", accent: "bg-accent", ok: "bg-ok", warn: "bg-warn", danger: "bg-danger" };

/** A progress bar. `fraction` is 0–1; null/undefined draws an indeterminate pulse. */
export function Progress({
  fraction,
  tone = "accent",
  size = "md",
  label,
  className,
}: {
  fraction?: number | null;
  tone?: Tone;
  size?: "xs" | "sm" | "md";
  label?: string;
  className?: string;
}) {
  const pct = fraction != null ? Math.min(100, Math.max(0, fraction * 100)) : null;
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct != null ? Math.round(pct) : undefined}
      aria-label={label}
      className={cx("overflow-hidden rounded-full bg-hairline", size === "xs" ? "h-1" : size === "sm" ? "h-1.5" : "h-2", className)}
    >
      <div
        className={cx("h-full rounded-full transition-[width] duration-700", BAR[tone], pct == null && "w-1/3 animate-pulse")}
        style={pct != null ? { width: `${pct}%` } : undefined}
      />
    </div>
  );
}

const PILL: Record<Tone, string> = {
  neutral: "border-hairline text-fg3",
  accent: "border-accent/30 bg-accent/10 text-accent-text",
  ok: "border-ok/30 bg-ok/10 text-ok",
  warn: "border-warn/30 bg-warn/10 text-warn",
  danger: "border-danger/30 bg-danger/10 text-danger",
};

/** A small status pill ("connected", "not set", "3 failed"). */
export function Pill({ children, tone = "neutral", title, className }: { children: ReactNode; tone?: Tone; title?: string; className?: string }) {
  return (
    <span
      title={title}
      className={cx("inline-flex shrink-0 items-center gap-1 rounded-full border px-2 text-[11px] leading-[18px] font-medium whitespace-nowrap", PILL[tone], className)}
    >
      {children}
    </span>
  );
}
export const Badge = Pill;

/** A label over a big tabular number. With onClick it's a button (e.g. a filter). */
export function Stat({
  value,
  label,
  tone,
  size = "md",
  onClick,
  className,
}: {
  value: ReactNode;
  label: ReactNode;
  tone?: "ok" | "warn" | "danger";
  size?: "sm" | "md" | "lg";
  onClick?: () => void;
  className?: string;
}) {
  const inner = (
    <>
      <span
        className={cx(
          "tabular leading-tight",
          size === "sm" ? "text-lg font-medium" : size === "lg" ? "text-3xl font-light" : "text-2xl font-light",
          tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : tone === "danger" ? "text-danger" : "text-fg",
        )}
      >
        {value}
      </span>
      <span className={cx("text-fg3", size === "sm" ? "text-[11px]" : "text-[12px]")}>{label}</span>
    </>
  );
  return onClick ? (
    <button onClick={onClick} className={cx("focus-ring flex flex-col items-start rounded-ctl px-3 py-2 text-left hover:bg-material-soft", className)}>
      {inner}
    </button>
  ) : (
    <div className={cx("flex flex-col items-start", className)}>{inner}</div>
  );
}

/**
 * A search input with its icon. `bar` sits flush at the top of a panel (a hairline under it); `box` is a
 * standalone field like inputClass.
 */
export function SearchField({
  value,
  onChange,
  placeholder,
  label,
  variant = "box",
  size = "md",
  trailing,
  inputRef,
  onKeyDown,
  autoFocus,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  label?: string;
  variant?: "bar" | "box";
  size?: "md" | "lg";
  trailing?: ReactNode;
  inputRef?: Ref<HTMLInputElement>;
  onKeyDown?: (e: KeyboardEvent<HTMLInputElement>) => void;
  autoFocus?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex min-w-0 items-center",
        variant === "bar"
          ? cx("border-b border-hairline px-4", size === "lg" ? "gap-3" : "gap-2.5")
          : "h-9 gap-2 rounded-ctl border border-hairline bg-material-strong px-3 focus-within:ring-2 focus-within:ring-accent",
        className,
      )}
    >
      <Search size={size === "lg" ? 17 : 15} className="shrink-0 text-fg3" aria-hidden />
      <input
        ref={inputRef}
        type="search"
        aria-label={label ?? placeholder}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        className={cx(
          "min-w-0 flex-1 appearance-none bg-transparent text-fg outline-none placeholder:text-fg3 [&::-webkit-search-cancel-button]:hidden",
          variant === "bar" ? (size === "lg" ? "h-13 text-[15px]" : "h-10 text-sm") : "h-full text-sm",
        )}
      />
      {trailing}
    </div>
  );
}

/** A dialog over a scrim, in the strong glass style. Esc or a click outside closes it. */
export function Modal({
  title,
  icon,
  label,
  onClose,
  children,
  wide,
  className,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  /** Accessible name when the title isn't plain text. */
  label?: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  className?: string;
}) {
  useEffect(() => {
    const on = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-scrim p-4 pt-[6vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={label ?? (typeof title === "string" ? title : undefined)}
        className={cx("glass-strong rise w-full rounded-card", wide ? "max-w-4xl" : "max-w-2xl", className)}
      >
        {title !== undefined && (
          <header className="flex items-center gap-2 px-5 pt-4 pb-2">
            {icon && <span className="text-fg3">{icon}</span>}
            <h2 className="card-title text-[13px] font-semibold tracking-wide text-fg2 uppercase">{title}</h2>
            <IconButton className="ml-auto" label="Close" onClick={onClose}>
              <X size={15} />
            </IconButton>
          </header>
        )}
        <div className={title !== undefined ? "px-5 pb-5" : undefined}>{children}</div>
      </section>
    </div>
  );
}

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
          "absolute top-0.5 left-0.5 size-5 rounded-full bg-knob shadow transition-transform duration-200 ease-spring",
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
              "glass-strong rise pointer-events-auto rounded-card px-4 py-3 text-sm",
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
