"""GitHub: repository search (knowledge) and release watching (news)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sources.base import Item, Source, canonical_id, iso_epoch

API = "https://api.github.com"


def _headers(token: str) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def repo_item(repo: dict[str, Any], source: str) -> Item:
    created = iso_epoch(repo.get("created_at")) or time.time()
    age_days = max((time.time() - created) / 86400.0, 1.0)
    stars = int(repo.get("stargazers_count") or 0)
    topics = ", ".join(repo.get("topics") or [])
    return {
        "id": f"gh:{repo.get('full_name', '').lower()}",
        "kind": "repo",
        "source": source,
        "url": repo.get("html_url", ""),
        "title": repo.get("full_name", ""),
        "abstract": (repo.get("description") or "") + (f"\nTopics: {topics}" if topics else ""),
        "authors": (repo.get("owner") or {}).get("login", ""),
        "published_at": iso_epoch(repo.get("pushed_at")) or created,
        "signals": {
            "stars": stars,
            "stars_per_day": round(stars / age_days, 1),
            "age_days": round(age_days),
            "language": repo.get("language") or "",
            "code_url": repo.get("html_url", ""),
        },
    }


class GithubSearch(Source):
    """Young repos (created within max_age_days) that match a query, plus new repos gaining stars fast."""
    name = "github"
    track = "knowledge"

    def fetch(self, since: float, until: float) -> list[Item]:
        token = self.ctx.cfg.secret("GITHUB_TOKEN")
        day = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%d")
        # Only young repos: established ones reach you through watched releases (news).
        max_age = int(self.conf.get("max_age_days", 90))
        born = datetime.fromtimestamp(since - max_age * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
        min_stars = int(self.conf.get("min_stars", 50))
        queries = list(self.conf.get("queries") or []) + self.ctx.topic_queries("github")
        searches = [f"{q} created:>={born} pushed:>={day} stars:>={min_stars}"
                    for q in dict.fromkeys(queries)]
        new_min = int(self.conf.get("new_repo_min_stars", 100))
        if new_min:
            searches.append(f"created:>={day} stars:>={new_min}")
        items: dict[str, Item] = {}
        for q in searches:
            try:
                resp = self.http.get(f"{API}/search/repositories",
                                     params={"q": q, "sort": "stars", "order": "desc",
                                             "per_page": int(self.conf.get("per_query", 20))},
                                     headers=_headers(token))
                if resp.status_code == 403:
                    self.warn("rate limited; add GITHUB_TOKEN in Settings → Keys")
                    break
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                self.warn(f"'{q}': {exc}")
                continue
            for repo in resp.json().get("items", []):
                it = repo_item(repo, self.name)
                items.setdefault(it["id"], it)
            time.sleep(0.5 if token else 6)  # the search API allows 10/min without a token
        self.log(f"  github: {len(items)} repos from {len(searches)} searches")
        return list(items.values())


class GithubReleases(Source):
    """New releases of repos you watch."""
    name = "github_releases"
    track = "news"

    def fetch(self, since: float, until: float) -> list[Item]:
        token = self.ctx.cfg.secret("GITHUB_TOKEN")
        repos = list(dict.fromkeys(list(self.conf.get("watch") or [])
                                   + self.ctx.topic_watch("repos")))
        items: list[Item] = []
        for full_name in repos:
            try:
                resp = self.http.get(f"{API}/repos/{full_name}/releases",
                                     params={"per_page": 5}, headers=_headers(token))
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                self.warn(f"{full_name}: {exc}")
                continue
            for rel in resp.json():
                published = iso_epoch(rel.get("published_at"))
                if not published or not since <= published <= until or rel.get("prerelease"):
                    continue
                url = rel.get("html_url", "")
                items.append({
                    "id": canonical_id(url),
                    "kind": "news",
                    "source": self.name,
                    "url": url,
                    "title": f"{full_name} {rel.get('name') or rel.get('tag_name') or ''}".strip(),
                    "abstract": (rel.get("body") or "")[:3000],
                    "authors": full_name.split("/")[0],
                    "published_at": published,
                    "signals": {"release_of": full_name},
                })
        self.log(f"  github_releases: {len(items)} releases from {len(repos)} watched repos")
        return items
