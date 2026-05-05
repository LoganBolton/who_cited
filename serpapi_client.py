"""Optional SerpApi path: paid proxy that handles GS CAPTCHAs reliably."""

from __future__ import annotations

import concurrent.futures
import functools
import os
import re
import time
from urllib.parse import parse_qs, urlparse

import requests

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()
SERPAPI_URL = "https://serpapi.com/search.json"
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def is_enabled() -> bool:
    return bool(SERPAPI_KEY)


def _summary_to_year_venue(summary: str) -> tuple[int | None, str]:
    ymatch = _YEAR_RE.search(summary)
    year = int(ymatch.group(0)) if ymatch else None
    venue = re.sub(r"^[^-]*-\s*", "", summary)
    venue = _YEAR_RE.sub("", venue, count=1).strip(" ,-")
    return year, venue


def _author_id(author: dict) -> str:
    """Extract a Google Scholar profile id from SerpApi author metadata."""
    for key in ("serpapi_scholar_link", "link"):
        value = (author.get(key) or "").strip()
        if not value:
            continue
        user = parse_qs(urlparse(value).query).get("user")
        if user and user[0]:
            return user[0]
        author_id = parse_qs(urlparse(value).query).get("author_id")
        if author_id and author_id[0]:
            return author_id[0]
    return ""


def _shape_author(author: dict) -> dict:
    name = (author.get("name") or "").strip()
    shaped = {"name": name, "affiliations": []}
    scholar_id = _author_id(author)
    if scholar_id:
        shaped["scholar_author_id"] = scholar_id
    return shaped


@functools.lru_cache(maxsize=2048)
def fetch_author_affiliation(author_id: str, *, timeout: float = 15.0) -> list[str]:
    """Fetch the affiliation string from a Google Scholar author profile."""
    if not is_enabled() or not author_id:
        return []

    params = {
        "engine": "google_scholar_author",
        "author_id": author_id,
        "hl": "en",
        "api_key": SERPAPI_KEY,
    }
    try:
        r = requests.get(SERPAPI_URL, params=params, timeout=timeout)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    try:
        data = r.json()
    except ValueError:
        return []

    affiliation = ((data.get("author") or {}).get("affiliations") or "").strip()
    return [affiliation] if affiliation else []


def enrich_author_profiles(
    citations: list[dict],
    *,
    max_profiles: int = 100,
    max_workers: int = 8,
    overall_timeout: float = 45.0,
) -> list[dict]:
    """Fill blank affiliations from linked Google Scholar author profiles."""
    if not is_enabled() or not citations:
        return citations

    authors_by_id: dict[str, list[dict]] = {}
    for citation in citations:
        for author in citation.get("authors") or []:
            author_id = (author.get("scholar_author_id") or "").strip()
            if not author_id or author.get("affiliations"):
                continue
            if author_id not in authors_by_id and len(authors_by_id) >= max_profiles:
                break
            authors_by_id.setdefault(author_id, []).append(author)
        if len(authors_by_id) >= max_profiles:
            break

    deadline = time.monotonic() + overall_timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(fetch_author_affiliation, author_id): author_id
            for author_id in authors_by_id
        }
        for fut in concurrent.futures.as_completed(futures):
            if time.monotonic() > deadline:
                break
            author_id = futures[fut]
            try:
                affs = fut.result()
            except Exception:
                continue
            if affs:
                for author in authors_by_id[author_id]:
                    author["affiliations"] = affs
    return citations


def fetch_cites(cites_ids: list[str], *, max_results: int = 500) -> dict | None:
    """Fetch citing papers via SerpApi. Returns None if SERPAPI_KEY isn't set."""
    if not is_enabled():
        return None

    cites_param = ",".join(cites_ids)
    papers: list[dict] = []
    seen: set[str] = set()
    total: int | None = None
    start, page_size = 0, 20

    while start < max_results:
        params = {
            "engine": "google_scholar",
            "q": "",
            "cites": cites_param,
            "hl": "en",
            "as_sdt": "5",
            "start": str(start),
            "num": str(page_size),
            "api_key": SERPAPI_KEY,
        }
        try:
            r = requests.get(SERPAPI_URL, params=params, timeout=30)
        except requests.RequestException:
            break
        if r.status_code != 200:
            break

        data = r.json()
        if total is None:
            total = (data.get("search_information") or {}).get("total_results")

        results = data.get("organic_results") or []
        if not results:
            break

        new_added = 0
        for entry in results:
            title = (entry.get("title") or "").strip()
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())

            pub = entry.get("publication_info") or {}
            authors = [_shape_author(a) for a in (pub.get("authors") or []) if a.get("name")]
            year, venue = _summary_to_year_venue(pub.get("summary") or "")

            papers.append({
                "title": title,
                "url": entry.get("link") or "",
                "authors": authors,
                "authors_truncated": False,
                "year": year,
                "venue": venue,
            })
            new_added += 1

        if new_added == 0:
            break
        start += page_size
        if total is not None and start >= total:
            break
        time.sleep(0.5)

    return {"papers": papers, "total": total, "fetched": len(papers), "blocked": False}
