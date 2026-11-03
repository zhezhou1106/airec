# airec

**A weekly AI briefing that runs itself.**

airec (shown as AI Weekly in the hub; command `rec`) writes one Markdown report a week with three sections:

| Section | What you get | How it stays small |
|---|---|---|
| **GitHub: rising repos** | New AI repositories gaining stars, with what each one does | Measures momentum, then reads READMEs only for the top candidates |
| **AI news** | Model releases, techniques, company moves and tools, merged and sorted | One editor pass over lab feeds, Hugging Face, Hacker News, Reddit, web search and repo releases |
| **Papers on your directions** | At most five papers a week per direction, only if they pass strict quality rules | Targeted searches, title and abstract screening, no category sweeps and no full-text reading |

For papers you write a description and nothing else. A model derives the search phrases,
categories and criteria and scores each new paper against your description. A second, stricter
model then checks the shortlist against written quality rules and keeps at most five, or none in
a weak week, with a short takeaway for each. Papers it keeps improve next week's recommendations.

The report opens with **At a glance**: once the sections are done, one writer call reads them all and
writes a paragraph on the repos, one on the news and one per paper direction, connecting them. If that
call fails, the report falls back to each section's plain highlights.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/zhezhou1106/airec && cd airec
uv venv && uv pip install -e ".[dev]"
source .venv/bin/activate

rec init               # creates ~/.airec
$EDITOR ~/.airec/.env  # add your keys
rec doctor             # checks models, keys and every source
rec run                # your first report
rec schedule install   # from now on, weekly (macOS)
rec serve              # the app, at http://127.0.0.1:8765
```

Reports land in `~/.airec/reports/2026-W45.md`, plus any folder you set as `report.copy_to` (an
Obsidian vault works well). `rec open` opens the latest one. A macOS notification announces each
report; with `brew install terminal-notifier`, clicking it opens the report, or the URL you set as
`report.link`.

## The app

`rec serve` serves a small app for reading and steering. Inside [my-hub](#using-it-with-my-hub)
it looks like one of the hub's own pages and follows its style:

- **Weeks:** every report, newest first, one row each: the date window, the first highlights,
  the sections with their item counts and the cover, if the week has one. A row opens that week
  (`#/weeks/2026-W45`), drawn by the hub's own report renderer so it looks exactly like the hub's
  Outputs viewer, with earlier/later week buttons, the run notes and the report file.
- **Configuration:** **Directions** (add, edit, pause or remove a direction, see its search plan,
  and **Preview** what it would catch in the last two weeks, without recording anything), then
  **Report**, **Schedule**, **Sections**, **Models** and **Cover**, which edit `config.yaml`. A save
  writes only the values you changed, keeps the file's comments and layout, is checked against the
  same models `rec` uses (nothing is written if it is invalid), and keeps the previous file as
  `config.yaml.bak-<time>` (the last five). Models show the *name* of each key's variable and
  whether it is set, never the key. The schedule needs no reinstall: the agent wakes hourly and
  reads it each time.

The page is a small React app in `web/`, built into `src/airec/web/static/` (the build is
kept in the repo, so installing airec needs no Node). It uses the hub's own design layer, copied into
`web/src/hub/` by `web/scripts/sync-hub-ui.mjs`: the same page frame (`AppShell`) as the hub's
other app pages, and the same report renderer (`report.tsx`) as the hub's Outputs viewer. After changing it, or after the hub's design changes:

```bash
npm --prefix web ci
npm --prefix web run sync-hub-ui     # HUB_WEB=<my-hub>/web if the hub is not in ~/Desktop/home
npm --prefix web run build           # typecheck, then build into src/airec/web/static
npm --prefix web run dev             # live reload at :5175, against `rec serve --port 8765`
```

When the hub starts the app it sets `HUB_API` and `HUB_TOKEN`: the server reads the hub's
appearance so the first paint already matches, and opens links through the hub (a new tab does
nothing inside the hub's window).

## Tracking a direction

Add a direction in the app, or from the terminal:

```bash
rec direction add "Continual learning for LLMs: how a deployed model keeps acquiring ..."
rec direction add              # or write it in your editor
rec direction test <name>      # see what it would catch in the last two weeks
rec direction list
```

A direction is a Markdown file in `~/.airec/directions/`. The file name is its id and the text is
the description, so you can also just create or edit files. Editing the text rebuilds the search
plan on the next run. Prefix a file with `_` to pause it. The more precisely you say what does and
does not count, the better the screening.

Each paper passes two checks. `papers.min_score` is the relevance score needed to reach the quality
gate, and `papers.quality_rules` replaces the built-in rules. `papers.max_papers` caps the week.

## Commands

```
rec init                        create ~/.airec (or $AIREC_HOME); never overwrites
rec run                         generate this week's report
    --dry-run                   write a draft to reports/drafts; remember nothing
    --section papers            draft one section
    --days 3                    draft the last N days
    --if-due                    run only if this week's report is owed (the scheduler uses this)
rec direction add [TEXT]        add a direction (omit TEXT to write it in your editor)
    --name NAME                 file name; derived from the plan's title if omitted
rec direction list              your directions and their search plans
rec direction test NAME         what a direction would catch, recording nothing
    --days 14                   how far back to look
rec cover [--week 2026-W45]     (re)draw the overview image for a report (default the latest)
rec schedule install|uninstall  turn the weekly run on or off (macOS)
rec serve [--port N]            the app
rec open                        open the latest report
rec status                      schedule state and recent runs
rec doctor                      check config, keys, models and sources
```

## How the weekly run behaves

- **The schedule survives a closed laptop.** A launchd agent checks hourly and at login, and
  generates the report only if this week's is owed, so a Mac that was off at 9:00 on Monday reports
  when it next wakes.
- **Failures are contained.** Each section runs on its own. A dead feed costs that feed, shown in
  the report's run notes. A failed section is marked in the report, its window stays uncovered, and
  only that section is retried (up to `schedule.max_attempts` a week, then again next week). State
  is recorded after the report is written, so an interrupted run leaves nothing half-done.
- **Nothing repeats.** Each section picks up where it last succeeded, up to
  `report.max_window_days`, and anything shown is remembered. A manual `rec run` rebuilds the
  week's report; if a section fails during a rebuild, its earlier version is kept.
- **It costs little.** Plans are cached per description and each paper is judged once per direction.
  Every report ends with its model usage; full prompts and replies are in `~/.airec/logs/`.

## Configuration

Everything personal lives in `~/.airec` (or `$AIREC_HOME`), outside the repo:

```
config.yaml     models, schedule, report, and one block per section
.env            API keys
directions/     one file per paper direction
reports/        the weekly reports
airec.sqlite    memory between runs
logs/           model traces and scheduler output
```

| Key | Used for | |
|---|---|---|
| `DEEPSEEK_API_KEY` | all model calls with the default config | required (or point `llm.providers` at any OpenAI-compatible server) |
| `GITHUB_TOKEN` | repo details, READMEs and releases | strongly recommended |
| `TAVILY_API_KEY` | news from labs without a feed | optional |
| `SEMANTIC_SCHOLAR_API_KEY` | steadier conference papers and recommendations | optional; they work without a key through a shared pool that often refuses, in which case airec retries patiently and then continues with arXiv |

Models are chosen by role in `config.yaml`: `fast` screens in bulk, `writer` produces what you
read. `report.reader` describes you, and tailors the news and repo sections.

## Using it with my-hub

The hub registers airec with the manifest in my-hub's `config/apps/airec.toml` (it lives in the hub,
so this repo stays untouched; see that file for the full list of actions and the live run view). The
app root is your airec home, not the code:

```toml
path = "${HOME}/.airec"
id   = "airec"
name = "AI Weekly"

[service]
cmd  = ["${HUB}/Apps/airec/.venv/bin/rec", "serve", "--port", "{port}"]
port = "auto"

[[outputs]]
label   = "Weekly report"
glob    = "reports/*.md"
latest  = "name"
primary = true

[[widgets]]
id        = "this-week"
title     = "AI Weekly"
kind      = "list"
file      = "hub-card.json"
placement = "results"
```

Set `report.link` to `http://127.0.0.1:7777/#/app/airec/outputs/{path}` so notifications and
`rec open` go to the hub. airec writes `hub-card.json` with each report, and the hub shows its
highlights in Latest results. The hub's Stop button sends SIGTERM; a run stops and is recorded as
`cancelled`.

## Adding a section

A section is a module in `src/airec/sections/`. This is all of it:

```python
from airec.config import Strict
from airec.sections import register
from airec.sections.base import Section, SectionResult, Window


@register
class Podcasts(Section):
    key = "podcasts"
    title = "Podcasts"

    class Config(Strict):  # options from config.yaml, validated
        feeds: list[str] = []

    def run(self, window: Window) -> SectionResult:
        episodes = self.gather("feeds", lambda: fetch(self.config.feeds, window), [])
        self.require_sources()
        seen = self.ctx.store.shown_before(self.key, [e.id for e in episodes], self.ctx.week)
        episodes = [e for e in episodes if e.id not in seen]
        return SectionResult(
            markdown=render(episodes),
            highlights=[...],                  # fallback bullets for "At a glance"
            shown=[e.id for e in episodes],    # not to repeat next week
        )
```

Then add a `podcasts:` block to `config.yaml`; its position there is its position in the report.
The section gets the run's services through `self.ctx`: `llm`, `http`, `store` and `secrets`. It can
keep small documents with `store.get_state` and `set_state`, or declare tables in `schema`; state
for later weeks goes in a `commit` callable on the `SectionResult`, which the pipeline calls only
once the report is written (never for a draft or a failed run). Windows,
failure handling, retries and recording `shown` come from the pipeline; the section filters
against `shown_before` itself, because what counts as a repeat differs by section.

## Project layout

```
src/airec/
  cli.py        the rec command
  pipeline.py   one run: windows, failure isolation, delivery, is_due
  report.py     assembling and delivering the Markdown
  schedule.py   the launchd agent
  config.py     paths, secrets, validated config
  llm.py        role-based OpenAI-compatible client, validated JSON replies
  http.py       shared client with retries
  store.py      SQLite memory
  sections/     the plugins: base.py (contract), github_trends, ai_news, papers/
  web/          the app: the FastAPI JSON API (__init__.py), config.yaml editing (config_edit.py)
                and the built page in static/
  sources/      outside services, knowing nothing about reports
  prompts/      every prompt as plain text
  defaults/     what `rec init` copies
tests/          offline: a fake web and a scripted model
web/            the app's React source (Vite, Tailwind, the hub's vendored design layer)
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## License

MIT
