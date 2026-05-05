const form = document.getElementById("search-form");
const urlInput = document.getElementById("url");
const titleInput = document.getElementById("title");
const titleFallback = document.getElementById("title-fallback");
const submitBtn = document.getElementById("submit-btn");
const presetButtons = Array.from(document.querySelectorAll("[data-title]"));
const statusBox = document.getElementById("status");
const paperBox = document.getElementById("paper");
const paperTitleEl = document.getElementById("paper-title");
const paperMetaEl = document.getElementById("paper-meta");
const resultsBox = document.getElementById("results");
const resultsList = document.getElementById("results-list");
const countEl = document.getElementById("count");
const totalSuffixEl = document.getElementById("total-suffix");

function setStatus(text, kind = "info") {
  if (!text) {
    statusBox.hidden = true;
    statusBox.textContent = "";
    return;
  }
  statusBox.hidden = false;
  statusBox.className = `status ${kind}`;
  statusBox.textContent = text;
}

function authorName(a) {
  if (a == null) return "";
  if (typeof a === "string") return a;
  return a.name || "";
}

function authorAffiliations(a) {
  if (a && typeof a === "object" && Array.isArray(a.affiliations)) return a.affiliations;
  return [];
}

function formatAuthors(authors, truncated = false) {
  if (!authors || authors.length === 0) return "(unknown authors)";
  const joined = authors.map(authorName).filter(Boolean).join(", ");
  return truncated ? `${joined}, and others` : joined;
}

function buildAuthorsNode(authors, truncated = false) {
  const ul = document.createElement("ul");
  ul.className = "author-list";
  if (!authors || authors.length === 0) {
    const li = document.createElement("li");
    li.className = "author muted";
    li.textContent = "(unknown authors)";
    ul.appendChild(li);
    return ul;
  }
  for (const a of authors) {
    const li = document.createElement("li");
    li.className = "author";
    const nameEl = document.createElement("span");
    nameEl.className = "author-name";
    nameEl.textContent = authorName(a);
    li.appendChild(nameEl);

    const affs = authorAffiliations(a);
    if (affs.length) {
      const affEl = document.createElement("span");
      affEl.className = "author-affil";
      affEl.textContent = affs.join("; ");
      affEl.title = affs.join("\n");
      li.appendChild(affEl);
    }
    ul.appendChild(li);
  }
  if (truncated) {
    const li = document.createElement("li");
    li.className = "author author-more";
    li.textContent = "…and others";
    ul.appendChild(li);
  }
  return ul;
}

function renderPaper(paper, source) {
  // Hide the placeholder card for cites/pasted-html paths — the results
  // header already says "X citing papers" and the placeholder title there
  // is meaningless ("Cited-by results for N cluster ID(s)").
  if (!paper || !paper.authors || paper.authors.length === 0) {
    paperBox.hidden = true;
    return;
  }
  paperBox.hidden = false;
  paperTitleEl.textContent = paper.title || "(untitled)";
  const bits = [];
  bits.push(formatAuthors(paper.authors, paper.authors_truncated));
  if (paper.year) bits.push(paper.year);
  if (paper.venue) bits.push(paper.venue);
  paperMetaEl.textContent = bits.join(" · ");
}

function renderCitations(citations) {
  resultsList.innerHTML = "";
  for (const c of citations) {
    const li = document.createElement("li");

    const titleEl = document.createElement("a");
    titleEl.className = "title";
    titleEl.textContent = c.title;
    if (c.url) {
      titleEl.href = c.url;
      titleEl.target = "_blank";
      titleEl.rel = "noreferrer";
    }
    li.appendChild(titleEl);

    const authorsEl = document.createElement("div");
    authorsEl.className = "authors";
    authorsEl.appendChild(buildAuthorsNode(c.authors, c.authors_truncated));
    li.appendChild(authorsEl);

    const metaBits = [];
    if (c.year) metaBits.push(c.year);
    if (c.venue) metaBits.push(c.venue);
    if (metaBits.length) {
      const metaEl = document.createElement("div");
      metaEl.className = "meta";
      metaEl.textContent = metaBits.join(" · ");
      li.appendChild(metaEl);
    }

    resultsList.appendChild(li);
  }
  countEl.textContent = citations.length.toString();
  resultsBox.hidden = false;
}

function setTotalSuffix(total, count) {
  if (!totalSuffixEl) return;
  if (total && total > count) {
    totalSuffixEl.textContent = `(of ${total} on Google Scholar)`;
  } else {
    totalSuffixEl.textContent = "";
  }
}

function blockedHelpText(data) {
  const countText = data.total
    ? `Google Scholar found ${data.total} citing papers, but blocked the app before it could list them.`
    : "Google Scholar blocked the app before it could list citing papers.";
  return [
    countText,
    "This app uses SerpApi for blocked Scholar pages. Check that SERPAPI_KEY is set in the server environment, the Flask server was restarted after setting it, and the SerpApi account has remaining searches.",
  ].join("\n");
}

function showBlockedHelp(data) {
  setStatus(blockedHelpText(data), "error");
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  for (const button of presetButtons) {
    button.disabled = isLoading;
  }
}

async function fetchCitations(activePreset = null) {
  const title = titleInput.value.trim();
  setStatus(
    title ? `Looking up "${title}" and fetching citations...` : "Looking up paper and fetching citations...",
    "info",
  );
  paperBox.hidden = true;
  resultsBox.hidden = true;
  setLoading(true);
  if (activePreset) activePreset.setAttribute("aria-busy", "true");

  const body = {
    url: urlInput.value.trim(),
    title,
  };

  try {
    const res = await fetch("/api/citations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (!res.ok) {
      setStatus(data.error || `Request failed (${res.status})`, "error");
      if (data.needs_title) titleFallback.open = true;
      return;
    }

    setStatus("");
    renderPaper(data.paper, data.source);
    renderCitations(data.citations);
    setTotalSuffix(data.total, data.count);
    if (data.blocked) {
      showBlockedHelp(data);
    } else if (data.warning) {
      setStatus(data.warning, data.blocked ? "error" : "info");
    } else if (data.total && data.count < data.total) {
      setStatus(
        `Showing ${data.count} of ${data.total} reported by Google Scholar.`,
        "info",
      );
    } else if (data.count === 0) {
      setStatus(
        "Returned 0 citations. The paper may not be indexed, or GS blocked the scraper.",
        "info",
      );
    }
  } catch (err) {
    setStatus(`Network error: ${err.message}`, "error");
  } finally {
    if (activePreset) activePreset.removeAttribute("aria-busy");
    setLoading(false);
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  await fetchCitations();
});

for (const button of presetButtons) {
  button.addEventListener("click", async () => {
    titleInput.value = button.dataset.title || "";
    urlInput.value = button.dataset.url || "";
    titleFallback.open = !button.dataset.url;
    await fetchCitations(button);
  });
}
