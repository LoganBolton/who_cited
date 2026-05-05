const form = document.getElementById("search-form");
const urlInput = document.getElementById("url");
const titleInput = document.getElementById("title");
const titleFallback = document.getElementById("title-fallback");
const cookiesInput = document.getElementById("cookies");
const htmlInput = document.getElementById("html");
const submitBtn = document.getElementById("submit-btn");
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

function formatAuthors(authors) {
  if (!authors || authors.length === 0) return "(unknown authors)";
  if (authors.length <= 6) return authors.join(", ");
  return authors.slice(0, 6).join(", ") + ` et al. (+${authors.length - 6})`;
}

function renderPaper(paper) {
  paperBox.hidden = false;
  paperTitleEl.textContent = paper.title || "(untitled)";
  const bits = [];
  if (paper.authors && paper.authors.length) bits.push(formatAuthors(paper.authors));
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
    authorsEl.textContent = formatAuthors(c.authors);
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

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  setStatus("Looking up paper and fetching citations…", "info");
  paperBox.hidden = true;
  resultsBox.hidden = true;
  submitBtn.disabled = true;

  const body = {
    url: urlInput.value.trim(),
    title: titleInput.value.trim(),
    cookies: cookiesInput ? cookiesInput.value.trim() : "",
    html: htmlInput ? htmlInput.value.trim() : "",
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
    renderPaper(data.paper);
    renderCitations(data.citations);
    setTotalSuffix(data.total, data.count);
    if (data.warning) {
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
    submitBtn.disabled = false;
  }
});
