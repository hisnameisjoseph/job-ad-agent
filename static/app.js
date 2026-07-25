// Fetch scored jobs and render the table. Uses DOM APIs (textContent) rather
// than innerHTML so job text can never inject markup.

const tierOf = (s) => (s >= 70 ? "strong" : s >= 45 ? "moderate" : "weak");

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function reqList(title, items) {
  const wrap = document.createElement("div");
  wrap.appendChild(el("h4", title));
  const ul = document.createElement("ul");
  if (items && items.length) {
    items.forEach((it) => ul.appendChild(el("li", it)));
  } else {
    ul.appendChild(el("li", "\u2014")); // em dash placeholder
  }
  wrap.appendChild(ul);
  return wrap;
}

function externalLink(url, text, className) {
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener";
  a.className = className;
  a.textContent = text;
  return a;
}

function renderRow(item) {
  const j = item.job;
  const s = item.score;
  const tr = document.createElement("tr");
  tr.classList.add("tier-" + tierOf(s.fit_score));

  // Fit score
  const scoreTd = el("td", null, "col-score");
  scoreTd.appendChild(el("span", s.fit_score, "score"));
  tr.appendChild(scoreTd);

  // Role: title (links to posting) + one-line reason + expandable breakdown
  const roleTd = document.createElement("td");
  if (j.url) {
    roleTd.appendChild(externalLink(j.url, j.title || "Untitled role", "role-title"));
  } else {
    roleTd.appendChild(el("span", j.title || "Untitled role", "role-title"));
  }
  roleTd.appendChild(el("p", s.one_line, "why"));

  const details = document.createElement("details");
  details.className = "breakdown";
  details.appendChild(el("summary", "Requirement breakdown"));
  const grid = el("div", null, "grid");
  grid.appendChild(reqList("Meets", s.met_requirements));
  grid.appendChild(reqList("Partial", s.partial_requirements));
  grid.appendChild(reqList("Missing", s.missing_requirements));
  details.appendChild(grid);
  roleTd.appendChild(details);
  tr.appendChild(roleTd);

  tr.appendChild(el("td", j.company || "\u2014"));
  tr.appendChild(el("td", j.location || "\u2014"));
  tr.appendChild(el("td", s.recommendation, "rec"));

  // Explicit posting link per row
  const linkTd = document.createElement("td");
  if (j.url) {
    linkTd.appendChild(externalLink(j.url, "Open \u2197", "posting-link"));
  } else {
    linkTd.textContent = "\u2014";
  }
  tr.appendChild(linkTd);

  return tr;
}

async function load() {
  const rows = document.getElementById("rows");
  const count = document.getElementById("count");
  const empty = document.getElementById("empty");

  try {
    const res = await fetch("/api/jobs");
    const data = await res.json();
    rows.replaceChildren();

    if (!data.length) {
      count.textContent = "0 matches";
      empty.hidden = false;
      return;
    }

    empty.hidden = true;
    count.textContent = data.length + (data.length === 1 ? " match" : " matches");
    data.forEach((item) => rows.appendChild(renderRow(item)));
  } catch (err) {
    count.textContent = "Could not load jobs. Is the server running and the scoring finished?";
  }
}

load();