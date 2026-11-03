"""Each section end to end, against the fake web and a scripted model."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import httpx
import pytest

from airec.http import Http
from airec.llm import LlmError
from airec.sections.ai_news import AiNews, _link_text
from airec.sections.base import SectionError, Window
from airec.sections.github_trends import GithubTrends
from airec.sections.papers import Papers
from airec.sections.papers.directions import (
    Direction,
    load_directions,
    remove_direction,
    save_direction,
    set_paused,
    slugify,
    unique_id,
)
from airec.sections.papers.plan import SearchPlan
from airec.sources import news
from airec.sources.news import Story
from airec.sources.papers import S2_ATTEMPTS, hf_daily_papers
from tests.conftest import NOW

WINDOW = Window(NOW - timedelta(days=7), NOW)

# ---- github_trends ----


def trending_page(*repos: tuple[str, int]) -> str:
    return "".join(
        f'<article class="Box-row"><h2><a href="/{name}">x</a></h2>'
        f'<span class="float-sm-right">{gained} stars this week</span></article>'
        for name, gained in repos
    )


def repo_json(name: str, *, days_old: int = 10, stars: int = 500, **extra) -> dict:
    created = (NOW - timedelta(days=days_old)).isoformat()
    return {
        "full_name": name,
        "created_at": created,
        "stargazers_count": stars,
        "description": f"{name} description",
        **extra,
    }


def trends(make_context, answer, **options) -> GithubTrends:
    config = {"trending_languages": [""], "search_topics": [], **options}
    return GithubTrends(make_context(answer), GithubTrends.Config(**config))


def test_trends_keep_new_repos_ranked_by_momentum(make_context, web):
    web.route(
        "github.com/trending", trending_page(("a/slow", 10), ("b/fast", 900), ("c/old", 5000))
    )
    web.route("/repos/a/slow", repo_json("a/slow"))
    web.route("/repos/b/fast", repo_json("b/fast"))
    web.route("/repos/c/old", repo_json("c/old", days_old=900))
    reply = {
        "trend": "Serving is hot.",
        "picks": [
            {"repo": "b/fast", "summary": "Fast thing."},
            {"repo": "ghost/repo", "summary": "Made up."},
        ],
    }
    section = trends(make_context, lambda *_: reply)
    result = section.run(WINDOW)

    user_prompt = section.ctx.llm.prompts[0][2]
    assert user_prompt.index("b/fast") < user_prompt.index("a/slow")
    assert "c/old" not in user_prompt
    assert "ghost/repo" not in result.markdown
    assert "Serving is hot." in result.markdown
    assert "★ 500 (+900)" in result.markdown
    assert result.shown == ["b/fast"]


def test_trends_cooldown_hides_recently_shown_repos(make_context, web, store):
    web.route("github.com/trending", trending_page(("b/fast", 900)))
    web.route("/repos/b/fast", repo_json("b/fast"))
    store.mark_shown("github_trends", ["b/fast"], "2026-W44")
    result = trends(make_context, lambda *_: {"picks": []}).run(WINDOW)
    assert result.stats["candidates"] == 0


def test_trends_measure_momentum_from_a_stored_snapshot(make_context, web, store):
    web.route("search/repositories", {"items": [repo_json("d/steady", stars=1000, days_old=60)]})
    section = trends(make_context, lambda *_: {"picks": []}, search_topics=["llm"])
    store.execute("INSERT INTO repo_stars VALUES ('d/steady', '2026-10-26', 800)")
    repo = section._collect(section_github(section), NOW)[0]
    assert section._momentum(repo, NOW) == 200


def section_github(section: GithubTrends):
    from airec.sources.github import GitHub

    return GitHub(section.ctx.http)


def test_trends_fail_when_every_source_failed(make_context, web):
    web.route("github.com/trending", lambda _r: httpx.Response(500))
    with pytest.raises(SectionError):
        trends(make_context, lambda *_: {}).run(WINDOW)


# ---- ai_news ----


def news_section(make_context, answer, **options) -> AiNews:
    return AiNews(make_context(answer), AiNews.Config(**options))


def rss(*items: tuple[str, str]) -> str:
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link>"
        f"<pubDate>Sat, 31 Oct 2026 10:00:00 GMT</pubDate></item>"
        for title, link in items
    )
    return f"<rss><channel>{body}</channel></rss>"


def test_news_drops_stories_without_real_sources_and_groups_by_category(make_context, web):
    web.route("lab.dev/feed", rss(("Model X", "https://lab.dev/x"), ("Deal", "https://lab.dev/d")))
    web.route("api/models", [])
    reply = {
        "stories": [
            {
                "category": "Companies & industry",
                "headline": "Lab buys Co",
                "summary": "Deal.",
                "sources": ["s2"],
            },
            {
                "category": "Model releases",
                "headline": "Model X ships",
                "summary": "New model.",
                "sources": ["s1"],
                "importance": "high",
            },
            {
                "category": "Model releases",
                "headline": "Invented",
                "summary": "No.",
                "sources": ["s99"],
            },
        ]
    }
    feeds = [{"name": "Lab", "url": "https://lab.dev/feed", "primary": True}]
    result = news_section(make_context, lambda *_: reply, feeds=feeds).run(WINDOW)

    assert "Invented" not in result.markdown
    assert result.markdown.index("### Model releases") < result.markdown.index("### Companies")
    assert "[Lab](https://lab.dev/x)" in result.markdown
    assert result.highlights == ["Model X ships"]
    assert len(result.shown) == 2


def test_news_hides_shown_stories_and_tells_the_editor_past_headlines(make_context, web, store):
    web.route("lab.dev/feed", rss(("Old", "https://lab.dev/old"), ("New", "https://lab.dev/new")))
    web.route("api/models", [])
    store.mark_shown("ai_news", [Story("", "https://lab.dev/old", "", "", NOW).id], "2026-W44")
    store.set_state(
        "ai_news",
        "recent_headlines",
        [
            {"week": "2026-W44", "headline": "Last week's story"},
            {"week": "2026-W45", "headline": "This week's own story"},
        ],
    )
    feeds = [{"name": "Lab", "url": "https://lab.dev/feed"}]
    section = news_section(make_context, lambda *_: {"stories": []}, feeds=feeds)
    section.run(WINDOW)
    _role, system, user = section.ctx.llm.prompts[0]
    assert "New" in user and "Old" not in user
    assert "Last week's story" in system
    assert "This week's own story" not in system


def test_news_survives_one_dead_source(make_context, web):
    web.route("alive.dev/feed", rss(("Fine", "https://alive.dev/1")))
    web.route("dead.dev/feed", lambda _r: httpx.Response(500))
    web.route("api/models", [])
    feeds = [
        {"name": "Alive", "url": "https://alive.dev/feed"},
        {"name": "Dead", "url": "https://dead.dev/feed"},
    ]
    section = news_section(make_context, lambda *_: {"stories": []}, feeds=feeds)
    section.run(WINDOW)
    assert any("Dead" in warning for warning in section.warnings)


def test_news_web_search_skipped_without_key(make_context, web):
    web.route("api/models", [])
    section = news_section(make_context, lambda *_: {}, web_queries=["x"])
    section.run(WINDOW)
    assert any("TAVILY_API_KEY" in warning for warning in section.warnings)


# ---- papers ----

PLAN = {
    "title": "Test-time training",
    "phrases": ["test-time training", "online adaptation", "TTT"],
    "categories": ["cs.LG"],
    "include": ["updates weights at inference"],
    "exclude": ["image benchmarks"],
}


def arxiv_feed(*papers: tuple[str, str]) -> str:
    entries = "".join(
        f"<entry><id>http://arxiv.org/abs/{pid}v1</id><title>{title}</title>"
        f"<summary>About {title}.</summary><published>2026-10-30T10:00:00Z</published></entry>"
        for pid, title in papers
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


def gate_all(user: str) -> dict:
    labels = [line.split(":")[0] for line in user.split("\n\n")]
    return {"decisions": [{"id": label, "keep": True, "reason": "solid"} for label in labels]}


def paper_answers(scores: dict[str, float]):
    """A model that plans, judges by title and writes briefs."""

    def answer(role, system, user):
        if "weekly literature tracker" in system:
            return PLAN
        if "screen new papers" in system:
            verdicts = []
            for line in user.split("\n\n"):
                label, title = line.split("\n")[0].split(": ", 1)
                verdicts.append({"id": label, "score": scores.get(title, 0), "reason": "fit"})
            return {"verdicts": verdicts}
        if "last, strict filter" in system:
            return gate_all(user)
        return {"pulse": "A shared theme.", "papers": [{"id": "p1", "takeaway": "Key finding."}]}

    return answer


def papers_section(make_context, answer, **options) -> Papers:
    return Papers(make_context(answer), Papers.Config(**options))


def add_direction(settings, name="ttt", text="Test-time training for LLMs."):
    (settings.paths.directions / f"{name}.md").write_text(text)


def route_papers(web, *papers):
    web.route("export.arxiv.org", arxiv_feed(*papers))
    web.route("daily_papers", [])
    web.route("semanticscholar", lambda _r: httpx.Response(429))


def test_papers_flow_from_description_to_report(make_context, web, settings, store):
    add_direction(settings)
    route_papers(
        web,
        ("2610.00001", "Great paper"),
        ("2610.00002", "Good paper"),
        ("2610.00003", "Off topic"),
    )
    answer = paper_answers({"Great paper": 9, "Good paper": 8, "Off topic": 2})
    result = papers_section(make_context, answer).run(WINDOW)

    assert "### Test-time training" in result.markdown
    assert "A shared theme." in result.markdown and "Key finding." in result.markdown
    assert "Off topic" not in result.markdown
    assert "*3 new candidates, 2 relevant, 2 passed the quality gate.*" in result.markdown
    assert sorted(result.shown) == ["arxiv:2610.00001", "arxiv:2610.00002"]
    assert store.get_state("papers", "seeds:ttt") == ["ArXiv:2610.00001", "ArXiv:2610.00002"]


def test_quality_gate_caps_the_week_at_five_and_rejects_for_good(make_context, web, settings):
    add_direction(settings)
    papers = [(f"2610.0000{n}", f"Paper {n}") for n in range(1, 8)]
    route_papers(web, *papers)
    scores = {title: 9 for _, title in papers}
    plain = paper_answers(scores)

    def answer(role, system, user):
        if "last, strict filter" in system:
            decisions = gate_all(user)["decisions"]
            decisions[6]["keep"] = False
            return {"decisions": decisions}
        return plain(role, system, user)

    section = papers_section(make_context, answer)
    result = section.run(WINDOW)
    assert result.stats["shown"] == 5
    assert result.markdown.count("arxiv.org/abs") == 5
    assert len(result.shown) == 7  # the rejected two are not offered again next week
    gate_prompt = next(p for p in section.ctx.llm.prompts if "last, strict filter" in p[1])
    assert "Surveys, position papers" in gate_prompt[1]
    assert "no code link" in gate_prompt[2]


def test_quality_gate_may_keep_nothing(make_context, web, settings):
    add_direction(settings)
    route_papers(web, ("2610.00001", "Weak paper"))
    plain = paper_answers({"Weak paper": 9})

    def answer(role, system, user):
        if "last, strict filter" in system:
            return {"decisions": [{"id": "p1", "keep": False, "reason": "no evidence"}]}
        return plain(role, system, user)

    result = papers_section(make_context, answer).run(WINDOW)
    assert "Nothing passed the quality gate" in result.markdown
    assert "Weak paper" not in result.markdown
    assert result.shown == ["arxiv:2610.00001"]


def test_unavailable_quality_gate_fails_rather_than_passing_papers(make_context, web, settings):
    add_direction(settings)
    route_papers(web, ("2610.00001", "Paper"))
    plain = paper_answers({"Paper": 9})

    def answer(role, system, user):
        if "last, strict filter" in system:
            return "not json"
        return plain(role, system, user)

    with pytest.raises(LlmError):
        papers_section(make_context, answer).run(WINDOW)


def test_papers_treat_semantic_scholar_as_optional_and_stop_asking(make_context, web, settings):
    add_direction(settings, "one")
    add_direction(settings, "two", "Other direction.")
    route_papers(web, ("2610.00001", "Paper"))
    section = papers_section(make_context, paper_answers({"Paper": 8}))
    section.run(WINDOW)
    asks = [r for r in web.requests if "semanticscholar" in str(r.url)]
    assert len(asks) == S2_ATTEMPTS  # one call's patient attempts, then never again
    assert sum("Semantic Scholar" in w for w in section.warnings) == 1


def test_papers_plan_is_reused_until_the_description_changes(make_context, web, settings):
    add_direction(settings)
    route_papers(web, ("2610.00001", "Paper"))
    answer = paper_answers({})

    def plans(section):
        return sum("weekly literature tracker" in p[1] for p in section.ctx.llm.prompts)

    section = papers_section(make_context, answer)
    section.run(WINDOW)
    assert plans(section) == 1
    section = papers_section(make_context, answer)
    section.run(WINDOW)
    assert plans(section) == 0
    add_direction(settings, text="Test-time training, now including vision models.")
    section = papers_section(make_context, answer)
    section.run(WINDOW)
    assert plans(section) == 1


def test_papers_are_judged_once_per_description(make_context, web, settings):
    add_direction(settings)
    route_papers(web, ("2610.00001", "Paper"))
    answer = paper_answers({"Paper": 2})
    papers_section(make_context, answer).run(WINDOW)
    section = papers_section(make_context, answer)
    section.run(WINDOW)
    assert not any("screen new papers" in p[1] for p in section.ctx.llm.prompts)


def test_papers_already_shown_do_not_return(make_context, web, settings, store):
    add_direction(settings)
    route_papers(web, ("2610.00001", "Paper"))
    store.mark_shown("papers", ["arxiv:2610.00001"], "2026-W44")
    result = papers_section(make_context, paper_answers({"Paper": 9})).run(WINDOW)
    assert result.shown == []


def test_papers_shared_by_directions_appear_once_under_the_better_fit(make_context, web, settings):
    add_direction(settings, "aaa", "First direction.")
    add_direction(settings, "bbb", "Second direction.")
    route_papers(web, ("2610.00001", "Shared paper"))

    def answer(role, system, user):
        if "screen new papers" in system:
            score = 10 if "Second direction" in system else 8
            return {"verdicts": [{"id": "p1", "score": score, "reason": "fit"}]}
        if "weekly literature tracker" in system:
            title = "First" if "First" in user else "Second"
            return {**PLAN, "title": title}
        if "last, strict filter" in system:
            return gate_all(user)
        return {"pulse": "", "papers": []}

    result = papers_section(make_context, answer).run(WINDOW)
    first, second = result.markdown.split("### Second")
    assert "Shared paper" not in first
    assert "Shared paper" in second and "also: First" in second
    assert result.shown == ["arxiv:2610.00001"]


def test_near_tied_paper_goes_to_the_less_crowded_direction(make_context, web, settings):
    add_direction(settings, "crowded", "Crowded direction.")
    add_direction(settings, "narrow", "Narrow direction.")
    extra = [(f"2610.0010{n}", f"Crowded paper {n}") for n in range(4)]
    route_papers(web, ("2610.00001", "Shared paper"), *extra)

    def answer(role, system, user):
        if "weekly literature tracker" in system:
            return {**PLAN, "title": "Crowded" if "Crowded" in user else "Narrow"}
        if "screen new papers" in system:
            verdicts = []
            for block in user.split("\n\n"):
                label, title = block.split("\n")[0].split(": ", 1)
                if "Crowded direction" in system:
                    verdicts.append({"id": label, "score": 10 if "Shared" in title else 9})
                else:
                    verdicts.append({"id": label, "score": 9 if "Shared" in title else 0})
            return {"verdicts": verdicts}
        if "last, strict filter" in system:
            return gate_all(user)
        return {"pulse": "", "papers": []}

    result = papers_section(make_context, answer).run(WINDOW)
    crowded, narrow = result.markdown.split("### Narrow")
    assert "Shared paper" in narrow and "Shared paper" not in crowded


def test_news_links_name_the_destination_not_the_aggregator():
    story = Story("t", "https://www.cnbc.com/a", "", "Hacker News", NOW)
    assert _link_text(story) == "cnbc.com"
    assert _link_text(Story("t", "https://x.dev", "", "OpenAI", NOW)) == "OpenAI"


def test_candidate_cap_is_reported(make_context, web, settings):
    add_direction(settings)
    route_papers(web, ("2610.00001", "One"), ("2610.00002", "Two"))
    section = papers_section(make_context, paper_answers({}), max_candidates=1)
    section.run(WINDOW)
    assert any("too broad" in warning for warning in section.warnings)


def test_wrongly_shaped_news_reply_is_a_failure_not_a_quiet_week(make_context, web):
    web.route("lab.dev/feed", rss(("Real", "https://lab.dev/x")))
    web.route("api/models", [])
    feeds = [{"name": "Lab", "url": "https://lab.dev/feed"}]
    section = news_section(make_context, lambda *_: {"items": []}, feeds=feeds)
    with pytest.raises(LlmError):
        section.run(WINDOW)


def test_news_citing_only_invented_sources_fails(make_context, web):
    web.route("lab.dev/feed", rss(("Real", "https://lab.dev/x")))
    web.route("api/models", [])
    invented = {
        "stories": [{"category": "c", "headline": "h", "summary": "s", "sources": ["OpenAI"]}]
    }
    feeds = [{"name": "Lab", "url": "https://lab.dev/feed"}]
    section = news_section(make_context, lambda *_: invented, feeds=feeds)
    with pytest.raises(SectionError):
        section.run(WINDOW)


def test_web_dates_without_a_timezone_are_treated_as_utc(make_context, web):
    web.route("api/models", [])
    result = {
        "title": "T",
        "url": "https://a.dev/t",
        "content": "c",
        "published_date": "Sat, 31 Oct 2026 10:00:00",
    }
    undated = {"title": "U", "url": "https://a.dev/u", "content": "c"}
    web.route("tavily.com", {"results": [result, undated]})
    stories = news.web_search(Http(web), "key", "q", 7, 5, NOW)
    assert WINDOW.contains(stories[0].published_at)
    assert stories[1].published_at == NOW


def test_papers_fail_when_arxiv_is_down_even_if_daily_papers_answer(make_context, web, settings):
    add_direction(settings)
    web.route("export.arxiv.org", lambda _r: httpx.Response(503))
    web.route("daily_papers", [])
    web.route("semanticscholar", lambda _r: httpx.Response(429))
    with pytest.raises(SectionError, match="arXiv"):
        papers_section(make_context, paper_answers({})).run(WINDOW)


def test_papers_fail_when_too_many_judge_batches_fail(make_context, web, settings):
    add_direction(settings)
    route_papers(web, ("2610.00001", "One"))

    def answer(role, system, user):
        if "weekly literature tracker" in system:
            return PLAN
        return "not json"

    with pytest.raises(SectionError, match="judge"):
        papers_section(make_context, answer).run(WINDOW)


def test_papers_use_daily_list_for_matches_and_signals(make_context, web, settings):
    add_direction(settings)
    route_papers(web, ("2610.00001", "From arXiv"))
    daily = [
        {
            "paper": {
                "id": "2610.00001",
                "title": "From arXiv",
                "summary": "x",
                "upvotes": 40,
                "githubRepo": "https://github.com/a/b",
                "publishedAt": "2026-10-30T00:00:00Z",
            }
        },
        {
            "paper": {
                "id": "2610.00002",
                "title": "Online adaptation of agents",
                "summary": "x",
                "publishedAt": "2026-10-30T00:00:00Z",
            }
        },
        {
            "paper": {
                "id": "2610.00003",
                "title": "Unrelated",
                "summary": "x",
                "publishedAt": "2026-10-30T00:00:00Z",
            }
        },
    ]
    web.route(
        "daily_papers",
        lambda r: httpx.Response(200, json=daily if "2026-10-30" in str(r.url) else []),
    )
    scores = {"From arXiv": 9, "Online adaptation of agents": 8, "Unrelated": 10}
    result = papers_section(make_context, paper_answers(scores)).run(WINDOW)
    assert "▲40" in result.markdown and "[code](https://github.com/a/b)" in result.markdown
    assert "Online adaptation of agents" in result.markdown
    assert "Unrelated" not in result.markdown


def test_papers_fail_when_nothing_can_be_retrieved(make_context, web, settings):
    add_direction(settings)
    web.route("export.arxiv.org", lambda _r: httpx.Response(503))
    web.route("daily_papers", lambda _r: httpx.Response(503))
    web.route("semanticscholar", lambda _r: httpx.Response(429))
    with pytest.raises(SectionError):
        papers_section(make_context, paper_answers({})).run(WINDOW)


def test_papers_without_directions_explain_what_to_do(make_context):
    result = papers_section(make_context, paper_answers({})).run(WINDOW)
    assert "rec direction add" in result.markdown


def test_plan_matching_ignores_word_order_and_case():
    plan = SearchPlan.model_validate(PLAN)
    assert plan.matches("Training at test-time", "")
    assert plan.matches("x", "we study TTT here")
    assert not plan.matches("Reinforcement learning", "nothing relevant")


def test_plan_rejects_malformed_categories():
    plan = SearchPlan.model_validate({**PLAN, "categories": ["cs.LG", "bad; drop"]})
    assert plan.categories == ["cs.LG"]


def test_directions_can_be_paused_edited_and_removed(tmp_path):
    save_direction(tmp_path, "one", "First text.")
    set_paused(tmp_path, "one", True)
    assert load_directions(tmp_path) == []
    [paused] = load_directions(tmp_path, include_paused=True)
    assert paused.paused and paused.id == "one"

    save_direction(tmp_path, "one", "New text.")
    assert (tmp_path / "_one.md").read_text() == "New text.\n"
    set_paused(tmp_path, "one", False)
    assert unique_id(tmp_path, "One") == "one-2"

    remove_direction(tmp_path, "one")
    assert load_directions(tmp_path, include_paused=True) == []
    assert (tmp_path / ".removed" / "one.md").exists()


def test_direction_files(tmp_path):
    (tmp_path / "live.md").write_text("  Something real.\n")
    (tmp_path / "_paused.md").write_text("Skip me.")
    (tmp_path / "empty.md").write_text("   ")
    assert [d.id for d in load_directions(tmp_path)] == ["live"]
    assert (
        Direction("a", "one  two\nthree").fingerprint == Direction("a", "one two three").fingerprint
    )
    assert slugify("Test-Time Training: Agents!") == "test-time-training-agents"


def test_preview_searches_like_a_run_with_daily_papers_and_lookback(make_context, web, settings):
    add_direction(settings)
    route_papers(web, ("2610.00001", "From arXiv"))
    only_daily = {
        "paper": {
            "id": "2610.00002",
            "title": "Online adaptation of agents",
            "summary": "x",
            "publishedAt": "2026-10-30T00:00:00Z",
        }
    }
    web.route(
        "daily_papers",
        lambda r: httpx.Response(200, json=[only_daily] if "2026-10-30" in str(r.url) else []),
    )
    section = papers_section(make_context, paper_answers({"Online adaptation of agents": 9}))
    [direction] = load_directions(settings.paths.directions)
    scan = section.preview(direction, WINDOW)
    assert [item.paper.title for item in scan.accepted] == ["Online adaptation of agents"]
    days = [r.url.params["date"] for r in web.requests if "daily_papers" in str(r.url)]
    widened = WINDOW.since - timedelta(days=section.config.lookback_days)
    assert min(days) == widened.date().isoformat()
    arxiv = next(r for r in web.requests if "export.arxiv.org" in str(r.url))
    assert f"submittedDate:[{widened:%Y%m%d%H%M}" in arxiv.url.params["search_query"]


# ---- concurrency ----


class _Overlap:
    """A slow handler that records how many requests were in flight at once."""

    def __init__(self, reply, delay: float = 0.03):
        self.reply, self.delay = reply, delay
        self.now = self.peak = 0
        self.lock = threading.Lock()

    def __call__(self, request):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        time.sleep(self.delay)
        with self.lock:
            self.now -= 1
        return self.reply(request)


def test_gather_all_keeps_order_and_counts_every_failure(make_context):
    section = news_section(make_context, lambda *_: {})

    def job(n):
        def fetch():
            time.sleep(0.01 * (5 - n % 5))
            if n % 3 == 0:
                raise RuntimeError(f"down {n}")
            return [n]

        return (f"source {n}", fetch)

    found = section.gather_all([job(n) for n in range(12)], [])
    assert found == [[] if n % 3 == 0 else [n] for n in range(12)]
    assert section._failed_sources == 4 and section._ok_sources == 8
    assert sorted(section.warnings) == sorted(
        f"source {n} failed: RuntimeError: down {n}" for n in range(0, 12, 3)
    )


def test_news_sources_are_fetched_concurrently_in_config_order(make_context, web):
    def feed(request):
        name = request.url.host.split(".")[0]
        return httpx.Response(200, text=rss((f"Story {name}", f"https://{name}.dev/1")))

    slow = _Overlap(feed)
    web.route("/feed", slow)
    web.route("api/models", [])
    feeds = [{"name": f"F{n}", "url": f"https://f{n}.dev/feed"} for n in range(12)]
    section = news_section(make_context, lambda *_: {}, feeds=feeds)
    stories = section._collect(WINDOW)
    assert [story.source for story in stories] == [f"F{n}" for n in range(12)]
    assert 1 < slow.peak <= 6


def test_daily_papers_fetch_days_concurrently_in_order(web):
    def day(request):
        date = request.url.params["date"]
        return httpx.Response(200, json=[{"paper": {"id": date, "title": date}}])

    slow = _Overlap(day)
    web.route("daily_papers", slow)
    papers = hf_daily_papers(Http(web), NOW - timedelta(days=9), NOW)
    expected = [(NOW - timedelta(days=9 - n)).date().isoformat() for n in range(10)]
    assert [paper.title for paper in papers] == expected
    assert 1 < slow.peak <= 6
