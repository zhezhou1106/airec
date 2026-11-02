"""Parsers against realistic payloads."""

from __future__ import annotations

from datetime import timedelta

from airec.http import Http
from airec.sources import news
from airec.sources.github import GitHub, parse_trending
from airec.sources.papers import arxiv_query, hf_daily_papers, parse_arxiv
from tests.conftest import NOW

TRENDING_HTML = """
<article class="Box-row">
  <h2 class="h3"><a class="Link" href="/acme/rocket"><span>acme /</span> rocket</a></h2>
  <p class="col-9">  Fast <b>LLM</b> serving  </p>
  <div>
    <span itemprop="programmingLanguage">Rust</span>
    <a class="Link" href="/acme/rocket/stargazers">12,345</a>
    <span class="d-inline-block float-sm-right">1,234 stars this week</span>
  </div>
</article>
<article class="Box-row"><h2><a href="/solo/no-language">x</a></h2></article>
"""

ARXIV_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2610.12345v2</id>
    <title>A  New
      Method</title>
    <summary>We show X. Code: https://github.com/acme/method.</summary>
    <published>2026-10-30T10:00:00Z</published>
    <author><name>Ada Lovelace</name></author>
  </entry>
</feed>"""


def test_parse_trending():
    first, second = parse_trending(TRENDING_HTML)
    assert first.full_name == "acme/rocket"
    assert (first.stars, first.stars_gained, first.language) == (12345, 1234, "Rust")
    assert first.description == "Fast LLM serving"
    assert second.stars_gained is None and second.stars == 0


def test_github_readme_is_empty_when_missing(web):
    github = GitHub(Http(web, sleep=lambda _s: None))
    assert github.readme("acme/none") == ""


def test_parse_arxiv():
    [paper] = parse_arxiv(ARXIV_XML)
    assert paper.id == "arxiv:2610.12345"
    assert paper.title == "A New Method"
    assert paper.code_url == "https://github.com/acme/method"
    assert paper.authors == ["Ada Lovelace"]


def test_arxiv_query_shape():
    query = arxiv_query(['say "hi"', "GRPO"], ["cs.LG"], NOW - timedelta(days=7), NOW)
    assert 'ti:"say hi" OR abs:"say hi"' in query
    assert "AND (cat:cs.LG)" in query
    assert query.endswith("submittedDate:[202610261200 TO 202611021200]")


def test_hf_daily_walks_each_day(web):
    entry = {"paper": {"id": "2610.1", "title": "T", "summary": "S", "upvotes": 9}}
    web.route("daily_papers", [entry])
    papers = hf_daily_papers(Http(web), NOW - timedelta(days=2), NOW)
    assert len(papers) == 3
    assert papers[0].upvotes == 9


def test_hf_models_keeps_originals_and_skips_derivatives(web):
    created = (NOW - timedelta(days=1)).isoformat()
    original = {
        "id": "lab/model-7b",
        "createdAt": created,
        "likes": 90,
        "pipeline_tag": "text-generation",
        "safetensors": {"total": 7_000_000_000},
        "cardData": {"license": "apache-2.0"},
    }
    quantized = {**original, "id": "someone/model-7b-GGUF"}
    finetune = {**original, "id": "other/finetune", "cardData": {"base_model": "lab/model-7b"}}
    unpopular = {**original, "id": "lab/quiet", "likes": 1}
    old = {**original, "id": "lab/old", "createdAt": "2025-01-01T00:00:00Z"}
    web.route("api/models", [original, quantized, finetune, unpopular, old])
    stories = news.new_hf_models(Http(web), 30, NOW - timedelta(days=7), NOW)
    assert [story.title for story in stories] == ["New on Hugging Face: lab/model-7b"]
    assert "7.0B parameters" in stories[0].summary


def test_feed_filters_by_window(web):
    def item(day: str) -> str:
        return f"<item><title>{day}</title><link>https://x.dev/{day}</link><pubDate>{day}</pubDate></item>"

    rss = (
        "<rss><channel>"
        + item("Sat, 31 Oct 2026 10:00:00 GMT")
        + item("Sat, 03 Jan 2026 10:00:00 GMT")
        + "</channel></rss>"
    )
    web.route("feed.xml", rss)
    stories = news.read_feed(
        Http(web), "Lab", "https://x.dev/feed.xml", NOW - timedelta(days=7), NOW, primary=True
    )
    assert len(stories) == 1
    assert stories[0].primary


def test_story_id_ignores_tracking_parameters():
    first = news.Story("t", "https://a.dev/x?utm_source=rss", "", "s", NOW)
    second = news.Story("t", "https://www.a.dev/x/", "", "s", NOW)
    assert first.id == second.id
