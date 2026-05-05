"""Optional SerpApi path: paid proxy that handles GS CAPTCHAs reliably."""

from __future__ import annotations

import os
import re
import time

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
            authors = [a.get("name") for a in (pub.get("authors") or []) if a.get("name")]
            year, venue = _summary_to_year_venue(pub.get("summary") or "")

            papers.append({
                "title": title,
                "url": entry.get("link") or "",
                "authors": [{"name": n, "affiliations": []} for n in authors],
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
