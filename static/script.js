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
const peopleFilter = document.getElementById("people-filter");
const peopleBody = document.getElementById("people-body");
const countEl = document.getElementById("count");
const paperCountEl = document.getElementById("paper-count");
const visibleCountEl = document.getElementById("visible-count");
const totalSuffixEl = document.getElementById("total-suffix");
let personRows = [];
let loadingTimer = null;
let loadingStartedAt = 0;
let loadingContext = null;

function setStatus(text, kind = "info") {
  stopLoadingProgress();
  if (!text) {
    statusBox.hidden = true;
    statusBox.textContent = "";
    return;
  }
  statusBox.hidden = false;
  statusBox.className = `status ${kind}`;
  statusBox.textContent = text;
}

function stopLoadingProgress() {
  if (loadingTimer) {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }
}

function loadingSteps(context) {
  const resolveLabel = context.url
    ? "Resolving Google Scholar citation cluster"
    : "Finding the paper on Semantic Scholar";
  return [
    {
      at: 0,
      label: "Preparing request",
      detail: context.title ? `Search target: ${context.title}` : "Using the Scholar URL from the form.",
    },
    {
      at: 1.2,
      label: resolveLabel,
      detail: context.url
        ? "If this is a profile article page, the server extracts its Cited by link first."
        : "The server looks up the title and gets the paper identifier before fetching citations.",
    },
    {
      at: 3,
      label: "Fetching citing papers",
      detail: "SerpApi is used when configured; otherwise the server tries direct Scholar fetching and reports blocks.",
    },
    {
      at: 6,
      label: "Enriching author affiliations",
      detail: "OpenAlex is checked for institutions attached to authors on the citing papers.",
    },
    {
      at: 9,
      label: "Preparing spreadsheet rows",
      detail: "Each cited author becomes one searchable table row when the server response returns.",
    },
  ];
}

function stepState(step, index, elapsed, steps) {
  const next = steps[index + 1];
  if (elapsed >= step.at && (!next || elapsed < next.at)) return "current";
  if (elapsed >= step.at) return "done";
  return "queued";
}

function renderLoadingProgress() {
  if (!loadingContext) return;
  const elapsed = (Date.now() - loadingStartedAt) / 1000;
  const steps = loadingSteps(loadingContext);

  statusBox.hidden = false;
  statusBox.className = "status loading";
  statusBox.textContent = "";

  const header = document.createElement("div");
  header.className = "loading-head";

  const title = document.createElement("div");
  title.className = "loading-title";
  title.textContent = "Working on citations";
  header.appendChild(title);

  const time = document.createElement("div");
  time.className = "loading-time";
  time.textContent = `${Math.floor(elapsed)}s elapsed`;
  header.appendChild(time);
  statusBox.appendChild(header);

  const list = document.createElement("ol");
  list.className = "loading-steps";
  steps.forEach((step, index) => {
    const li = document.createElement("li");
    li.className = stepState(step, index, elapsed, steps);

    const label = document.createElement("span");
    label.className = "loading-step-label";
    label.textContent = step.label;
    li.appendChild(label);

    const detail = document.createElement("span");
    detail.className = "loading-step-detail";
    detail.textContent = step.detail;
    li.appendChild(detail);
    list.appendChild(li);
  });
  statusBox.appendChild(list);

  const note = document.createElement("p");
  note.className = "loading-note";
  note.textContent = "Progress updates here are based on the server workflow. Exact paper and author counts appear when the API response finishes.";
  statusBox.appendChild(note);
}

function startLoadingProgress(context) {
  stopLoadingProgress();
  loadingContext = context;
  loadingStartedAt = Date.now();
  renderLoadingProgress();
  loadingTimer = setInterval(renderLoadingProgress, 1000);
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

function citationToPersonRows(citation, paperIndex) {
  const authors = citation.authors && citation.authors.length ? citation.authors : [null];
  const rows = authors.map((author, authorIndex) => {
    const affiliations = authorAffiliations(author);
    const affiliationText = affiliations.length ? affiliations.join("; ") : "";
    const name = authorName(author) || "(unknown author)";
    const row = {
      name,
      affiliation: affiliationText,
      title: citation.title || "(untitled)",
      url: citation.url || "",
      year: citation.year || "",
      venue: citation.venue || "",
      paperIndex,
      authorIndex,
    };
    row.searchText = [
      row.name,
      row.affiliation,
      row.title,
      row.year,
      row.venue,
    ].join(" ").toLowerCase();
    return row;
  });
  if (citation.authors_truncated) {
    rows.push({
      name: "and others",
      affiliation: "",
      title: citation.title || "(untitled)",
      url: citation.url || "",
      year: citation.year || "",
      venue: citation.venue || "",
      paperIndex,
      authorIndex: rows.length,
      searchText: ["and others", citation.title, citation.year, citation.venue].join(" ").toLowerCase(),
    });
  }
  return rows;
}

function makeCell(text, className = "") {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = text || "";
  return td;
}

function makePaperCell(row) {
  const td = document.createElement("td");
  td.className = "paper-cell";
  if (row.url) {
    const a = document.createElement("a");
    a.href = row.url;
    a.target = "_blank";
    a.rel = "noreferrer";
    a.textContent = row.title;
    td.appendChild(a);
  } else {
    td.textContent = row.title;
  }
  return td;
}

function renderPersonRows(rows) {
  peopleBody.innerHTML = "";
  const fragment = document.createDocumentFragment();
  if (rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.className = "muted-cell empty-table-cell";
    td.colSpan = 5;
    td.textContent = "No people match this filter.";
    tr.appendChild(td);
    fragment.appendChild(tr);
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.appendChild(makeCell(row.name, "person-cell"));
    tr.appendChild(makeCell(row.affiliation || "-", row.affiliation ? "affiliation-cell" : "affiliation-cell muted-cell"));
    tr.appendChild(makePaperCell(row));
    tr.appendChild(makeCell(row.year, "year-cell"));
    tr.appendChild(makeCell(row.venue, "venue-cell"));
    fragment.appendChild(tr);
  }
  peopleBody.appendChild(fragment);
  visibleCountEl.textContent = rows.length === personRows.length
    ? `${rows.length} rows`
    : `${rows.length} of ${personRows.length} rows`;
}

function applyPeopleFilter() {
  const query = (peopleFilter.value || "").trim().toLowerCase();
  const filtered = query
    ? personRows.filter((row) => row.searchText.includes(query))
    : personRows;
  renderPersonRows(filtered);
}

function renderCitations(citations) {
  personRows = citations.flatMap((citation, index) => citationToPersonRows(citation, index));
  if (peopleFilter) peopleFilter.value = "";
  countEl.textContent = personRows.length.toString();
  paperCountEl.textContent = `${citations.length} citing ${citations.length === 1 ? "paper" : "papers"}`;
  renderPersonRows(personRows);
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
  paperBox.hidden = true;
  resultsBox.hidden = true;
  personRows = [];
  setLoading(true);
  if (activePreset) activePreset.setAttribute("aria-busy", "true");

  const body = {
    url: urlInput.value.trim(),
    title,
  };
  startLoadingProgress(body);

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
    stopLoadingProgress();
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

if (peopleFilter) {
  peopleFilter.addEventListener("input", applyPeopleFilter);
}
