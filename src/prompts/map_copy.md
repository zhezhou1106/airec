I keep an "interest map" for a personal research digest: a YAML file that decides which
papers, posts, repos and news get collected and how they are judged. Please revise it
using what I tell you about my interests below. Use your broad and current knowledge of
AI research and engineering (and web search, if you have it) so the map covers the
frontier of what I care about, including directions I may not have named.

THE SCHEMA
{{schema}}

GUIDANCE
- Topics are lines of work, not single papers. Each summary says in plain language what
  counts and why I care.
- priority: core (read closely), side (keep an eye on), explore (adjacent, worth sampling).
  Add a few explore topics for promising directions next to mine.
- queries feed the sources:
    arxiv       short phrases matched in titles and abstracts ("test-time training")
    github      GitHub search syntax ("topic:rlhf", "llm distillation language:python")
    web         news-style searches ("new open-weight video generation model")
    hackernews  one or two words
  Give each topic 3-8 arxiv queries plus a few github and web queries. Use the terms
  the field uses now, including newer names for older ideas.
- watch.repos: GitHub repos whose releases matter. watch.feeds: blogs with RSS; only
  list feed URLs you are confident of.
- Keep the topic ids of my existing topics unchanged, and keep what I wrote unless my
  notes below say otherwise.

MY CURRENT MAP
```yaml
{{current}}
```

MY INTERESTS AND WHAT TO CHANGE
[Write here, in your own words: what you work on, what you want more or less of,
new directions you are curious about.]

ANSWER FORMAT
Reply with the complete revised map in a single ```yaml block, then a short list of what
you changed and why. Only one yaml block.
