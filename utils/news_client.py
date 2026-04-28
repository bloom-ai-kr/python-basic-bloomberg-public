from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
import re
from typing import Any
from xml.etree import ElementTree as ET

try:
    import requests
except Exception:  # pragma: no cover - optional dependency/runtime
    requests = None

RSS_FEEDS = (
    {
        "url": "https://www.yna.co.kr/rss/news.xml",
        "default_name": "연합뉴스 최신기사",
    },
    {
        "url": "https://www.yna.co.kr/rss/economy.xml",
        "default_name": "연합뉴스 경제 최신기사",
    },
)
REQUEST_HEADERS = {
    "User-Agent": "python-basic-bloomberg-public/1.0",
}

HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = unescape(text)
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def _parse_published_at(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
            return parsed.replace(tzinfo=None)
        return parsed
    except Exception:
        return datetime.now()


def _build_keywords(company_name: str, symbol: str) -> list[str]:
    keywords: list[str] = []
    clean_company_name = _clean_text(company_name)
    clean_symbol = _clean_text(symbol)

    if clean_company_name and clean_company_name != clean_symbol:
        if not clean_company_name.endswith(" 종목"):
            keywords.append(clean_company_name.lower())
            keywords.append(clean_company_name.replace(" ", "").lower())

    if clean_symbol:
        keywords.append(f"[{clean_symbol}]".lower())
        keywords.append(clean_symbol.lower())

    deduped: list[str] = []
    for keyword in keywords:
        if keyword and keyword not in deduped:
            deduped.append(keyword)
    return deduped


def _matches_company(title: str, description: str, keywords: list[str], symbol: str) -> bool:
    if not keywords:
        return False

    haystack = f"{title}\n{description}".lower()
    compact_haystack = haystack.replace(" ", "")
    clean_symbol = _clean_text(symbol)

    for keyword in keywords:
        if keyword.startswith("[") and keyword.endswith("]"):
            if keyword in haystack:
                return True
            continue

        if clean_symbol and keyword == clean_symbol.lower():
            if f"[{clean_symbol.lower()}]" in haystack:
                return True
            continue

        if keyword in haystack or keyword in compact_haystack:
            return True

    return False


def fetch_company_news(
    *,
    company_name: str,
    symbol: str = "",
    max_items: int = 5,
    days: int = 7,
) -> list[dict[str, Any]]:
    if requests is None:
        raise RuntimeError("requests 패키지가 없어 뉴스 RSS를 불러올 수 없습니다.")

    keywords = _build_keywords(company_name, symbol)
    if not keywords:
        return []

    cutoff = datetime.now() - timedelta(days=max(days, 1))
    articles: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    for feed in RSS_FEEDS:
        response = requests.get(
            feed["url"],
            headers=REQUEST_HEADERS,
            timeout=15,
        )
        response.raise_for_status()

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise RuntimeError("연합뉴스 RSS 응답을 해석할 수 없습니다.") from exc

        items = root.findall("./channel/item")
        if not items:
            continue

        source_name = _clean_text(root.findtext("./channel/title")) or str(feed["default_name"])

        for item in items:
            title = _clean_text(item.findtext("title"))
            description = _clean_text(item.findtext("description"))
            link = _clean_text(item.findtext("link"))
            published_at = _parse_published_at(_clean_text(item.findtext("pubDate")))

            if published_at < cutoff:
                continue

            if not _matches_company(title, description, keywords, symbol):
                continue

            dedupe_key = link or f"{published_at.isoformat()}::{title}"
            if dedupe_key in seen_links:
                continue
            seen_links.add(dedupe_key)

            articles.append(
                {
                    "title": title or "제목 없음",
                    "link": link,
                    "source_name": source_name,
                    "source_url": str(feed["url"]),
                    "published_at": published_at.strftime("%Y-%m-%d %H:%M"),
                    "description": description,
                }
            )

    articles.sort(key=lambda article: str(article.get("published_at") or ""), reverse=True)
    return articles[: max(max_items, 1)]
