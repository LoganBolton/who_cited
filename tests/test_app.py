"""Network-free tests for app.py.

We never hit Google Scholar or Semantic Scholar; every test runs against
saved HTML fixtures or pure-Python helpers.
"""

from pathlib import Path

import pytest

import app
from app import (
    app as flask_app,
    extract_cites_ids,
    gs_total_results,
    is_blocked_html,
    parse_gs_results_html,
    parse_pasted_html,
    _parse_author_line,
    _parse_cookie_string,
    _title_variants,
)


FIXTURES = Path(__file__).parent / "fixtures"
RESULTS_HTML = (FIXTURES / "gs_results_page.html").read_text(encoding="utf-8")
CAPTCHA_HTML = (FIXTURES / "gs_captcha_page.html").read_text(encoding="utf-8")


# ---------- extract_cites_ids -------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (
        "https://scholar.google.com/scholar?oi=bibs&hl=en&cites=11329264822226102204,3714977367501678530&as_sdt=5",
        ["11329264822226102204", "3714977367501678530"],
    ),
    (
        "https://scholar.google.com/scholar?cites=2960712678066186980",
        ["2960712678066186980"],
    ),
    (
        "https://scholar.google.com/scholar?cites=1,2,3,4,5",
        ["1", "2", "3", "4", "5"],
    ),
    # Tolerates whitespace inside the comma list
    (
        "https://scholar.google.com/scholar?cites=11329264822226102204, 3714977367501678530",
        ["11329264822226102204", "3714977367501678530"],
    ),
])
def test_extract_cites_ids_positive(url, expected):
    assert extract_cites_ids(url) == expected


@pytest.mark.parametrize("url", [
    "https://scholar.google.com/citations?view_op=view_citation&hl=en&user=foo",
    "https://scholar.google.com/scholar?q=hello",
    "https://scholar.google.com/scholar?cites=&hl=en",
    "https://example.com/?cites=123",  # not a Scholar URL — still no harm in returning ids
    "",
])
def test_extract_cites_ids_negative(url):
    # We only require: when there is no `cites=` param, return None.
    if "cites=" in url and url.split("cites=", 1)[1].split("&", 1)[0]:
        pytest.skip("URL has a cites= value; covered by positive cases")
    assert extract_cites_ids(url) is None


# ---------- parse_gs_results_html --------------------------------------------

def test_parse_results_basic_fields():
    rows = parse_gs_results_html(RESULTS_HTML)
    assert len(rows) == 10  # GS default page size
    first = rows[0]
    assert first["title"] == "Seed1. 5-vl technical report"
    assert first["year"] == 2025
    assert first["url"].startswith("https://arxiv.org/abs/")
    assert isinstance(first["authors"], list) and len(first["authors"]) >= 3


def test_parse_results_all_have_titles_and_authors():
    rows = parse_gs_results_html(RESULTS_HTML)
    for r in rows:
        assert r["title"]
        assert isinstance(r["authors"], list)
        # Year should either be None or a sane 4-digit int
        assert r["year"] is None or 1900 <= r["year"] <= 2100


def test_parse_results_dedupe_ready():
    """parse_gs_results_html returns raw entries; dedupe is the caller's job."""
    rows = parse_gs_results_html(RESULTS_HTML)
    titles = [r["title"].lower() for r in rows]
    # Titles within a single page should already be distinct
    assert len(set(titles)) == len(titles)


# ---------- gs_total_results --------------------------------------------------

def test_total_results_from_results_page():
    assert gs_total_results(RESULTS_HTML) == 219


def test_total_results_handles_thousand_separators():
    assert gs_total_results("<div>About 1,234 results (0.05 sec)</div>") == 1234


def test_total_results_returns_none_when_absent():
    assert gs_total_results("<html><body>nothing</body></html>") is None


# ---------- is_blocked_html ---------------------------------------------------

def test_blocked_detector_flags_captcha_page():
    assert is_blocked_html(CAPTCHA_HTML) is True


def test_blocked_detector_passes_real_results():
    assert is_blocked_html(RESULTS_HTML) is False


# ---------- _parse_author_line ------------------------------------------------

def test_author_line_typical():
    info = _parse_author_line(
        "S Bai, Y Cai, R Chen, K Chen, X Chen, Z Cheng … - arXiv preprint arXiv…, 2025 - arxiv.org"
    )
    assert info["year"] == 2025
    assert "arXiv preprint" in info["venue"]
    assert info["authors"][:3] == ["S Bai", "Y Cai", "R Chen"]
    assert "…" not in "".join(info["authors"])


def test_author_line_no_year():
    info = _parse_author_line("J Doe, A Smith - Some Venue - host.example")
    assert info["year"] is None
    assert info["authors"] == ["J Doe", "A Smith"]


def test_author_line_empty():
    assert _parse_author_line("") == {"authors": [], "venue": "", "year": None}


# ---------- _parse_cookie_string ----------------------------------------------

def test_cookie_string_parses_browser_format():
    parsed = _parse_cookie_string("NID=abc; __Secure-3PSID=xyz; SID=foo")
    assert parsed == {"NID": "abc", "__Secure-3PSID": "xyz", "SID": "foo"}


def test_cookie_string_handles_extra_whitespace_and_blanks():
    parsed = _parse_cookie_string("  NID=abc ;  ; FOO=bar ")
    assert parsed == {"NID": "abc", "FOO": "bar"}


def test_cookie_string_empty():
    assert _parse_cookie_string("") == {}


# ---------- _title_variants ---------------------------------------------------

def test_title_variants_strips_subtitle_and_shortens():
    v = _title_variants("BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding")
    assert v[0].startswith("BERT:")
    assert "BERT" in v[1]  # subtitle stripped
    assert any(len(x.split()) <= 6 for x in v)  # short variant present


def test_title_variants_short_title_unchanged():
    v = _title_variants("Hello World")
    assert v == ["Hello World"]


# ---------- parse_pasted_html -------------------------------------------------

def test_parse_pasted_html_full_pipeline():
    out = parse_pasted_html(RESULTS_HTML)
    assert out["fetched"] == 10
    assert out["total"] == 219
    assert out["blocked"] is False
    assert out["papers"][0]["title"] == "Seed1. 5-vl technical report"


def test_parse_pasted_html_concatenated_pages_are_deduped():
    blob = RESULTS_HTML + "\n" + RESULTS_HTML
    out = parse_pasted_html(blob)
    assert out["fetched"] == 10  # dedupe keeps it at 10 even though we doubled


# ---------- Flask endpoint ----------------------------------------------------

@pytest.fixture()
def client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_endpoint_rejects_empty_payload(client):
    r = client.post("/api/citations", json={})
    assert r.status_code == 400


def test_endpoint_pasted_html_path(client):
    r = client.post("/api/citations", json={"html": RESULTS_HTML})
    assert r.status_code == 200
    body = r.get_json()
    assert body["source"] == "pasted_html"
    assert body["count"] == 10
    assert body["total"] == 219
    assert body["citations"][0]["title"].startswith("Seed1")


def test_endpoint_cites_url_blocked_path(client, monkeypatch):
    """When direct scraping is blocked, the endpoint still 200s with a warning."""
    def fake_scrape(*_a, **_kw):
        return {"papers": [], "total": None, "fetched": 0, "blocked": True}

    monkeypatch.setattr(app, "scrape_gs_cites", fake_scrape)
    monkeypatch.setattr(app, "SERPAPI_KEY", "")  # force serpapi to skip

    r = client.post("/api/citations", json={
        "url": "https://scholar.google.com/scholar?cites=123,456",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["blocked"] is True
    assert body["count"] == 0
    assert "CAPTCHA" in body["warning"]


def test_endpoint_cites_url_success_path(client, monkeypatch):
    """When scraping succeeds, the endpoint returns the parsed papers."""
    def fake_scrape(*_a, **_kw):
        return {
            "papers": [{"title": "Paper A", "url": "https://x", "authors": ["X Y"], "year": 2024, "venue": "v"}],
            "total": 219,
            "fetched": 1,
            "blocked": False,
        }

    monkeypatch.setattr(app, "scrape_gs_cites", fake_scrape)
    monkeypatch.setattr(app, "SERPAPI_KEY", "")

    r = client.post("/api/citations", json={
        "url": "https://scholar.google.com/scholar?cites=123",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 1
    assert body["total"] == 219
    assert body["blocked"] is False
    assert body["citations"][0]["title"] == "Paper A"
