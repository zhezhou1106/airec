"""Sources, one file each. To add one: write a Source subclass and register it here."""
from __future__ import annotations

from sources.arxiv import Arxiv
from sources.base import Source, SourceContext
from sources.community import HackerNews, Reddit
from sources.feeds import Feeds
from sources.github import GithubReleases, GithubSearch
from sources.hf_models import HfModels
from sources.hf_papers import HfPapers
from sources.semantic_scholar import SemanticScholar
from sources.web_news import WebNews

REGISTRY: dict[str, type[Source]] = {
    "arxiv": Arxiv,
    "hf_papers": HfPapers,
    "semantic_scholar": SemanticScholar,
    "github": GithubSearch,
    "feeds": Feeds,
    "github_releases": GithubReleases,
    "hf_models": HfModels,
    "hackernews": HackerNews,
    "reddit": Reddit,
    "web_news": WebNews,
}


def build(ctx: SourceContext, track: str) -> list[Source]:
    """The enabled sources for one track, in the order sources.yaml lists them."""
    out: list[Source] = []
    for name, conf in (ctx.cfg.sources or {}).items():
        cls = REGISTRY.get(name)
        if cls is None or not isinstance(conf, dict) or not conf.get("enabled", True):
            continue
        if cls is Feeds:
            out.append(Feeds(ctx, conf, track=track))
        elif cls.track == track:
            out.append(cls(ctx, conf))
    return out
