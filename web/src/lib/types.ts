export interface Overview {
  title: string;
  weeks: string[];
  schedule: string;
  scheduled: boolean;
  reports_dir: string;
  hub: boolean;
}
export interface Section {
  key: string;
  title: string;
  markdown: string;
  highlights: string[];
  stats: Record<string, unknown>;
  warnings: string[];
  window: string;
}
export interface WeekReport {
  week: string;
  window: string | null;
  sections: Section[];
  highlights: string[];
  warnings: string[];
  raw: string;
  /** The report file as written, front matter included: what the shared renderer draws. */
  markdown: string;
  cover: string | null;
  file: string;
}
/** One row of the Weeks gallery. */
export interface WeekSummary {
  week: string;
  window: string | null;
  highlights: string[];
  sections: { title: string; count: number }[];
  cover: string | null;
  latest: boolean;
}
export interface Plan { title: string; phrases: string[]; categories: string[] }
export interface Direction { id: string; title: string; description: string; paused: boolean; plan: Plan | null }
export interface DirectionList {
  directions: Direction[];
  papers: { max_papers: number; min_score: number; quality_rules: string[] };
}
export interface Preview {
  title: string; phrases: string[]; categories: string[];
  candidates: number; relevant: number; days: number;
  papers: { title: string; url: string; score: number; reason: string }[];
}

export type Scalar = string | number | boolean | null;
export type Value = Scalar | Value[] | { [k: string]: Value };
export interface ConfigData {
  file: string;
  report: { title: string; reader: string; first_window_days: number; max_window_days: number; copy_to: string | null; notify: boolean; link: string | null };
  schedule: { weekday: number; hour: number; max_attempts: number };
  cover: { enabled: boolean; model: string | null; reasoning: string | null; timeout: number };
  sections: { key: string; title: string; values: Record<string, Value> }[];
  models: {
    providers: { name: string; base_url: string; api_key_env: string; key_set: boolean }[];
    roles: { name: string; provider: string; model: string; temperature: number }[];
  };
  schedule_text: string;
  scheduled: boolean;
}
