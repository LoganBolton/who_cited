"""Google Scholar: HTML parsing and the paginating cited-by scraper."""

from __future__ import annotations

import random
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

GS_BASE = "https://scholar.google.com"
GS_SCHOLAR = f"{GS_BASE}/scholar"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


# ---------- URL + cookie helpers ---------------------------------------------

def extract_cites_ids(url: str) -> list[str] | None:
    """Pull cluster IDs out of a `?cites=ID,ID,…` GS URL. None if absent."""
    raw = parse_qs(urlparse(url).query).get("cites", [None])[0]
    if not raw:
        return None
    ids = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    return ids or None


def extract_cites_ids_from_citation_html(html: str) -> list[str] | None:
    """Pull cited-by cluster IDs from a GS profile citation detail page."""
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.select('a[href*="cites="]'):
        href = link.get("href") or ""
        cites_ids = extract_cites_ids(urljoin(GS_BASE, href))
        if cites_ids:
            return cites_ids
    return None


def parse_cookie_string(raw: str) -> dict:
    """Browser cookie header → {name: value}."""
    out: dict[str, str] = {}
    for piece in (raw or "").split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# ---------- HTML parsing ------------------------------------------------------

def parse_author_line(line: str) -> dict:
    """Parse a `.gs_a` line. GS shape: `Authors - Venue, Year - host`."""
    line = re.sub(r"\s+", " ", line).strip()
    info: dict = {"authors": [], "venue": "", "year": None, "authors_truncated": False}
    if not line:
        return info

    parts = [p.strip() for p in line.split(" - ")]
    if len(parts) >= 2:
        author_part, middle = parts[0], parts[1]
        ymatch = YEAR_RE.search(middle)
        if ymatch:
            info["year"] = int(ymatch.group(0))
            info["venue"] = middle[: ymatch.start()].rstrip(", ").strip()
        else:
            info["venue"] = middle
    else:
        author_part = line

    cleaned: list[str] = []
    truncated = False
    for a in re.split(r",\s*", author_part):
        a = a.strip()
        if a in ("…", "..."):
            truncated = True
            continue
        if not a:
            continue
        if a.endswith("…") or a.endswith("..."):
            truncated = True
            a = a.rstrip("…").rstrip(".").strip()
            if not a:
                continue
        cleaned.append(a)
    info["authors"] = cleaned
    info["authors_truncated"] = truncated
    return info


def _authors_to_dicts(names: list[str]) -> list[dict]:
    return [{"name": n, "affiliations": []} for n in names]


def parse_results_html(html: str) -> list[dict]:
    """Parse a GS results page into citing-paper dicts (no dedupe)."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for r in soup.select("div.gs_r.gs_or.gs_scl"):
        rt = r.select_one("h3.gs_rt")
        if not rt:
            continue
        for tag in rt.select("span.gs_ctg2, span.gs_ct1, span.gs_ct2"):
            tag.extract()
        link = rt.select_one("a")
        title = rt.get_text(" ", strip=True)
        href = link.get("href") if link else ""

        a_div = r.select_one("div.gs_a")
        info = parse_author_line(a_div.get_text(" ", strip=True) if a_div else "")

        out.append({
            "title": title,
            "url": href or "",
            "authors": _authors_to_dicts(info["authors"]),
            "authors_truncated": info["authors_truncated"],
            "year": info["year"],
            "venue": info["venue"],
        })
    return out


def total_results(html: str) -> int | None:
    """Pull '… 219 results' off a GS results header. None if absent."""
    m = re.search(r"About\s+([\d,]+)\s+results?", html) or re.search(r"\b([\d,]+)\s+results?", html)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def is_blocked_html(html: str) -> bool:
    """Heuristic: did GS serve a CAPTCHA / challenge instead of results?"""
    lower = html.lower()
    return (
        "please show you're not a robot" in lower
        or "unusual traffic from your computer network" in lower
        or "/sorry/index" in lower
        or "g-recaptcha" in lower
        or ("captcha" in lower and "<form" in lower and "scholar" in lower)
    )


def _dedupe_by_title(papers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for p in papers:
        key = (p.get("title") or "").lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def parse_pasted_html(blob: str) -> dict:
    """Parse one or more concatenated GS results pages."""
    papers = _dedupe_by_title(parse_results_html(blob))
    return {
        "papers": papers,
        "total": total_results(blob),
        "fetched": len(papers),
        "blocked": False,
    }


# ---------- HTTP scrapers -----------------------------------------------------

def extract_title_from_citation_page(url: str) -> str | None:
    """Fetch a GS `view_op=view_citation` detail page and return the title."""
    try:
        resp = requests.get(url, headers=_headers(), timeout=15)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")

    for selector in ("#gsc_oci_title a", "#gsc_oci_title"):
        node = soup.select_one(selector)
        if node:
            return node.get_text(strip=True)

    if soup.title and soup.title.string:
        return re.sub(r"\s*-\s*Google Scholar\s*$", "", soup.title.string.strip())
    return None


def extract_cites_ids_from_citation_page(
    url: str,
    *,
    cookie_string: str = "",
) -> list[str] | None:
    """Fetch a GS profile citation detail page and return its cited-by IDs."""
    session = requests.Session()
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        session.cookies.update(cookies)
    try:
        resp = session.get(url, headers=_headers(), timeout=15)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or is_blocked_html(resp.text):
        return None
    return extract_cites_ids_from_citation_html(resp.text)


def scrape_cites(
    cites_ids: list[str],
    *,
    max_results: int = 1000,
    page_size: int = 10,
    cookie_string: str = "",
    delay_range: tuple[float, float] = (3.0, 6.0),
) -> dict:
    """Page through GS' `?cites=…` results until empty or blocked.

    Returns {"papers", "total", "fetched", "blocked"}.
    """
    cites_param = ",".join(cites_ids)
    session = requests.Session()
    cookies = parse_cookie_string(cookie_string)
    if cookies:
        session.cookies.update(cookies)

    # Warm-up so subsequent requests look like in-session navigation.
    try:
        session.get(GS_BASE + "/", headers=_headers(), timeout=15)
    except requests.RequestException:
        pass

    papers: list[dict] = []
    seen: set[str] = set()
    total: int | None = None
    blocked = False
    referer = GS_BASE + "/"
    start = 0

    while start < max_results:
        params = {
            "hl": "en",
            "as_sdt": "5",
            "cites": cites_param,
            "start": str(start),
            "num": str(page_size),
        }
        url = f"{GS_SCHOLAR}?{urlencode(params)}"
        headers = _headers() | {"Referer": referer}
        try:
            r = session.get(url, headers=headers, timeout=20)
        except requests.RequestException:
            break

        if r.status_code != 200:
            blocked = r.status_code in (403, 429, 503)
            break
        if total is None:
            total = total_results(r.text)
        if is_blocked_html(r.text):
            blocked = True
            break

        page = parse_results_html(r.text)
        if not page:
            break

        new_added = 0
        for p in page:
            key = p["title"].lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            papers.append(p)
            new_added += 1
        if new_added == 0:
            break

        referer = r.url
        start += page_size
        if total is not None and start >= total:
            break
        time.sleep(random.uniform(*delay_range))

    return {"papers": papers, "total": total, "fetched": len(papers), "blocked": blocked}
