# who_cited

Paste a Google Scholar link, get back every paper that cites it (with authors).

## How it works

1. You paste a Google Scholar citation URL (or just a paper title).
2. The backend reads the title from the GS page (one request — single-shot scraping usually goes through).
3. It looks the paper up on the [Semantic Scholar API](https://api.semanticscholar.org/) and fetches all citing papers.
4. The frontend renders the list with authors, year, and venue.

> Note: Semantic Scholar's citation count can differ from Google Scholar's — GS tends to report higher numbers because it includes preprints, theses, and books that S2 may miss.

## Install and run with uv

```bash
uv sync
uv run python app.py
```

Then open http://127.0.0.1:5000.

## Running over SSH

The Flask app binds to `127.0.0.1:5000` on the remote machine. If you are
SSH'd into the machine, start the app there:

```bash
cd /path/to/who_cited
uv sync
uv run python app.py
```

In a second terminal on your local machine, forward port 5000:

```bash
ssh -L 5000:127.0.0.1:5000 your-user@your-server
```

Then open http://127.0.0.1:5000 in your local browser.

## Run tests

```bash
uv sync --extra dev
uv run pytest
```

## Project layout

```
app.py                  Flask backend + Semantic Scholar lookup
templates/index.html    Single-page UI
static/style.css        Styling
static/script.js        Frontend logic
pyproject.toml          Python deps and project metadata
uv.lock                 Locked dependency versions
```

## Limitations

- Google Scholar has no public API and aggressively blocks scrapers; if the title-extraction step gets blocked, paste the paper title in the fallback field instead.
- Semantic Scholar's citation graph is large but not identical to GS's.
