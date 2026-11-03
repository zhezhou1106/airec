"""Paper sources: arXiv search, Semantic Scholar, Hugging Face daily papers.

arXiv is the backbone. Semantic Scholar adds conference papers and
recommendations from papers already accepted. It works without a key, but the
shared pool often refuses, so sections treat it as a bonus.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import feedparser

from airec.http import Http
from airec.sources.common import GITHUB_URL, clean_text, from_struct, parse_iso

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PAUSE_SECONDS = 3.0  # arXiv asks for one request every three seconds
S2_API = "https://api.semanticscholar.org"
S2_ATTEMPTS = 6  # the keyless pool is shared and refuses often; waiting usually works
S2_FIELDS = "title,externalIds,abstract,publicationDate,citationCount,venue,authors,url"
HF_DAILY = "https://huggingface.co/api/daily_papers"
HF_WORKERS = 6  # days fetched at once
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})")


@dataclass
class Paper:
    id: str  # arxiv:<id>, or s2:<id> for papers not on arXiv
    title: str
    abstract: str
    url: str
    published_at: datetime
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    code_url: str = ""
    upvotes: int = 0
    found_by: list[str] = field(default_factory=list)

    @property
    def arxiv_id(self) -> str | None:
        return self.id.removeprefix("arxiv:") if self.id.startswith("arxiv:") else None

    def absorb(self, other: Paper) -> None:
        """Merge what another source knows about the same paper."""
        self.found_by = list(dict.fromkeys(self.found_by + other.found_by))
        self.venue = self.venue or other.venue
        self.code_url = self.code_url or other.code_url
        self.upvotes = max(self.upvotes, other.upvotes)
        if len(other.abstract) > len(self.abstract):
            self.abstract = other.abstract


def _code_link(text: str) -> str:
    match = GITHUB_URL.search(text)
    return match.group(0).rstrip(".") if match else ""


def _arxiv_stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%d%H%M")


def arxiv_query(phrases: list[str], categories: list[str], since: datetime, until: datetime) -> str:
    terms = []
    for phrase in phrases:
        quoted = phrase.replace('"', "")
        terms += [f'ti:"{quoted}"', f'abs:"{quoted}"']
    query = f"({' OR '.join(terms)})"
    if categories:
        query += f" AND ({' OR '.join(f'cat:{c}' for c in categories)})"
    return f"{query} AND submittedDate:[{_arxiv_stamp(since)} TO {_arxiv_stamp(until)}]"


def parse_arxiv(feed_xml: bytes | str) -> list[Paper]:
    papers = []
    for entry in feedparser.parse(feed_xml).entries:
        match = _ARXIV_ID.search(entry.get("id", ""))
        published = from_struct(entry.get("published_parsed"))
        if match is None or published is None:
            continue
        abstract = clean_text(entry.get("summary"))
        papers.append(
            Paper(
                id=f"arxiv:{match.group(1)}",
                title=clean_text(entry.get("title")),
                abstract=abstract,
                url=f"https://arxiv.org/abs/{match.group(1)}",
                published_at=published,
                authors=[a.get("name", "") for a in entry.get("authors", [])],
                code_url=_code_link(abstract),
                found_by=["arxiv"],
            )
        )
    return papers


class Arxiv:
    def __init__(self, http: Http):
        self.http = http
        self._last_call = 0.0

    def search(
        self,
        phrases: list[str],
        categories: list[str],
        since: datetime,
        until: datetime,
        limit: int = 100,
    ) -> list[Paper]:
        wait = self._last_call + ARXIV_PAUSE_SECONDS - time.monotonic()
        if self._last_call and wait > 0:
            self.http.sleep(wait)
        params = {
            "search_query": arxiv_query(phrases, categories, since, until),
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            response = self.http.get(ARXIV_API, params=params, timeout=60)
        finally:
            self._last_call = time.monotonic()
        return parse_arxiv(response.content)


def _s2_paper(data: dict, found_by: str) -> Paper | None:
    title = clean_text(data.get("title"))
    published = parse_iso(data.get("publicationDate"))
    if not title or published is None:
        return None
    arxiv_id = (data.get("externalIds") or {}).get("ArXiv")
    abstract = clean_text(data.get("abstract"))
    return Paper(
        id=f"arxiv:{arxiv_id}" if arxiv_id else f"s2:{data.get('paperId', '')}",
        title=title,
        abstract=abstract,
        url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else data.get("url", ""),
        published_at=published,
        authors=[a.get("name", "") for a in data.get("authors") or []],
        venue=data.get("venue") or "",
        code_url=_code_link(abstract),
        found_by=[found_by],
    )


class SemanticScholar:
    def __init__(self, http: Http, api_key: str = ""):
        self.http = http
        self.headers = {"x-api-key": api_key} if api_key else {}

    def search(self, phrases: list[str], since: datetime, until: datetime) -> list[Paper]:
        query = " | ".join(f'"{phrase.replace(chr(34), "")}"' for phrase in phrases)
        response = self.http.get(
            f"{S2_API}/graph/v1/paper/search/bulk",
            params={
                "query": query,
                "fields": S2_FIELDS,
                "publicationDateOrYear": f"{since:%Y-%m-%d}:{until:%Y-%m-%d}",
                "sort": "publicationDate:desc",
            },
            headers=self.headers,
            timeout=60,
            attempts=S2_ATTEMPTS,
        )
        found = (_s2_paper(item, "semantic_scholar") for item in response.json().get("data") or [])
        return [paper for paper in found if paper]

    def recommend(self, seeds: list[str], limit: int = 100) -> list[Paper]:
        """Papers similar to `seeds`, given as ids such as "ArXiv:2610.12345"."""
        response = self.http.post(
            f"{S2_API}/recommendations/v1/papers",
            params={"limit": limit, "fields": S2_FIELDS},
            json={"positivePaperIds": seeds},
            headers=self.headers,
            timeout=60,
            attempts=S2_ATTEMPTS,
        )
        found = (
            _s2_paper(item, "recommendations")
            for item in response.json().get("recommendedPapers") or []
        )
        return [paper for paper in found if paper]


def hf_daily_papers(http: Http, since: datetime, until: datetime) -> list[Paper]:
    """Every paper featured on Hugging Face in the window, with upvotes and code links.

    Days are fetched a few at a time; the result keeps day order. One failing day
    fails the whole list, as before: a source answers fully or not at all.
    """
    days = [since.date() + timedelta(days=n) for n in range((until.date() - since.date()).days + 1)]

    def fetch(day: date) -> list[dict]:
        return http.get(HF_DAILY, params={"date": day.isoformat(), "limit": 100}).json()

    with ThreadPoolExecutor(max(1, min(HF_WORKERS, len(days)))) as pool:
        entries = [entry for found in pool.map(fetch, days) for entry in found]
    papers = []
    for entry in entries:
        paper = entry.get("paper") or {}
        arxiv_id = paper.get("id")
        if not arxiv_id:
            continue
        abstract = clean_text(paper.get("summary"))
        papers.append(
            Paper(
                id=f"arxiv:{arxiv_id}",
                title=clean_text(paper.get("title")),
                abstract=abstract,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                published_at=parse_iso(paper.get("publishedAt")) or since,
                authors=[a.get("name", "") for a in paper.get("authors") or []],
                code_url=paper.get("githubRepo") or _code_link(abstract),
                upvotes=int(paper.get("upvotes") or 0),
                found_by=["hf_daily"],
            )
        )
    return papers
