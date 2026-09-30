"""Web search for the map editor and the web-news source, with fallbacks.

Backends are tried in the order of settings.yaml `search.order` (default
tavily → brave → duckduckgo). A backend is skipped when its key is missing, and
once it fails (quota used up, outage) it is skipped for the rest of the job.
DuckDuckGo needs no key but is unofficial and rate-limits without warning, so
it is the last resort.
"""
from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from config import USER_AGENT, Config

KEY = "TAVILY_API_KEY"
BRAVE_KEY = "BRAVE_API_KEY"
KEYS = {"tavily": KEY, "brave": BRAVE_KEY, "duckduckgo": ""}
DEFAULT_ORDER = ["tavily", "brave", "duckduckgo"]

Result = dict[str, Any]


class SearchError(RuntimeError):
    pass


def _fresh(days: int | None, codes: tuple[str, str, str, str]) -> str:
    """Pick a provider's freshness code for day / week / month / year."""
    if not days:
        return ""
    for limit, code in zip((1, 7, 31, 366), codes):
        if days <= limit:
            return code
    return ""


class WebSearch:
    def __init__(self, cfg: Config, log: Callable[[str], None] | None = None):
        order = (cfg.settings.get("search") or {}).get("order") or DEFAULT_ORDER
        self.keys = {name: cfg.secret(KEYS[name]) for name in order if name in KEYS}
        self.order = [n for n in order if n in KEYS and (n == "duckduckgo" or self.keys[n])]
        self.failed: set[str] = set()
        self.log = log or (lambda _m: None)
        self.client = httpx.Client(timeout=45, follow_redirects=True,
                                   headers={"User-Agent": USER_AGENT})

    @property
    def available(self) -> bool:
        return any(n not in self.failed for n in self.order)

    @property
    def engines(self) -> list[str]:
        return [n for n in self.order if n not in self.failed]

    def search(self, query: str, *, max_results: int = 6, news: bool = False,
               days: int | None = None) -> list[Result]:
        """[{title, url, content, published, engine}] from the first backend that answers."""
        if not self.order:
            raise SearchError("no search backend: add TAVILY_API_KEY or BRAVE_API_KEY in "
                              "Settings → Keys, or allow duckduckgo in settings.yaml search.order")
        errors = []
        for name in self.engines:
            try:
                results = getattr(self, f"_{name}")(query, max_results, news, days)
            except Exception as exc:  # noqa: BLE001 - fall through to the next backend
                self.failed.add(name)
                errors.append(f"{name}: {exc}")
                nxt = self.engines
                self.log(f"[warn] {name} search failed ({str(exc)[:120]})"
                         + (f"; falling back to {nxt[0]}" if nxt else ""))
                continue
            for r in results:
                r["engine"] = name
            return results[:max_results]
        raise SearchError("every search backend failed: " + "; ".join(errors))

    # ---------------------------------------------------------------- backends

    def _tavily(self, query: str, n: int, news: bool, days: int | None) -> list[Result]:
        body: dict[str, Any] = {"query": query, "max_results": n, "search_depth": "basic",
                                "topic": "news" if news else "general"}
        if days:
            body["days"] = int(days)
        resp = self.client.post("https://api.tavily.com/search", json=body,
                                headers={"Authorization": f"Bearer {self.keys['tavily']}"})
        if resp.status_code >= 400:
            raise SearchError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "content": (r.get("content") or "")[:1200],
                 "published": r.get("published_date") or ""}
                for r in resp.json().get("results", [])]

    def _brave(self, query: str, n: int, news: bool, days: int | None) -> list[Result]:
        kind = "news" if news else "web"
        params: dict[str, Any] = {"q": query, "count": min(n, 20)}
        fresh = _fresh(days, ("pd", "pw", "pm", "py"))
        if fresh:
            params["freshness"] = fresh
        resp = self.client.get(f"https://api.search.brave.com/res/v1/{kind}/search",
                               params=params,
                               headers={"X-Subscription-Token": self.keys["brave"],
                                        "Accept": "application/json"})
        if resp.status_code >= 400:
            raise SearchError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        data = resp.json()
        rows = data.get("results", []) if news else (data.get("web") or {}).get("results", [])
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "content": _strip_tags(r.get("description") or "")[:1200],
                 "published": r.get("page_age") or ""}
                for r in rows]

    def _duckduckgo(self, query: str, n: int, news: bool, days: int | None) -> list[Result]:
        from bs4 import BeautifulSoup

        params = {"q": query}
        fresh = _fresh(days, ("d", "w", "m", "y"))
        if fresh:
            params["df"] = fresh
        resp = self.client.post("https://html.duckduckgo.com/html/", data=params,
                                headers={"User-Agent": "Mozilla/5.0 (Macintosh) airec"})
        if resp.status_code >= 400:
            raise SearchError(f"HTTP {resp.status_code}")
        soup = BeautifulSoup(resp.text, "lxml")
        if soup.find(class_="anomaly-modal__title") or "challenge" in resp.url.path:
            raise SearchError("DuckDuckGo asked for a captcha (rate limited)")
        out = []
        for block in soup.select(".result"):
            link = block.select_one("a.result__a")
            if not link or "result--ad" in (block.get("class") or []):
                continue
            out.append({"title": link.get_text(" ", strip=True),
                        "url": _ddg_target(link.get("href", "")),
                        "content": (block.select_one(".result__snippet") or link)
                        .get_text(" ", strip=True)[:1200],
                        "published": ""})
            if len(out) >= n:
                break
        return out


def _ddg_target(href: str) -> str:
    """DuckDuckGo wraps links as //duckduckgo.com/l/?uddg=<real url>."""
    q = parse_qs(urlparse(href).query)
    return unquote(q["uddg"][0]) if "uddg" in q else href


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)
