// Word-search tab: same underlying data as the graph explorer
// (/api/search/<period>/<word>, backed by the same per-period network and
// community files) but rendered as a plain list instead of a node-link
// diagram - a second interface over one dataset, not a second pipeline.
// We don't yet know which of the two reads better for a historian's own
// workflow, hence both existing side by side off the same portada.

const form = document.getElementById("search-form");
const inputEl = document.getElementById("search-input");
const periodSelect = document.getElementById("search-period");
const statusEl = document.getElementById("search-status");
const resultsEl = document.getElementById("search-results");
const wordHeadingEl = document.getElementById("search-word-heading");
const wordMetaEl = document.getElementById("search-word-meta");
const neighborsBody = document.getElementById("neighbors-body");

const SEED_PERIOD = document.body.dataset.seedPeriod;
const SEED_WORD = document.body.dataset.seedWord;

let periods = [];
const wordPeriodsCache = new Map();

// Runs once at load - the mixed-fraction is a property of the whole
// labeling pass (all 308 communities), not of whatever word is on screen.
async function loadLabelCaveat() {
  const labelCaveatText = document.getElementById("label-caveat-text");
  if (!labelCaveatText) return;
  const res = await fetch("/api/label-caveat");
  const data = await res.json();
  labelCaveatText.textContent =
    `Each community name was written by reading its 25 most-connected words once - a reading aid, not a checked ` +
    `taxonomy. ${data.n_mixed} of ${data.n_total} communities (${data.mixed_pct}%) got flagged "(mixed)" during that ` +
    `read-through, meaning the words shared a grammatical pattern (verb forms, comparatives, name-like words, OCR ` +
    `noise) rather than an obvious topic - this corpus mixes English with Latin, French, Welsh, and other languages, ` +
    `and the clustering can pick up on shared form as easily as shared meaning. So "(mixed)" is a real warning; no ` +
    `"(mixed)" tag is a better sign, but still not a guarantee the name is right.`;
}
loadLabelCaveat();

async function loadPeriods() {
  const res = await fetch("/api/periods");
  periods = await res.json();
  periodSelect.innerHTML = "";
  periods.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.label;
    opt.textContent = p.label + (p.has_data ? "" : " (no data)");
    opt.disabled = !p.has_data;
    periodSelect.appendChild(opt);
  });
  const seedIndex = periods.findIndex((p) => p.label === SEED_PERIOD && p.has_data);
  periodSelect.value = seedIndex >= 0 ? SEED_PERIOD : (periods.find((p) => p.has_data) || {}).label || "";

  if (SEED_WORD) {
    inputEl.value = SEED_WORD;
    runSearch(SEED_WORD, periodSelect.value);
  }
}

async function getWordPeriods(word) {
  if (wordPeriodsCache.has(word)) return wordPeriodsCache.get(word);
  const res = await fetch(`/api/word-periods/${encodeURIComponent(word)}`);
  const data = await res.json();
  wordPeriodsCache.set(word, data.periods);
  return data.periods;
}

form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  runSearch(inputEl.value, periodSelect.value);
});

async function runSearch(rawWord, periodLabel) {
  const word = rawWord.trim().toLowerCase();
  if (!word) {
    statusEl.textContent = "Type a word to search.";
    resultsEl.classList.add("hidden");
    return;
  }
  inputEl.value = word;

  statusEl.textContent = `Searching "${word}" in ${periodLabel}...`;
  resultsEl.classList.add("hidden");

  let res = await fetch(`/api/search/${encodeURIComponent(periodLabel)}/${encodeURIComponent(word)}`);
  let data = await res.json();

  if (!data.found) {
    const wordPeriods = await getWordPeriods(word);
    if (!wordPeriods.length) {
      statusEl.textContent = `"${word}" doesn't appear in any period of this corpus. Try another word.`;
      return;
    }
    if (!wordPeriods.includes(periodLabel)) {
      periodLabel = wordPeriods[0];
      periodSelect.value = periodLabel;
      statusEl.textContent = `"${word}" isn't in the period you had selected - jumped to ${periodLabel}, its earliest appearance.`;
      res = await fetch(`/api/search/${encodeURIComponent(periodLabel)}/${encodeURIComponent(word)}`);
      data = await res.json();
      if (!data.found) {
        statusEl.textContent = `"${word}" could not be looked up in ${periodLabel}. Try another word.`;
        return;
      }
    }
  }

  statusEl.textContent = "";
  renderResults(data);
}

// Communities are numbered arbitrarily by Leiden - "#3" means nothing to a
// historian. Where a Claude-assigned theme exists (see webapp/app.py's
// get_labels - a reading aid over each community's top words, not a
// validated taxonomy) show it with the number kept alongside for anyone
// cross-referencing the underlying data.
function formatCommunity(id, label) {
  return label ? `${label} (#${id})` : `#${id}`;
}

function renderResults(data) {
  resultsEl.classList.remove("hidden");
  wordHeadingEl.textContent = `"${data.word}" in ${data.period}`;

  let changeHtml;
  if (data.prev_period === null || data.prev_period === undefined) {
    changeHtml = `<span class="muted">no earlier period with data to compare</span>`;
  } else if (data.prev_community === null || data.prev_community === undefined) {
    changeHtml = `<span class="muted">not present in ${data.prev_period}</span>`;
  } else if (data.changed_community) {
    changeHtml = `<strong class="flag-changed">changed community</strong> since ${data.prev_period} ` +
      `(was ${formatCommunity(data.prev_community, data.prev_community_label)})`;
  } else {
    changeHtml = `<strong class="flag-stable">same community</strong> as ${data.prev_period}`;
  }

  wordMetaEl.innerHTML = `
    <dt>Community</dt><dd>${formatCommunity(data.community, data.community_label)}</dd>
    <dt>Degree</dt><dd>${data.degree} connections within its top-15 neighbours</dd>
    <dt>Since previous period</dt><dd>${changeHtml}</dd>
  `;

  neighborsBody.innerHTML = "";
  data.neighbors.forEach((n) => {
    const tr = document.createElement("tr");
    if (n.community === data.community) tr.classList.add("same-community");
    const wordTd = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "word-link";
    btn.textContent = n.word;
    btn.addEventListener("click", () => runSearch(n.word, periodSelect.value));
    wordTd.appendChild(btn);
    const simTd = document.createElement("td");
    simTd.textContent = n.similarity.toFixed(3);
    const commTd = document.createElement("td");
    commTd.textContent = formatCommunity(n.community, n.community_label);
    tr.append(wordTd, simTd, commTd);
    neighborsBody.appendChild(tr);
  });
}

periodSelect.addEventListener("change", () => {
  if (inputEl.value.trim()) runSearch(inputEl.value, periodSelect.value);
});

loadPeriods();
