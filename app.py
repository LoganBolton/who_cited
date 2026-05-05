"""Flask routes for who_cited.

The heavy lifting lives in service modules: `gs`, `s2`, `openalex`,
`serpapi_client`. This file only orchestrates them.
"""

from __future__ import annotations

from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request

import gs
import openalex
import s2
import serpapi_client

app = Flask(__name__)


# ---------- response helpers --------------------------------------------------

def _empty_paper(title: str, url: str = "") -> dict:
    return {
        "title": title,
        "authors": [],
        "year": None,
        "venue": "",
        "url": url,
        "paperId": None,
    }


def _maybe_enrich(citations: list[dict], enrich: bool) -> None:
    if enrich and citations:
        openalex.enrich_authors(citations)
        serpapi_client.enrich_author_profiles(citations)


def _captcha_warning(fetched: int, total: int | None = None) -> str:
    prefix = (
        f"Google Scholar reports {total} citations, but served a CAPTCHA before listing them. "
        if total and total > fetched
        else f"Google Scholar served a CAPTCHA after {fetched} results. "
    )
    return (
        prefix +
        "Set SERPAPI_KEY in the server environment, restart the app, and check the "
        "SerpApi account has remaining searches."
    )


# ---------- request handlers --------------------------------------------------

def _handle_cites_url(
    cites_ids: list[str], url: str, *, enrich: bool
) -> dict:
    # Prefer SerpApi when configured (it handles CAPTCHAs); fall through on miss.
    result = serpapi_client.fetch_cites(cites_ids)
    source = "serpapi"
    if result is None or (result["fetched"] == 0 and not result["total"]):
        result = gs.scrape_cites(cites_ids)
        source = "google_scholar"

    _maybe_enrich(result["papers"], enrich)

    return {
        "source": source,
        "paper": _empty_paper(f"Cited-by results for {len(cites_ids)} cluster ID(s)", url),
        "citations": result["papers"],
        "count": result["fetched"],
        "total": result["total"],
        "blocked": result["blocked"],
        "warning": _captcha_warning(result["fetched"], result["total"]) if result["blocked"] else None,
    }


def _resolve_title(url: str, title_override: str) -> tuple[str | None, tuple[dict, int] | None]:
    """Either return (title, None) or (None, (error_response, status))."""
    if title_override:
        return title_override, None
    if not url:
        return None, ({"error": "Provide a Google Scholar URL or a paper title."}, 400)
    if "scholar.google" not in urlparse(url).netloc:
        return None, ({"error": "URL doesn't look like Google Scholar."}, 400)
    title = gs.extract_title_from_citation_page(url)
    if title:
        return title, None
    return None, ({
        "error": (
            "Couldn't read the paper title from Google Scholar. "
            "Paste the paper title directly, or use a 'Cited by' URL "
            "(scholar.google.com/scholar?cites=...)."
        ),
        "needs_title": True,
    }, 502)


def _handle_title_lookup(title: str, *, enrich: bool) -> tuple[dict, int]:
    paper = s2.find_paper(title)
    if not paper:
        return {"error": f"No match on Semantic Scholar for: {title!r}"}, 404

    raw = s2.fetch_all_citations(paper["paperId"])
    citations = [s2.shape_citation(c) for c in raw]
    _maybe_enrich(citations, enrich)

    return {
        "source": "semantic_scholar",
        "paper": {
            "title": paper.get("title"),
            "authors": [a.get("name") for a in (paper.get("authors") or [])],
            "year": paper.get("year"),
            "venue": paper.get("venue") or "",
            "url": paper.get("url") or "",
            "paperId": paper["paperId"],
        },
        "citations": citations,
        "count": len(citations),
    }, 200


# ---------- routes ------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/citations", methods=["POST"])
def api_citations():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    title_override = (payload.get("title") or "").strip()
    enrich = bool(payload.get("enrich_affiliations", True))

    if not url and not title_override:
        return jsonify({"error": "Provide a Google Scholar URL or a paper title."}), 400

    cites_ids = gs.extract_cites_ids(url) if url else None
    if not cites_ids and url:
        cites_ids = gs.extract_cites_ids_from_citation_page(url)
    if cites_ids:
        return jsonify(_handle_cites_url(cites_ids, url, enrich=enrich))

    title, err = _resolve_title(url, title_override)
    if err is not None:
        body, status = err
        return jsonify(body), status

    body, status = _handle_title_lookup(title, enrich=enrich)
    return jsonify(body), status


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
