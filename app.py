import concurrent.futures
import os
import random
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,year,venue,externalIds,url"
S2_FIELDS_WITH_AFFIL = "title,authors.name,authors.affiliations,year,venue,externalIds,url"
S2_API_KEY = os.environ.get("S2_API_KEY", "").strip()
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "").strip()
OPENALEX_BASE = "https://api.openalex.org/works"

GS_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def _gs_headers() -> dict:
    return {
        "User-Agent": random.choice(GS_USER_AGENTS),
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


def extract_cites_ids(url: str) -> list[str] | None:
    qs = parse_qs(urlparse(url).query)
    raw = qs.get("cites", [None])[0]
    if not raw:
        return None
    ids = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    return ids or None


def extract_title_from_scholar(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=_gs_headers(), timeout=15)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")

    anchor = soup.select_one("#gsc_oci_title a")
    if anchor:
        return anchor.get_text(strip=True)
    node = soup.select_one("#gsc_oci_title")
    if node:
        return node.get_text(strip=True)

    if soup.title and soup.title.string:
        text = soup.title.string.strip()
        return re.sub(r"\s*-\s*Google Scholar\s*$", "", text)
    return None


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _parse_author_line(line: str) -> dict:
    line = re.sub(r"\s+", " ", line).strip()
    info: dict = {"authors": [], "venue": "", "year": None, "authors_truncated": False}
    if not line:
        return info

    parts = [p.strip() for p in line.split(" - ")]
    if len(parts) >= 2:
        author_part = parts[0]
        middle = parts[1]
        ymatch = _YEAR_RE.search(middle)
        if ymatch:
            info["year"] = int(ymatch.group(0))
            venue = middle[: ymatch.start()].rstrip(", ").strip()
        else:
            venue = middle
        info["venue"] = venue
    else:
        author_part = line

    raw_authors = re.split(r",\s*", author_part)
    cleaned: list[str] = []
    truncated = False
    for a in raw_authors:
        a = a.strip()
        # GS appends "…" (or "...") to indicate the list was truncated. It usually
        # appears as a standalone trailing entry, but sometimes attached to the
        # last visible name.
        if a in ("…", "...", ""):
            truncated = truncated or a in ("…", "...")
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


def parse_gs_results_html(html: str) -> list[dict]:
    """Parse a Google Scholar results HTML page → list of citing papers."""
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
        info = _parse_author_line(a_div.get_text(" ", strip=True) if a_div else "")

        out.append({
            "title": title,
            "url": href or "",
            "authors": [{"name": n, "affiliations": []} for n in info["authors"]],
            "authors_truncated": info["authors_truncated"],
            "year": info["year"],
            "venue": info["venue"],
        })
    return out


def gs_total_results(html: str) -> int | None:
    m = re.search(r"About\s+([\d,]+)\s+results?", html)
    if not m:
        m = re.search(r"\b([\d,]+)\s+results?", html)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def is_blocked_html(html: str) -> bool:
    lower = html.lower()
    return (
        "please show you're not a robot" in lower
        or "unusual traffic from your computer network" in lower
        or "/sorry/index" in lower
        or "g-recaptcha" in lower
        or ("captcha" in lower and "<form" in lower and "scholar" in lower)
    )


def _parse_cookie_string(raw: str) -> dict:
    """Browser cookie header → {name: value}."""
    out = {}
    for piece in (raw or "").split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def scrape_gs_cites(
    cites_ids: list[str],
    *,
    max_results: int = 1000,
    page_size: int = 10,
    cookie_string: str = "",
    delay_range: tuple[float, float] = (3.0, 6.0),
) -> dict:
    """Page through GS' cites= results until empty or blocked.

    Returns {"papers": [...], "total": int|None, "fetched": int, "blocked": bool}.
    """
    base = "https://scholar.google.com/scholar"
    cites_param = ",".join(cites_ids)
    session = requests.Session()
    cookies = _parse_cookie_string(cookie_string)
    if cookies:
        session.cookies.update(cookies)

    seen_titles: set[str] = set()
    papers: list[dict] = []
    total: int | None = None
    blocked = False

    # Warm-up GET with the same headers, mimicking a user landing on /
    try:
        session.get("https://scholar.google.com/", headers=_gs_headers(), timeout=15)
    except requests.RequestException:
        pass

    referer = "https://scholar.google.com/"
    start = 0
    while start < max_results:
        params = {
            "hl": "en",
            "as_sdt": "5",
            "cites": cites_param,
            "start": str(start),
            "num": str(page_size),
        }
        url = f"{base}?{urlencode(params)}"
        headers = _gs_headers()
        headers["Referer"] = referer

        try:
            r = session.get(url, headers=headers, timeout=20)
        except requests.RequestException:
            break
        if r.status_code != 200:
            blocked = r.status_code in (403, 429, 503)
            break
        if is_blocked_html(r.text):
            blocked = True
            break

        if total is None:
            total = gs_total_results(r.text)

        page = parse_gs_results_html(r.text)
        if not page:
            break

        new_added = 0
        for p in page:
            key = p["title"].lower().strip()
            if key in seen_titles:
                continue
            seen_titles.add(key)
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


def serpapi_fetch_cites(cites_ids: list[str], *, max_results: int = 500) -> dict:
    """Fetch citing papers via SerpApi (paid, reliable) — requires SERPAPI_KEY."""
    if not SERPAPI_KEY:
        return {"papers": [], "total": None, "fetched": 0, "blocked": False, "skipped": True}

    cites_param = ",".join(cites_ids)
    papers: list[dict] = []
    seen: set[str] = set()
    total: int | None = None
    start = 0
    page_size = 20

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
            r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
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
            if not title:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            pub = entry.get("publication_info") or {}
            authors = [a.get("name") for a in (pub.get("authors") or []) if a.get("name")]
            summary = pub.get("summary") or ""
            year_match = _YEAR_RE.search(summary)
            year = int(year_match.group(0)) if year_match else None
            venue = re.sub(r"^[^-]*-\s*", "", summary)
            venue = _YEAR_RE.sub("", venue, count=1).strip(" ,-")
            papers.append({
                "title": title,
                "url": entry.get("link") or "",
                "authors": [{"name": n, "affiliations": []} for n in authors],
                "authors_truncated": False,  # SerpApi returns the full list
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


def parse_pasted_html(html_blob: str) -> dict:
    """Parse one or more concatenated GS results pages into citing papers."""
    seen: set[str] = set()
    papers: list[dict] = []
    total: int | None = gs_total_results(html_blob)
    for p in parse_gs_results_html(html_blob):
        key = p["title"].lower().strip()
        if key in seen:
            continue
        seen.add(key)
        papers.append(p)
    return {"papers": papers, "total": total, "fetched": len(papers), "blocked": False}


def _name_initial_last(name: str) -> tuple[str, str]:
    """Reduce a person name to (first-initial, lower-cased last name).

    A single-token name is treated as last-name-only (no initial), so a
    GS entry like 'Bai' matches an OpenAlex 'Shuai Bai' on last name alone.
    """
    cleaned = re.sub(r"[.,]", " ", name).strip()
    parts = [p for p in cleaned.split() if p]
    if not parts:
        return ("", "")
    last = parts[-1].lower()
    if len(parts) == 1:
        return ("", last)
    return (parts[0][0].lower(), last)


def _author_name_match(gs_name: str, openalex_name: str) -> bool:
    g_init, g_last = _name_initial_last(gs_name)
    o_init, o_last = _name_initial_last(openalex_name)
    if not g_last or not o_last:
        return False
    if g_last != o_last:
        return False
    # GS often only gives an initial; tolerate missing first-letter info on either side.
    if not g_init or not o_init:
        return True
    return g_init == o_init


def openalex_lookup_authors(title: str, *, timeout: float = 10.0) -> list[dict]:
    """Search OpenAlex by title; return [{name, affiliations}] for the top hit."""
    if not title:
        return []
    params = {"search": title, "per-page": 1}
    if OPENALEX_MAILTO:
        params["mailto"] = OPENALEX_MAILTO
    try:
        r = requests.get(OPENALEX_BASE, params=params, timeout=timeout)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    results = data.get("results") or []
    if not results:
        return []
    work = results[0]
    authorships = work.get("authorships") or []
    out: list[dict] = []
    for a in authorships:
        au = a.get("author") or {}
        name = (au.get("display_name") or "").strip()
        if not name:
            continue
        seen: set[str] = set()
        affs: list[str] = []
        for inst in (a.get("institutions") or []):
            disp = (inst.get("display_name") or "").strip()
            if disp and disp.lower() not in seen:
                seen.add(disp.lower())
                affs.append(disp)
        # Only fall back to raw affiliation strings if structured ones are missing.
        if not affs:
            for raw in (a.get("raw_affiliation_strings") or []):
                if not raw:
                    continue
                raw = raw.strip()
                if raw.lower() in seen:
                    continue
                seen.add(raw.lower())
                affs.append(raw)
        out.append({"name": name, "affiliations": affs})
    return out


def enrich_authors_with_openalex(
    citations: list[dict],
    *,
    max_workers: int = 8,
    overall_timeout: float = 45.0,
) -> list[dict]:
    """Look up each citation on OpenAlex in parallel; attach affiliations to each author.

    Mutates each citation's `authors` entries in place by setting `affiliations`.
    """
    if not citations:
        return citations

    def _one(c: dict) -> tuple[dict, list[dict]]:
        return c, openalex_lookup_authors(c.get("title") or "")

    deadline = time.monotonic() + overall_timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_one, c): c for c in citations}
        for fut in concurrent.futures.as_completed(futures):
            if time.monotonic() > deadline:
                break
            try:
                c, oa_authors = fut.result()
            except Exception:
                continue
            for author in c.get("authors") or []:
                if author.get("affiliations"):
                    continue
                for oa in oa_authors:
                    if _author_name_match(author["name"], oa["name"]):
                        author["affiliations"] = oa["affiliations"]
                        break
    return citations


def _title_variants(title: str) -> list[str]:
    title = title.strip()
    variants = [title]
    for sep in [":", " — ", " – ", " - "]:
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


def _s2_get(path: str, params: dict, retries: int = 3, backoff: float = 4.0):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(f"{S2_BASE}{path}", params=params, timeout=20)
        except requests.RequestException as e:
            last = e
            time.sleep(backoff * (attempt + 1))
            continue
        if r.status_code == 429:
            time.sleep(backoff * (attempt + 1))
            continue
        return r
    if isinstance(last, Exception):
        raise last
    return None


def s2_find_paper(query: str) -> dict | None:
    for variant in _title_variants(query):
        r = _s2_get("/paper/search", {"query": variant, "limit": 5, "fields": S2_FIELDS})
        if r is None or r.status_code != 200:
            continue
        data = r.json().get("data") or []
        if data:
            return data[0]
    return None


def s2_fetch_all_citations(paper_id: str, max_pages: int = 20) -> list[dict]:
    citations: list[dict] = []
    offset = 0
    limit = 1000
    for _ in range(max_pages):
        r = _s2_get(
            f"/paper/{paper_id}/citations",
            {"fields": S2_FIELDS_WITH_AFFIL, "limit": limit, "offset": offset},
        )
        if r is None or r.status_code != 200:
            break
        body = r.json()
        page = body.get("data") or []
        citations.extend(page)
        if len(page) < limit:
            break
        offset += limit
        if "next" not in body:
            break
    return citations


def shape_s2_citation(entry: dict) -> dict:
    paper = entry.get("citingPaper") or {}
    authors_raw = paper.get("authors") or []
    authors = []
    for a in authors_raw:
        name = a.get("name")
        if not name:
            continue
        affs = []
        for aff in (a.get("affiliations") or []):
            if isinstance(aff, str) and aff.strip():
                affs.append(aff.strip())
        authors.append({"name": name, "affiliations": affs})
    return {
        "title": paper.get("title") or "(untitled)",
        "authors": authors,
        "authors_truncated": False,  # S2 returns the full author list
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "url": paper.get("url") or "",
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/citations", methods=["POST"])
def api_citations():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    title_override = (payload.get("title") or "").strip()
    cookie_string = (payload.get("cookies") or "").strip()
    pasted_html = (payload.get("html") or "").strip()

    enrich = payload.get("enrich_affiliations", True)

    # Path 0: user pasted GS results HTML directly (escape hatch when blocked).
    if pasted_html:
        result = parse_pasted_html(pasted_html)
        if enrich:
            enrich_authors_with_openalex(result["papers"])
        return jsonify({
            "source": "pasted_html",
            "paper": {
                "title": "Citing papers (from pasted HTML)",
                "authors": [], "year": None, "venue": "", "url": url,
                "paperId": None,
            },
            "citations": result["papers"],
            "count": result["fetched"],
            "total": result["total"],
            "blocked": False,
        })

    if not url and not title_override:
        return jsonify({"error": "Provide a Google Scholar URL or a paper title."}), 400

    # Path A: GS "cited by" URL with cites=ID,ID,... — try SerpApi → direct scrape.
    cites_ids = extract_cites_ids(url) if url else None
    if cites_ids:
        # Prefer SerpApi if configured (most reliable).
        result = serpapi_fetch_cites(cites_ids)
        used = "serpapi"
        if result.get("skipped") or (result["fetched"] == 0 and not result.get("total")):
            # Fall through to direct scraping.
            result = scrape_gs_cites(cites_ids, cookie_string=cookie_string)
            used = "google_scholar"

        if enrich:
            enrich_authors_with_openalex(result["papers"])

        # Always return whatever we got — partial is better than nothing.
        warning = None
        if result["blocked"]:
            warning = (
                f"Google Scholar served a CAPTCHA after {result['fetched']} results. "
                "Options: paste GS cookies (NID/__Secure-3PSID) from your logged-in browser, "
                "set SERPAPI_KEY, or paste the saved HTML below."
            )
        return jsonify({
            "source": used,
            "paper": {
                "title": (
                    f"Cited-by results for {len(cites_ids)} cluster ID(s)"
                ),
                "authors": [], "year": None, "venue": "", "url": url,
                "paperId": None,
            },
            "citations": result["papers"],
            "count": result["fetched"],
            "total": result["total"],
            "blocked": result["blocked"],
            "warning": warning,
        })

    # Path B: title-based S2 fallback.
    title = title_override
    if not title and url:
        if "scholar.google" not in urlparse(url).netloc:
            return jsonify({"error": "URL doesn't look like Google Scholar."}), 400
        title = extract_title_from_scholar(url)
        if not title:
            return jsonify({
                "error": (
                    "Couldn't read the paper title from Google Scholar. "
                    "Paste the paper title directly, or use a 'Cited by' URL "
                    "(scholar.google.com/scholar?cites=...)."
                ),
                "needs_title": True,
            }), 502

    paper = s2_find_paper(title)
    if not paper:
        return jsonify({"error": f"No match on Semantic Scholar for: {title!r}"}), 404

    paper_id = paper.get("paperId")
    raw = s2_fetch_all_citations(paper_id)
    citations = [shape_s2_citation(c) for c in raw]
    if enrich:
        enrich_authors_with_openalex(citations)

    return jsonify({
        "source": "semantic_scholar",
        "paper": {
            "title": paper.get("title"),
            "authors": [a.get("name") for a in (paper.get("authors") or [])],
            "year": paper.get("year"),
            "venue": paper.get("venue") or "",
            "url": paper.get("url") or "",
            "paperId": paper_id,
        },
        "citations": citations,
        "count": len(citations),
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
