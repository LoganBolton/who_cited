import re
import time
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,year,venue,externalIds,url"
GS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_title_from_scholar(url: str) -> str | None:
    """Fetch a Google Scholar citation page and pull the paper title.

    GS blocks heavy scraping but single-shot requests usually go through.
    """
    try:
        resp = requests.get(url, headers=GS_HEADERS, timeout=10)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")

    # Citation detail page (view_op=view_citation): title is in #gsc_oci_title
    node = soup.select_one("#gsc_oci_title")
    if node:
        return node.get_text(strip=True)

    # Fallback: <title> tag — strip the trailing " - Google Scholar"
    if soup.title and soup.title.string:
        text = soup.title.string.strip()
        return re.sub(r"\s*-\s*Google Scholar\s*$", "", text)
    return None


def s2_find_paper(query: str) -> dict | None:
    """Search Semantic Scholar by title, return the best match or None."""
    r = requests.get(
        f"{S2_BASE}/paper/search",
        params={"query": query, "limit": 1, "fields": S2_FIELDS},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    data = r.json().get("data") or []
    return data[0] if data else None


def s2_fetch_all_citations(paper_id: str, max_pages: int = 20) -> list[dict]:
    """Fetch all citing papers, paging until exhausted or cap reached.

    Each page returns up to 1000 citations.
    """
    citations: list[dict] = []
    offset = 0
    limit = 1000
    for _ in range(max_pages):
        r = requests.get(
            f"{S2_BASE}/paper/{paper_id}/citations",
            params={"fields": S2_FIELDS, "limit": limit, "offset": offset},
            timeout=30,
        )
        if r.status_code == 429:
            # rate limited — back off briefly and retry once
            time.sleep(2)
            r = requests.get(
                f"{S2_BASE}/paper/{paper_id}/citations",
                params={"fields": S2_FIELDS, "limit": limit, "offset": offset},
                timeout=30,
            )
        if r.status_code != 200:
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


def shape_citation(entry: dict) -> dict:
    """Flatten a Semantic Scholar citation entry into the shape our UI wants."""
    paper = entry.get("citingPaper") or {}
    authors = [a.get("name") for a in (paper.get("authors") or []) if a.get("name")]
    return {
        "title": paper.get("title") or "(untitled)",
        "authors": authors,
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

    if not url and not title_override:
        return jsonify({"error": "Provide a Google Scholar URL or a paper title."}), 400

    title = title_override
    if not title and url:
        if "scholar.google" not in urlparse(url).netloc:
            return jsonify({"error": "URL doesn't look like Google Scholar."}), 400
        title = extract_title_from_scholar(url)
        if not title:
            return jsonify({
                "error": (
                    "Couldn't read the paper title from Google Scholar "
                    "(GS likely blocked the request). Paste the paper title directly."
                ),
                "needs_title": True,
            }), 502

    paper = s2_find_paper(title)
    if not paper:
        return jsonify({"error": f"No match on Semantic Scholar for: {title!r}"}), 404

    paper_id = paper.get("paperId")
    raw = s2_fetch_all_citations(paper_id)
    citations = [shape_citation(c) for c in raw]

    return jsonify({
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
