"""OpenAlex enrichment: attach institution affiliations to each author."""

from __future__ import annotations

import concurrent.futures
import os
import re
import time

import requests

OPENALEX_BASE = "https://api.openalex.org/works"
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "").strip()


def _name_initial_last(name: str) -> tuple[str, str]:
    """Reduce a name to (first-initial, lower-cased last name).

    Single-token names ('Bai') are treated as last-name-only, so a GS entry
    can match an OpenAlex full name on last name alone.
    """
    parts = [p for p in re.sub(r"[.,]", " ", name).split() if p]
    if not parts:
        return ("", "")
    last = parts[-1].lower()
    if len(parts) == 1:
        return ("", last)
    return (parts[0][0].lower(), last)


def name_match(gs_name: str, openalex_name: str) -> bool:
    g_init, g_last = _name_initial_last(gs_name)
    o_init, o_last = _name_initial_last(openalex_name)
    if not g_last or g_last != o_last:
        return False
    # GS often gives only an initial; tolerate a missing initial on either side.
    if not g_init or not o_init:
        return True
    return g_init == o_init


def _affiliations_from_authorship(a: dict) -> list[str]:
    """Pull deduped affiliation strings from one OpenAlex authorship entry."""
    seen: set[str] = set()
    affs: list[str] = []
    for inst in a.get("institutions") or []:
        disp = (inst.get("display_name") or "").strip()
        if disp and disp.lower() not in seen:
            seen.add(disp.lower())
            affs.append(disp)
    if affs:
        return affs
    # Structured institutions missing → fall back to raw strings from the paper.
    for raw in a.get("raw_affiliation_strings") or []:
        raw = (raw or "").strip()
        if raw and raw.lower() not in seen:
            seen.add(raw.lower())
            affs.append(raw)
    return affs


def lookup_authors(title: str, *, timeout: float = 10.0) -> list[dict]:
    """Search OpenAlex by title; return [{name, affiliations}] for the top hit."""
    if not title:
        return []
    params: dict = {"search": title, "per-page": 1}
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
    out: list[dict] = []
    for a in results[0].get("authorships") or []:
        name = ((a.get("author") or {}).get("display_name") or "").strip()
        if not name:
            continue
        out.append({"name": name, "affiliations": _affiliations_from_authorship(a)})
    return out


def enrich_authors(
    citations: list[dict],
    *,
    max_workers: int = 8,
    overall_timeout: float = 45.0,
) -> list[dict]:
    """Look up each citation's title on OpenAlex in parallel and attach affiliations.

    Mutates each citation's `authors[].affiliations` in place. Authors that
    already have affiliations (e.g. from the S2 path) are left alone.
    """
    if not citations:
        return citations

    deadline = time.monotonic() + overall_timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(lookup_authors, c.get("title") or ""): c for c in citations}
        for fut in concurrent.futures.as_completed(futures):
            if time.monotonic() > deadline:
                break
            citation = futures[fut]
            try:
                oa_authors = fut.result()
            except Exception:
                continue
            for author in citation.get("authors") or []:
                if author.get("affiliations"):
                    continue
                for oa in oa_authors:
                    if name_match(author["name"], oa["name"]):
                        author["affiliations"] = oa["affiliations"]
                        break
    return citations
