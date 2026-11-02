# airec

**A weekly AI briefing that runs itself.**

Once a week airec writes one Markdown report with three sections:

| Section | What you get | How it stays small |
|---|---|---|
| **GitHub: rising repos** | New AI repositories gaining stars, with what each one does | Measures momentum, then reads READMEs only for the top candidates |
| **AI news** | Model releases, techniques, company moves and tools, merged and sorted | One editor pass over lab feeds, Hugging Face, Hacker News, Reddit, web search and repo releases |
| **Papers on your directions** | New papers on each direction you describe | Targeted searches, title and abstract screening, no category sweeps and no full-text reading |

For papers you write a description and nothing else. A model derives the search phrases,
categories and criteria, scores each new paper against your description, and writes a short
takeaway for the best ones. Papers it accepts improve next week's recommendations.

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
```

Reports land in `~/.airec/reports/2026-W45.md`, plus any folder you set as `report.copy_to` (an
Obsidian vault works well). `rec open` opens the latest one. A macOS notification announces each
report; with `brew install terminal-notifier`, clicking it opens the report, or the URL you set as
`report.link`.

## Tracking a direction

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

## Commands

```
rec init                        create ~/.airec (or $AIREC_HOME); never overwrites
rec run                         generate this week's report
    --dry-run                   write a draft to reports/drafts; remember nothing
    --section papers            draft one section
    --days 3                    draft the last N days
    --if-due                    run only if this week's report is owed (the scheduler uses this)
rec direction add|list|test     manage paper directions
rec schedule install|uninstall  turn the weekly run on or off (macOS)
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
| `SEMANTIC_SCHOLAR_API_KEY` | conference papers and recommendations | optional; without it the shared pool often refuses, and airec continues with arXiv |

Models are chosen by role in `config.yaml`: `fast` screens in bulk, `writer` produces what you
read. `report.reader` describes you, and tailors the news and repo sections.

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
            highlights=[...],                  # bullets for "At a glance"
            shown=[e.id for e in episodes],    # not to repeat next week
        )
```

Then add a `podcasts:` block to `config.yaml`; its position there is its position in the report.
The section gets the run's services through `self.ctx`: `llm`, `http`, `store` and `secrets`. It can
keep small documents with `store.get_state` and `set_state`, or declare tables in `schema`. Windows,
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
  sources/      outside services, knowing nothing about reports
  prompts/      every prompt as plain text
  defaults/     what `rec init` copies
tests/          offline: a fake web and a scripted model
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## License

MIT
