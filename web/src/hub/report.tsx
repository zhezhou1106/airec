// Vendored from MyHome (kit/report.tsx) by scripts/sync-hub-ui.mjs. Don't edit here.
/**
 * Markdown documents and the weekly digest, rendered the way the hub's Outputs viewer shows them. Apps vendor this
 * file (scripts/sync-hub-ui.mjs) so their own pages render a report exactly like the hub does; the hub imports it
 * from components/content.tsx. It is self-contained (react, react-markdown, remark-gfm, styles from index.css) so
 * the copy needs no rewriting. Anything host-specific is injected:
 *
 *   <ReportView openLink={(url) => post("/api/open", { url })} resolveImage={(src) => `/api/assets/${src}`}>
 *     {markdown}
 *   </ReportView>
 *
 * `openLink` gets http(s) links (without it they open in a new tab); `resolveImage` gets relative image paths only
 * ("assets/x.png"; absolute URLs, "/…" and "#…" pass through unchanged).
 */
import { Children, createContext, isValidElement, useContext, useRef, type ReactElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export interface ReportEnv {
  /** Opens an http(s) link (inside the hub a target=_blank link does nothing). */
  openLink?: (href: string) => void;
  /** Maps a relative image path from the markdown to a URL the page can load. */
  resolveImage?: (src: string) => string | undefined;
}

const EnvContext = createContext<ReportEnv>({});

/** Split YAML front matter (Obsidian-style notes) from the body. */
export function frontMatter(text: string): [[string, string][], string] {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text);
  if (!m) return [[], text];
  const pairs = m[1]
    .split(/\r?\n/)
    .map((line) => /^([\w.-]+):\s*(.*)$/.exec(line))
    .filter((x): x is RegExpExecArray => !!x)
    .map((x) => [x[1], x[2]] as [string, string]);
  return [pairs, text.slice(m[0].length)];
}

const slug = (text: string) => text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "");

function plainText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(plainText).join("");
  return isValidElement(node) ? plainText((node.props as { children?: ReactNode }).children) : "";
}

/** The second-level headings, for the jump links. Lines inside code fences don't count. */
function sections(body: string): string[] {
  let fenced = false;
  const out: string[] = [];
  for (const line of body.split(/\r?\n/)) {
    if (line.startsWith("```")) fenced = !fenced;
    else if (!fenced && line.startsWith("## ")) out.push(line.slice(3).replace(/[*_`[\]]|\(http[^)]*\)/g, "").trim());
  }
  return out;
}

function MdImage({ src, alt }: { src?: string; alt?: string }) {
  const { resolveImage } = useContext(EnvContext);
  const url = !src || !resolveImage || /^([a-z][a-z0-9+.-]*:|\/|#)/i.test(src) ? src : resolveImage(src);
  return <img src={url} alt={alt ?? ""} loading="lazy" />;
}

function MdLink({ href, children }: { href?: string; children?: ReactNode }) {
  const { openLink } = useContext(EnvContext);
  if (!openLink) return <a href={href} target="_blank" rel="noopener">{children}</a>;
  return (
    <a
      href={href}
      onClick={(e) => {
        if (href?.startsWith("http")) {
          e.preventDefault();
          openLink(href);
        }
      }}
    >
      {children}
    </a>
  );
}

const MD_COMPONENTS: Components = {
  img: ({ src, alt }) => <MdImage src={typeof src === "string" ? src : undefined} alt={alt} />,
  a: ({ href, children }) => <MdLink href={href}>{children}</MdLink>,
  h2: ({ children }) => <h2 id={`s-${slug(plainText(children))}`}>{children}</h2>,
  table: ({ children }) => <div className="report-table"><table>{children}</table></div>,
};

/** Markdown in the page's prose style (manuals, notes): front matter as chips, then the text. */
export function Markdown({ children, className, ...env }: { children: string; className?: string } & ReportEnv) {
  const [meta, body] = frontMatter(children);
  return (
    <EnvContext.Provider value={env}>
      {meta.length > 0 && <MetaChips meta={meta} className="mb-4" />}
      <div
        className={cx(
          "prose prose-sm max-w-none dark:prose-invert prose-headings:tracking-tight prose-a:text-accent-text prose-code:before:content-none prose-code:after:content-none prose-pre:bg-material-strong prose-pre:text-fg prose-pre:border prose-pre:border-hairline prose-table:text-[13px]",
          className,
        )}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{body}</ReactMarkdown>
      </div>
    </EnvContext.Provider>
  );
}

export function MetaChips({ meta, className }: { meta: [string, string][]; className?: string }) {
  return (
    <dl className={cx("flex flex-wrap gap-1.5 text-xs", className)}>
      {meta.map(([k, v]) => (
        <div key={k} className="flex gap-1.5 rounded-full bg-sky/35 px-2.5 py-0.5 dark:bg-sky/15">
          <dt className="text-fg3">{k}</dt>
          <dd className="max-w-96 truncate font-medium text-fg2" title={v}>{v.replace(/^\[|\]$/g, "")}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A markdown file set as a document: its own solid page, a title block and jump links. Generic markdown gets plain
 *  typography only; a weekly digest (front matter `tags: [airec, ...]`) gets per-section layouts, see `DigestBody`. */
export function ReportView({ children, ...env }: { children: string } & ReportEnv) {
  const [meta, text] = frontMatter(children);
  // A leading "# Title" and "*subtitle*" lead the page, with the file's details under them.
  const head = /^\s*# +(.+)\r?\n+(?:\*([^*\n]+)\*[ \t]*(?:\r?\n|$))?/.exec(text);
  let body = head ? text.slice(head[0].length) : text;
  const root = useRef<HTMLElement>(null);
  const digest = isDigest(meta);
  // A digest's overview image (the first thing in its body) is shown as a cover under the title.
  const cover = digest ? /^\s*!\[([^\]]*)\]\(([^)\s]+)\)[ \t]*(?:\r?\n|$)/.exec(body) : null;
  if (cover) body = body.slice(cover[0].length);
  const toc = sections(body);
  return (
    <EnvContext.Provider value={env}>
    <article ref={root} className={cx("report", digest && "digest")}>
      {head && <h1>{head[1].replace(/[*_`]/g, "")}</h1>}
      {head?.[2] && <p className="report-subtitle">{head[2]}</p>}
      {cover && (
        <figure className="digest-cover">
          <MdImage src={cover[2]} alt={cover[1]} />
        </figure>
      )}
      {meta.length > 0 && <MetaChips meta={meta} />}
      {toc.length > 2 && (
        <nav aria-label="Sections" className="report-toc">
          {toc.map((title) => (
            <button key={title} onClick={() => root.current?.querySelector(`#s-${CSS.escape(slug(title))}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{title}</button>
          ))}
        </nav>
      )}
      {digest ? <DigestBody body={body} /> : <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{body}</ReactMarkdown>}
    </article>
    </EnvContext.Provider>
  );
}

// ── Weekly digest (airec) ───────────────────────────────────────────────────────────────────────────
// Each "## " section is rendered on its own, with a layout picked from its heading. A section whose items
// don't have the expected shape falls back to plain list items, so a format change degrades, not breaks.

function isDigest(meta: [string, string][]) {
  return meta.some(([k, v]) => k === "tags" && /(^|[\s,[])airec([\s,\]]|$)/.test(v));
}

type DigestKind = "glance" | "repos" | "news" | "papers" | "plain";

function digestKind(chunk: string): DigestKind {
  const h = /^## +(.*)/.exec(chunk)?.[1].toLowerCase() ?? "";
  if (!h) return "plain";
  if (h.includes("glance")) return "glance";
  if (/github|repo/.test(h)) return "repos";
  if (h.includes("news")) return "news";
  if (h.includes("paper")) return "papers";
  return "plain";
}

/** The body cut at each second-level heading (outside code fences). The first chunk is whatever precedes them. */
function splitSections(body: string): string[] {
  const out: string[] = [];
  let cur: string[] = [];
  let fenced = false;
  for (const line of body.split(/\r?\n/)) {
    if (line.startsWith("```")) fenced = !fenced;
    if (!fenced && line.startsWith("## ") && cur.length) {
      out.push(cur.join("\n"));
      cur = [];
    }
    cur.push(line);
  }
  if (cur.length) out.push(cur.join("\n"));
  return out.filter((c) => c.trim());
}

const NEWS_COMPONENTS: Components = { ...MD_COMPONENTS, li: ({ children }) => <NewsItem>{children}</NewsItem> };
const PAPER_COMPONENTS: Components = { ...MD_COMPONENTS, li: ({ children }) => <PaperItem>{children}</PaperItem> };
const SECTION_COMPONENTS: Record<DigestKind, Components> = {
  glance: MD_COMPONENTS,
  repos: MD_COMPONENTS,
  news: NEWS_COMPONENTS,
  papers: PAPER_COMPONENTS,
  plain: MD_COMPONENTS,
};

function DigestBody({ body }: { body: string }) {
  return (
    <>
      {splitSections(body).map((chunk, i) => {
        const kind = digestKind(chunk);
        return (
          <section key={i} className={`digest-${kind}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={SECTION_COMPONENTS[kind]}>{chunk}</ReactMarkdown>
          </section>
        );
      })}
    </>
  );
}

/** A list item's inline content, unwrapped from the <p> a loose list puts around it. */
function itemNodes(children: ReactNode): ReactNode[] {
  const nodes = Children.toArray(children).filter((n) => !(typeof n === "string" && !n.trim()));
  if (nodes.length === 1 && isValidElement(nodes[0]) && nodes[0].type === "p") {
    return Children.toArray((nodes[0].props as { children?: ReactNode }).children);
  }
  return Children.toArray(children);
}

const isEl = (n: ReactNode, type: string): n is ReactElement => isValidElement(n) && n.type === type;
const isLink = (n: ReactNode): n is ReactElement => isValidElement(n) && typeof (n.props as { href?: unknown }).href === "string";
const trimStart = (nodes: ReactNode[]) => (typeof nodes[0] === "string" ? [nodes[0].trimStart(), ...nodes.slice(1)] : nodes);

/** "**Headline.** Summary. [Source](…) · [Source](…)" → headline, summary, then the sources as chips. */
function NewsItem({ children }: { children?: ReactNode }) {
  const nodes = itemNodes(children);
  if (!isEl(nodes[0], "strong")) return <li>{children}</li>;
  const headline = (nodes[0].props as { children?: ReactNode }).children;
  let end = nodes.length;
  const sources: ReactElement[] = [];
  while (end > 1) {
    const n = nodes[end - 1];
    if (isLink(n)) sources.unshift(n);
    else if (!(typeof n === "string" && /^[\s·|,]*$/.test(n))) break;
    end--;
  }
  const summary = trimStart(nodes.slice(1, end));
  return (
    <li className="news-item">
      <div className="news-head">{typeof headline === "string" ? headline.replace(/\.\s*$/, "") : headline}</div>
      {summary.some((n) => typeof n !== "string" || n.trim()) && <p className="news-summary">{summary}</p>}
      {sources.length > 0 && <div className="news-sources">{sources}</div>}
    </li>
  );
}

/** "**[Title](…)** (9/10 · ▲23 · [code](…))  \n summary" → a score badge, the title, chips, then the summary. */
function PaperItem({ children }: { children?: ReactNode }) {
  const nodes = itemNodes(children);
  const br = nodes.findIndex((n) => isEl(n, "br"));
  if (!isEl(nodes[0], "strong")) return <li>{children}</li>;
  const metaNodes = nodes.slice(1, br < 0 ? 1 : br);
  const chips: ReactNode[] = [];
  for (const n of metaNodes) {
    if (typeof n === "string") chips.push(...n.replace(/[()]/g, "").split("·").map((t) => t.trim()).filter(Boolean));
    else chips.push(n);
  }
  const scoreAt = chips.findIndex((c) => typeof c === "string" && /^\d+(\.\d+)?\/10$/.test(c));
  const score = scoreAt >= 0 ? (chips.splice(scoreAt, 1)[0] as string).split("/")[0] : null;
  const summary = trimStart(br < 0 ? nodes.slice(1) : nodes.slice(br + 1));
  return (
    <li className="paper-item">
      <div className={cx("paper-score", score && Number(score) >= 9 && "paper-score-top")} title={score ? `Relevance ${score}/10` : undefined}>
        {score ? <><b>{score}</b><span>/10</span></> : "·"}
      </div>
      <div className="min-w-0">
        <div className="paper-title">{nodes[0]}</div>
        {br >= 0 && chips.length > 0 && (
          <div className="paper-chips">
            {chips.map((c, i) => <span key={i} className={cx("paper-chip", typeof c === "string" && c.startsWith("▲") && "paper-chip-votes")}>{c}</span>)}
          </div>
        )}
        {summary.some((n) => typeof n !== "string" || n.trim()) && <p className="paper-summary">{summary}</p>}
      </div>
    </li>
  );
}
