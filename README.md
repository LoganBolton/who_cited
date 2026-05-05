# who_cited

Paste a Google Scholar link, get back every paper that cites it (with authors).

## How it works

1. You paste a Google Scholar citation URL (or just a paper title).
2. The backend reads the title from the GS page (one request — single-shot scraping usually goes through).
3. It looks the paper up on the [Semantic Scholar API](https://api.semanticscholar.org/) and fetches all citing papers.
4. The frontend renders the list with authors, year, and venue.

> Note: Semantic Scholar's citation count can differ from Google Scholar's — GS tends to report higher numbers because it includes preprints, theses, and books that S2 may miss.

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

## Project layout

```
app.py                  Flask backend + Semantic Scholar lookup
templates/index.html    Single-page UI
static/style.css        Styling
static/script.js        Frontend logic
requirements.txt        Python deps
```

## Limitations

- Google Scholar has no public API and aggressively blocks scrapers; if the title-extraction step gets blocked, paste the paper title in the fallback field instead.
- Semantic Scholar's citation graph is large but not identical to GS's.
