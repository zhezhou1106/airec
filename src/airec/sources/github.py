"""GitHub: the trending page, repository search, READMEs and releases."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from airec.http import Http
from airec.sources.common import clean_text, parse_iso

API = "https://api.github.com"
TRENDING = "https://github.com/trending"


@dataclass
class Repo:
    full_name: str
    description: str = ""
    language: str = ""
    stars: int = 0
    stars_gained: int | None = None  # as reported by the trending page, for the period
    created_at: datetime | None = None
    topics: list[str] = field(default_factory=list)
    archived: bool = False
    fork: bool = False
    readme: str = ""

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}"

    @property
    def id(self) -> str:
        return self.full_name.lower()


@dataclass
class Release:
    repo: str
    name: str
    url: str
    body: str
    published_at: datetime


def _count(text: str) -> int:
    digits = "".join(ch for ch in text.split(" ")[0] if ch.isdigit())
    return int(digits) if digits else 0


def parse_trending(html: str) -> list[Repo]:
    repos = []
    for article in BeautifulSoup(html, "lxml").select("article.Box-row"):
        link = article.select_one("h2 a")
        if link is None or not link.get("href"):
            continue
        description = article.select_one("p")
        language = article.select_one('[itemprop="programmingLanguage"]')
        stars = article.select_one('a[href$="/stargazers"]')
        gained = article.select_one("span.float-sm-right")
        repos.append(
            Repo(
                full_name=str(link["href"]).strip("/"),
                description=clean_text(description.get_text()) if description else "",
                language=clean_text(language.get_text()) if language else "",
                stars=_count(stars.get_text().strip()) if stars else 0,
                stars_gained=_count(gained.get_text().strip()) if gained else None,
            )
        )
    return repos


def _repo_from_api(data: dict) -> Repo:
    return Repo(
        full_name=data["full_name"],
        description=data.get("description") or "",
        language=data.get("language") or "",
        stars=int(data.get("stargazers_count") or 0),
        created_at=parse_iso(data.get("created_at")),
        topics=list(data.get("topics") or []),
        archived=bool(data.get("archived")),
        fork=bool(data.get("fork")),
    )


class GitHub:
    def __init__(self, http: Http, token: str = ""):
        self.http = http
        self.headers = {"Accept": "application/vnd.github+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def trending(self, language: str = "", since: str = "weekly") -> list[Repo]:
        url = f"{TRENDING}/{language}" if language else TRENDING
        return parse_trending(self.http.get(url, params={"since": since}).text)

    def search(self, query: str, per_page: int = 20) -> list[Repo]:
        response = self.http.get(
            f"{API}/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
            headers=self.headers,
        )
        return [_repo_from_api(item) for item in response.json().get("items", [])]

    def repo(self, full_name: str) -> Repo:
        response = self.http.get(f"{API}/repos/{full_name}", headers=self.headers)
        return _repo_from_api(response.json())

    def readme(self, full_name: str) -> str:
        """Decoded README text, or "" for a repo without one."""
        try:
            response = self.http.get(f"{API}/repos/{full_name}/readme", headers=self.headers)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return ""
            raise
        payload = response.json().get("content") or ""
        return base64.b64decode(payload).decode("utf-8", "replace")

    def releases(self, full_name: str, per_page: int = 5) -> list[Release]:
        response = self.http.get(
            f"{API}/repos/{full_name}/releases",
            params={"per_page": per_page},
            headers=self.headers,
        )
        releases = []
        for item in response.json():
            published = parse_iso(item.get("published_at"))
            if published is None or item.get("prerelease") or item.get("draft"):
                continue
            name = item.get("name") or item.get("tag_name") or ""
            releases.append(
                Release(
                    full_name, name, item.get("html_url", ""), item.get("body") or "", published
                )
            )
        return releases
