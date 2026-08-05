// Word-search tab: same underlying data as the graph explorer
// (/api/search/<period>/<word>, backed by the same per-period network and
// community files) but rendered as a plain list instead of a node-link
// diagram - a second interface over one dataset, not a second pipeline.
// We don't yet know which of the two reads better for a historian's own
// workflow, hence both existing side by side off the same home page.
//
// The actual fetch + render logic lives in word_detail.js (loaded before
// this file - see search.html), shared with /timeline's per-period
// drill-in so the two views can never quietly drift apart.

const form = document.getElementById("search-form");
const inputEl = document.getElementById("search-input");
const periodSelect = document.getElementById("search-period");
const statusEl = document.getElementById("search-status");
const resultsEl = document.getElementById("search-results");
const wordHeadingEl = document.getElementById("search-word-heading");
const wordMetaEl = document.getElementById("search-word-meta");
const neighborsBody = document.getElementById("neighbors-body");
const regionRow = document.getElementById("region-row");
const regionToggleEl = document.getElementById("region-toggle");

const SEED_PERIOD = document.body.dataset.seedPeriod;
const SEED_WORD = document.body.dataset.seedWord;

let periods = [];
let REGIONS = [];
let activeRegion = null; // null = combined; otherwise one of REGIONS
const wordPeriodsCache = new Map();

function periodHasData(p) {
  return activeRegion ? p.regions.includes(activeRegion) : p.has_data;
}

async function loadRegions() {
  const res = await fetch("/api/regions");
  REGIONS = await res.json();
  renderRegionToggle();
}

function renderRegionToggle() {
  if (!REGIONS.length) {
    regionRow.style.display = "none";
    return;
  }
  regionRow.style.display = "";
  regionToggleEl.innerHTML = "";
  const options = [{ value: null, label: "Combined" }]
    .concat(REGIONS.map((r) => ({ value: r, label: r.charAt(0).toUpperCase() + r.slice(1) })));
  options.forEach((opt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "region-btn" + (activeRegion === opt.value ? " active" : "");
    btn.textContent = opt.label;
    btn.addEventListener("click", () => setRegion(opt.value));
    regionToggleEl.appendChild(btn);
  });
}

function setRegion(region) {
  if (region === activeRegion) return;
  activeRegion = region;
  renderRegionToggle();
  renderPeriodOptions();
  if (inputEl.value.trim()) runSearch(inputEl.value, periodSelect.value);
}

function renderPeriodOptions() {
  const current = periodSelect.value;
  periodSelect.innerHTML = "";
  periods.forEach((p) => {
    const has = periodHasData(p);
    const opt = document.createElement("option");
    opt.value = p.label;
    opt.textContent = p.label + (has ? "" : " (no data)");
    opt.disabled = !has;
    periodSelect.appendChild(opt);
  });
  const stillValid = periods.find((p) => p.label === current && periodHasData(p));
  periodSelect.value = stillValid ? current : (periods.find((p) => periodHasData(p)) || {}).label || "";
}

// Runs once at load - the "no clear subject" fraction is a property of the
// whole labeling pass, not of whatever word is on screen. Always visible
// (not a collapsed disclosure) - it governs how skeptically every group
// name below should be read, per the vault's wiki/labeling-pipeline.md.
async function loadLabelCaveat() {
  const labelCaveatText = document.getElementById("label-caveat-text");
  if (!labelCaveatText) return;
  const res = await fetch("/api/label-caveat");
  const data = await res.json();
  labelCaveatText.textContent =
    `Each group's name was written by reading its 25 most-connected words once - a reading aid, not a checked ` +
    `taxonomy. ${data.n_mixed} of ${data.n_total} groups (${data.mixed_pct}%) got flagged as having no clear ` +
    `subject during that read-through: the words shared a grammatical pattern (verb forms, comparatives, ` +
    `name-like words, OCR noise) rather than an obvious topic - this corpus mixes English with Latin, French, ` +
    `Welsh, and other languages, and the underlying clustering can pick up on shared form as easily as shared ` +
    `meaning. A named subject is a better sign than "no clear subject", but still not a guarantee the name is right.`;
}
loadLabelCaveat();

async function loadPeriods() {
  const res = await fetch("/api/periods");
  periods = await res.json();
  await loadRegions();
  renderPeriodOptions();
  const seedIndex = periods.findIndex((p) => p.label === SEED_PERIOD && periodHasData(p));
  if (seedIndex >= 0) periodSelect.value = SEED_PERIOD;

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

  let data = await WordDetail.fetchDetail(periodLabel, word, activeRegion);

  if (!data.found) {
    // word-periods is combined-only (see webapp/app.py) - a fine fallback
    // even in region mode, it just isn't guaranteed to land on a period the
    // active region also has data for; the second fetch below still checks.
    const wordPeriods = await getWordPeriods(word);
    if (!wordPeriods.length) {
      statusEl.textContent = `"${word}" doesn't appear in any period of this corpus. Try another word.`;
      return;
    }
    if (!wordPeriods.includes(periodLabel)) {
      periodLabel = wordPeriods[0];
      periodSelect.value = periodLabel;
      const regionNote = activeRegion ? ` (${activeRegion})` : "";
      statusEl.textContent = `"${word}" isn't in the period you had selected - jumped to ${periodLabel}${regionNote}, its earliest appearance.`;
      data = await WordDetail.fetchDetail(periodLabel, word, activeRegion);
      if (!data.found) {
        const regionSuffix = activeRegion ? ` for the ${activeRegion} source` : "";
        statusEl.textContent = `"${word}" could not be looked up in ${periodLabel}${regionSuffix}. Try another word.`;
        return;
      }
    }
  }

  statusEl.textContent = "";
  renderResults(data);
}

function renderResults(data) {
  resultsEl.classList.remove("hidden");
  WordDetail.render(
    { heading: wordHeadingEl, meta: wordMetaEl, neighborsBody },
    data,
    (word) => runSearch(word, periodSelect.value),
  );
}

periodSelect.addEventListener("change", () => {
  if (inputEl.value.trim()) runSearch(inputEl.value, periodSelect.value);
});

loadPeriods();
