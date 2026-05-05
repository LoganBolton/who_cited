"""Semantic Scholar client: paper search + citation pagination + shape adapter."""

from __future__ import annotations

import os
import time

import requests

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors.name,authors.affiliations,year,venue,externalIds,url"
S2_API_KEY = os.environ.get("S2_API_KEY", "").strip()


def _get(path: str, params: dict, *, retries: int = 3, backoff: float = 4.0):
    """GET against Semantic Scholar with linear-backoff retry on 429 / network errors."""
    for attempt in range(retries):
        try:
            r = requests.get(f"{S2_BASE}{path}", params=params, timeout=20)
        except requests.RequestException:
            time.sleep(backoff * (attempt + 1))
            continue
        if r.status_code == 429:
            time.sleep(backoff * (attempt + 1))
            continue
        return r
    return None


def _title_variants(title: str) -> list[str]:
    """Progressively shorter title variants for fuzzy matching."""
    title = title.strip()
    variants = [title]
    for sep in (":", " — ", " – ", " - "):
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if head and head not in variants:
                variants.append(head)
            break
    words = title.split()
    if len(words) > 6:
        short = " ".join(words[:6])
        if short not in variants:
            variants.append(short)
    return variants


def find_paper(query: str) -> dict | None:
    """Search S2 by title; return the top hit or None."""
    for variant in _title_variants(query):
        r = _get("/paper/search", {"query": variant, "limit": 5, "fields": S2_FIELDS})
        if r is None or r.status_code != 200:
            continue
        data = r.json().get("data") or []
        if data:
            return data[0]
    return None


def fetch_all_citations(paper_id: str, *, max_pages: int = 20) -> list[dict]:
    """Fetch every citing paper for `paper_id`, paging until exhausted."""
    out: list[dict] = []
    offset, limit = 0, 1000
    for _ in range(max_pages):
        r = _get(
            f"/paper/{paper_id}/citations",
            {"fields": S2_FIELDS, "limit": limit, "offset": offset},
        )
        if r is None or r.status_code != 200:
            break
        body = r.json()
        page = body.get("data") or []
        out.extend(page)
        if len(page) < limit or "next" not in body:
            break
        offset += limit
    return out


def shape_citation(entry: dict) -> dict:
    """S2 citation entry → our common citing-paper shape."""
    paper = entry.get("citingPaper") or {}
    authors: list[dict] = []
    for a in paper.get("authors") or []:
        name = a.get("name")
        if not name:
            continue
        affs = [s.strip() for s in (a.get("affiliations") or []) if isinstance(s, str) and s.strip()]
        authors.append({"name": name, "affiliations": affs})
    return {
        "title": paper.get("title") or "(untitled)",
        "authors": authors,
        "authors_truncated": False,
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "url": paper.get("url") or "",
    }
