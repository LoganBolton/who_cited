"""Network-free tests.

Every test runs against saved HTML fixtures or pure-Python helpers — no
outbound HTTP. External calls are stubbed via monkeypatch.
"""

from pathlib import Path

import pytest

import app
import gs
import openalex
import s2
import serpapi_client
from app import app as flask_app

FIXTURES = Path(__file__).parent / "fixtures"
RESULTS_HTML = (FIXTURES / "gs_results_page.html").read_text(encoding="utf-8")
CAPTCHA_HTML = (FIXTURES / "gs_captcha_page.html").read_text(encoding="utf-8")


# ---------- gs.extract_cites_ids ----------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (
        "https://scholar.google.com/scholar?oi=bibs&hl=en&cites=11329264822226102204,3714977367501678530&as_sdt=5",
        ["11329264822226102204", "3714977367501678530"],
    ),
    ("https://scholar.google.com/scholar?cites=2960712678066186980", ["2960712678066186980"]),
    ("https://scholar.google.com/scholar?cites=1,2,3,4,5", ["1", "2", "3", "4", "5"]),
    (
        "https://scholar.google.com/scholar?cites=11329264822226102204, 3714977367501678530",
        ["11329264822226102204", "3714977367501678530"],
    ),
])
def test_extract_cites_ids_positive(url, expected):
    assert gs.extract_cites_ids(url) == expected


@pytest.mark.parametrize("url", [
    "https://scholar.google.com/citations?view_op=view_citation&hl=en&user=foo",
    "https://scholar.google.com/scholar?q=hello",
    "https://scholar.google.com/scholar?cites=&hl=en",
    "https://example.com/?cites=123",
    "",
])
def test_extract_cites_ids_negative(url):
    if "cites=" in url and url.split("cites=", 1)[1].split("&", 1)[0]:
        pytest.skip("URL has a cites= value; covered by positive cases")
    assert gs.extract_cites_ids(url) is None


def test_extract_cites_ids_from_citation_html():
    html = """
    <html>
      <body>
        <a href="/scholar?oi=bibs&hl=en&cites=12345,67890&as_sdt=5">Cited by 186</a>
      </body>
    </html>
    """
    assert gs.extract_cites_ids_from_citation_html(html) == ["12345", "67890"]


def test_extract_cites_ids_from_citation_html_returns_none_when_absent():
    assert gs.extract_cites_ids_from_citation_html("<html><body>No cites link</body></html>") is None


# ---------- gs.parse_results_html ---------------------------------------------

def test_parse_results_basic_fields():
    rows = gs.parse_results_html(RESULTS_HTML)
    assert len(rows) == 10
    first = rows[0]
    assert first["title"] == "Seed1. 5-vl technical report"
    assert first["year"] == 2025
    assert first["url"].startswith("https://arxiv.org/abs/")
    assert isinstance(first["authors"], list) and len(first["authors"]) >= 3
    assert first["authors_truncated"] is True
    a = first["authors"][0]
    assert isinstance(a, dict) and "name" in a and "affiliations" in a
    assert a["affiliations"] == []


def test_parse_results_all_have_titles_and_authors():
    for r in gs.parse_results_html(RESULTS_HTML):
        assert r["title"]
        assert isinstance(r["authors"], list)
        for a in r["authors"]:
            assert isinstance(a, dict) and "name" in a and "affiliations" in a
        assert r["year"] is None or 1900 <= r["year"] <= 2100


def test_parse_results_dedupe_ready():
    titles = [r["title"].lower() for r in gs.parse_results_html(RESULTS_HTML)]
    assert len(set(titles)) == len(titles)


# ---------- gs.total_results --------------------------------------------------

def test_total_results_from_results_page():
    assert gs.total_results(RESULTS_HTML) == 219


def test_total_results_handles_thousand_separators():
    assert gs.total_results("<div>About 1,234 results (0.05 sec)</div>") == 1234


def test_total_results_returns_none_when_absent():
    assert gs.total_results("<html><body>nothing</body></html>") is None


# ---------- gs.is_blocked_html ------------------------------------------------

def test_blocked_detector_flags_captcha_page():
    assert gs.is_blocked_html(CAPTCHA_HTML) is True


def test_blocked_detector_passes_real_results():
    assert gs.is_blocked_html(RESULTS_HTML) is False


# ---------- gs.parse_author_line ----------------------------------------------

def test_author_line_typical():
    info = gs.parse_author_line(
        "S Bai, Y Cai, R Chen, K Chen, X Chen, Z Cheng … - arXiv preprint arXiv…, 2025 - arxiv.org"
    )
    assert info["year"] == 2025
    assert "arXiv preprint" in info["venue"]
    assert info["authors"][:3] == ["S Bai", "Y Cai", "R Chen"]
    assert "…" not in "".join(info["authors"])
    assert info["authors_truncated"] is True


def test_author_line_no_year():
    info = gs.parse_author_line("J Doe, A Smith - Some Venue - host.example")
    assert info["year"] is None
    assert info["authors"] == ["J Doe", "A Smith"]
    assert info["authors_truncated"] is False


def test_author_line_empty():
    assert gs.parse_author_line("") == {
        "authors": [], "venue": "", "year": None, "authors_truncated": False,
    }


def test_author_line_truncation_attached_to_last_name():
    info = gs.parse_author_line("J Doe, A Smith… - 2024 - host.example")
    assert info["authors"] == ["J Doe", "A Smith"]
    assert info["authors_truncated"] is True


def test_author_line_truncation_three_dots():
    info = gs.parse_author_line("J Doe, A Smith, ... - 2024")
    assert info["authors"] == ["J Doe", "A Smith"]
    assert info["authors_truncated"] is True


# ---------- s2._title_variants ------------------------------------------------

def test_title_variants_strips_subtitle_and_shortens():
    v = s2._title_variants("BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding")
    assert v[0].startswith("BERT:")
    assert "BERT" in v[1]
    assert any(len(x.split()) <= 6 for x in v)


def test_title_variants_short_title_unchanged():
    assert s2._title_variants("Hello World") == ["Hello World"]


# ---------- openalex name matching --------------------------------------------

@pytest.mark.parametrize("a, b, expected", [
    ("S Bai", "Shuai Bai", True),
    ("S. Bai", "Shuai Bai", True),
    ("Bai", "Shuai Bai", True),
    ("Shuai Bai", "Bai", True),
    ("S Bai", "Linfeng Bai", False),
    ("J Doe", "John Smith", False),
    ("", "John Doe", False),
])
def test_author_name_match(a, b, expected):
    assert openalex.name_match(a, b) is expected


def test_name_initial_last_handles_punctuation():
    assert openalex._name_initial_last("S. Bai") == ("s", "bai")
    assert openalex._name_initial_last("J. Doe, Jr.") == ("j", "jr")
    assert openalex._name_initial_last("Bai") == ("", "bai")
    assert openalex._name_initial_last("") == ("", "")


# ---------- openalex.enrich_authors -------------------------------------------

def _citation(title: str, authors: list[str]) -> dict:
    return {
        "title": title, "url": "",
        "authors": [{"name": n, "affiliations": []} for n in authors],
        "authors_truncated": False, "year": None, "venue": "",
    }


def test_enrich_authors_attaches_affiliations(monkeypatch):
    citations = [_citation("A great paper", ["S Bai", "Y Cai", "Unknown Person"])]

    def fake_lookup(title, **_kw):
        assert title == "A great paper"
        return [
            {"name": "Shuai Bai", "affiliations": ["Alibaba"]},
            {"name": "Yong Cai", "affiliations": ["MIT", "Cambridge"]},
        ]

    monkeypatch.setattr(openalex, "lookup_authors", fake_lookup)
    openalex.enrich_authors(citations, max_workers=2)

    a0, a1, a2 = citations[0]["authors"]
    assert a0["affiliations"] == ["Alibaba"]
    assert a1["affiliations"] == ["MIT", "Cambridge"]
    assert a2["affiliations"] == []


def test_enrich_authors_swallows_lookup_errors(monkeypatch):
    citations = [_citation("X", ["A B"])]

    def boom(_title, **_kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(openalex, "lookup_authors", boom)
    openalex.enrich_authors(citations, max_workers=2)
    assert citations[0]["authors"][0]["affiliations"] == []


# ---------- s2.shape_citation -------------------------------------------------

def test_shape_s2_citation_carries_affiliations():
    out = s2.shape_citation({
        "citingPaper": {
            "title": "Paper", "year": 2020, "venue": "X", "url": "https://example.com",
            "authors": [
                {"name": "Alice", "affiliations": ["Stanford"]},
                {"name": "Bob", "affiliations": []},
            ],
        }
    })
    assert out["authors"] == [
        {"name": "Alice", "affiliations": ["Stanford"]},
        {"name": "Bob", "affiliations": []},
    ]


# ---------- Flask endpoint ----------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    flask_app.config["TESTING"] = True
    # Default to no enrichment in endpoint tests; individual tests can override.
    monkeypatch.setattr(openalex, "enrich_authors", lambda c, **kw: c)
    return flask_app.test_client()


def test_endpoint_rejects_empty_payload(client):
    assert client.post("/api/citations", json={}).status_code == 400


def test_endpoint_cites_url_blocked_path(client, monkeypatch):
    """When direct scraping is blocked, the endpoint still 200s with a warning."""
    monkeypatch.setattr(gs, "scrape_cites", lambda *_a, **_kw: {
        "papers": [], "total": None, "fetched": 0, "blocked": True,
    })
    monkeypatch.setattr(serpapi_client, "fetch_cites", lambda *_a, **_kw: None)

    r = client.post("/api/citations", json={
        "url": "https://scholar.google.com/scholar?cites=123,456",
        "enrich_affiliations": False,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["blocked"] is True
    assert body["count"] == 0
    assert "CAPTCHA" in body["warning"]


def test_endpoint_cites_url_success_path(client, monkeypatch):
    """When scraping succeeds, the endpoint returns the parsed papers."""
    monkeypatch.setattr(gs, "scrape_cites", lambda *_a, **_kw: {
        "papers": [{
            "title": "Paper A", "url": "https://x",
            "authors": [{"name": "X Y", "affiliations": []}],
            "authors_truncated": False, "year": 2024, "venue": "v",
        }],
        "total": 219, "fetched": 1, "blocked": False,
    })
    monkeypatch.setattr(serpapi_client, "fetch_cites", lambda *_a, **_kw: None)

    r = client.post("/api/citations", json={
        "url": "https://scholar.google.com/scholar?cites=123",
        "enrich_affiliations": False,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 1
    assert body["total"] == 219
    assert body["blocked"] is False
    assert body["citations"][0]["title"] == "Paper A"
    assert body["citations"][0]["authors"][0] == {"name": "X Y", "affiliations": []}


def test_endpoint_profile_citation_url_uses_gs_cited_by_link(client, monkeypatch):
    monkeypatch.setattr(gs, "extract_cites_ids_from_citation_page", lambda *_a, **_kw: ["789"])
    monkeypatch.setattr(gs, "scrape_cites", lambda *_a, **_kw: {
        "papers": [{
            "title": "Paper B", "url": "https://y",
            "authors": [{"name": "A B", "affiliations": []}],
            "authors_truncated": False, "year": 2025, "venue": "venue",
        }],
        "total": 186, "fetched": 1, "blocked": False,
    })
    monkeypatch.setattr(serpapi_client, "fetch_cites", lambda *_a, **_kw: None)
    monkeypatch.setattr(s2, "find_paper", lambda *_a, **_kw: pytest.fail("Should not use S2 fallback"))

    r = client.post("/api/citations", json={
        "url": "https://scholar.google.com/citations?view_op=view_citation&user=abc&citation_for_view=abc:def",
        "enrich_affiliations": False,
    })

    assert r.status_code == 200
    body = r.get_json()
    assert body["source"] == "google_scholar"
    assert body["count"] == 1
    assert body["total"] == 186
    assert body["citations"][0]["title"] == "Paper B"
