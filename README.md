# airec

**A personal research digest that reads the firehose for you.**

airec collects everything new in your corner of AI (arXiv, Semantic Scholar, Hugging Face,
GitHub, research blogs and, optionally, the news), judges each item against an
*interest map* you write in plain language, and hands you a digest you can read in ten
minutes:

- **an article on top**, in plain language, that connects the week's papers and repos by
  theme and cites each one, and
- **a short note on each selected item** below it: *what it is · how it works · what they
  found · open it if…*, written to help you decide whether to read the original.

It runs when you ask it to, on your machine. Bulk work runs on a local model (LM Studio,
Ollama, llama.cpp, vLLM…). The few writing-heavy steps can use any OpenAI-compatible API
(DeepSeek by default), or stay local too.

---

## How it works

```
collect   arXiv (a full sweep of your categories), Semantic Scholar (one search per topic),
          HF daily papers, new GitHub repos, research blogs         → 1,000–5,000 items
triage    keep or drop from title + summary, 25 per model call       → a few hundred
score     0–10 with a one-line reason, against your interest map
select    notes and one-line mentions; a share kept for things outside your map
read      a four-paragraph note per selected item, from the paper's own text
news      optional: model releases, lab posts, HN, Reddit, web search since the last run
write     the article, from the notes → markdown file + the History page
```

- **No keyword filter.** Nothing is dropped for missing a keyword. A model looks at
  every item, and the least relevant carry over to the next run if a run hits its budget.
- **Nothing is done twice.** A local index remembers every item and every judgement,
  cached per version of your interest map.
- **Nothing is shown twice, unless there's a reason.** A paper comes back only when
  something changed (code released, now trending, matches a topic you just added), and
  it says so.
- **Your interests, not seed papers.** The interest map is a YAML file of topics, each
  with a summary, what counts and what doesn't, and search queries per source. The
  easiest way to write it is to let a strong model do it (see below).

## Quick start

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), a local OpenAI-compatible
model server, and `llama-server` from llama.cpp for embeddings (`brew install llama.cpp`).

```bash
git clone https://github.com/zhezhou1106/airec && cd airec
uv venv && uv pip install -e ".[dev]"
uv run rec init      # creates config/ and .env from the examples
uv run rec serve     # http://127.0.0.1:8765
```

Then, in the app:

1. **Settings → Models:** point the `local` provider at your server, set the model names
   it lists, and click **Test every model**. The embedding server starts by itself on first
   use: `scripts/embed-server.sh` downloads Qwen3-Embedding-0.6B (~640 MB) once.
2. **Settings → Keys:** add a key for your API provider (e.g. `DEEPSEEK_API_KEY`). To stay
   fully local, point the `article`, `news`, `deep_read` and `map_editor` phases at the
   local provider instead.
3. **Settings → Interests:** click **Copy prompt**, paste it into the strongest chat model
   you use, describe your interests where it says *"Write here"*, and paste the answer
   back. Preview the diff, then **Replace** or **Merge**.
4. **Run → Start run.** The first run covers the last 7 days and takes a while
   (30–60 minutes on a laptop, mostly local triage and scoring); later runs only process
   what's new.

Optional: **Run → Backfill** fills the index with the past 12 months of arXiv (metadata
and embeddings, about 1 GB a year), so older papers can resurface when your interests change.

## The app

| Page | What it does |
|---|---|
| **Run** | Start a digest (since the last one, or the last N days; news on or off), and watch it live: each phase with its progress, model, calls, tokens and details (items per source, kept per topic, score distribution, chosen notes). Cancel, resume, backfill. |
| **History** | Every digest. On wide screens the article's citations become margin notes. Each note has a **Deep read** button for a long, careful reading by the strong model. Every run's full log and every model call (prompt, reply, tokens, latency) are kept. |
| **Settings** | **Interests** (copy prompt, paste answer, diff, replace or merge, version history) · **Sources** · **Models** (one model per phase, connection test) · **Digest** (size, news, output folder) · **Keys**. Every save is checked first, and the previous version is kept. |

Digests are also written as markdown to `data/digests/`, and to a folder of your choice
(an Obsidian vault works well).

## Configuration

| File | What's in it |
|---|---|
| `config/interests.yaml` | your interest map: topics, what counts, per-source search queries, repos to watch |
| `config/sources.yaml` | which sources are on, feeds, limits |
| `config/models.yaml` | providers (local server, APIs) and which model runs each phase |
| `config/settings.yaml` | digest size, news window, output folder, device |
| `.env` | API keys only |

`config/` and `.env` are git-ignored: they're yours. The templates live in
`config.example/` and `.env.example`.

**Keys** (all optional except the one your models need):

| Key | Used for |
|---|---|
| `DEEPSEEK_API_KEY` (or your provider's) | article, news, deep reads, map editor |
| `TAVILY_API_KEY` | web search for news and the map editor (falls back to Brave, then DuckDuckGo) |
| `BRAVE_API_KEY` | second choice for web search |
| `SEMANTIC_SCHOLAR_API_KEY` | steadier topic searches (works without one on the shared pool) |
| `GITHUB_TOKEN` | higher GitHub rate limits |

## Commands

```
rec init                                 create config/ and .env from the examples
rec serve                                the web app
rec run [--days N] [--news/--no-news]    make a digest
rec resume [RUN_ID]                      continue a failed or cancelled run
rec backfill [--months N]                fill the index from arXiv
rec deep-read arxiv:2609.12345           long reading of one item
rec revise-map "add ..." [--no-web]      propose a revised interest map with the API model
rec check                                ping every configured model
rec status                               index size and recent runs
```

## Project layout

```
src/
  sources/     one file per source (arxiv, semantic_scholar, hf_papers, github, feeds,
               hf_models, community, web_news) behind a small Source interface
  phases/      one file per pipeline step (collect, embed, resurface, triage, score,
               select, read, news, write)
  prompts/     every prompt as plain text; edit wording without touching code
  web/         FastAPI + htmx app: routes, templates, static
  pipeline.py  the run, start to finish
  store.py     the SQLite index
  llm.py       OpenAI-compatible chat and embedding clients
  search.py    web search with fallbacks
config.example/  starter config
scripts/         embedding-server helper (llama.cpp)
tests/           the whole pipeline and every page, offline
```

**Adding a source:** write a `Source` subclass in `src/sources/` with a `name`, a
`track` (`knowledge` or `news`) and `fetch(since, until) -> list[item]`. Register it in
`sources/__init__.py` and give it a section in `config/sources.yaml`.

## Tests

```bash
uv run pytest
```

The tests run the whole pipeline with fake sources and models, and exercise every page
and every write path of the app. No network or GPU needed.

## Notes

- arXiv's search API allows about one request every 3 seconds, so the category sweep uses
  arXiv's bulk interface (OAI-PMH), and topic searches go to Semantic Scholar.
- Reddit and DuckDuckGo rate-limit anonymous clients aggressively. When a source fails, the
  run logs a warning and carries on.
- Everything stays on your machine except requests to the sources and the API models you
  configure.

## License

MIT. Bundles [htmx](https://htmx.org) (BSD Zero-Clause).
