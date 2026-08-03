// Timeline tab: one word, tracked across every configured period, backed by
// a single /api/timeline/<word> call (see webapp/app.py) rather than one
// /api/search round-trip per period. See the vault's
// wiki/timeline-feature-plan.md for why this replaced the graph explorer as
// the primary interface - the 2026-07-29 meeting's most repeated,
// most concrete ask was "show which community a word belonged to, across
// periods", not a force-directed diagram.

const form = document.getElementById("timeline-form");
const inputEl = document.getElementById("timeline-input");
const statusEl = document.getElementById("timeline-status");
const resultsEl = document.getElementById("timeline-results");
const trackEl = document.getElementById("timeline-track");
const regionRow = document.getElementById("region-row");
const regionToggleEl = document.getElementById("region-toggle");
const laneLegendEl = document.getElementById("lane-legend");
const notesEl = document.getElementById("timeline-notes");
const seamNoteEl = document.getElementById("corpus-seam-note");
const drilldownEl = document.getElementById("drilldown");
const drilldownHeadingEl = document.getElementById("drilldown-heading");
const drilldownMetaEl = document.getElementById("drilldown-meta");
const drilldownNeighborsBody = document.getElementById("drilldown-neighbors-body");
const drilldownCloseBtn = document.getElementById("drilldown-close");

const SEED_WORD = document.body.dataset.seedWord;

// Search boxes are expected to behave like search boxes: clicking in to
// search again should let a new word fully replace the old one, not insert
// into it. Without this, clicking in and typing over the seeded/previous
// word appends instead of replacing (e.g. "system" -> "systemsystem").
inputEl.addEventListener("focus", () => inputEl.select());

let REGIONS = [];
let activeRegion = null; // null = combined; otherwise one of REGIONS

// One fixed color per lane (see the vault's wiki/labeling-pipeline.md for
// how this 15-lane taxonomy was derived from the 707 labels already
// generated). Validated with the dataviz skill's palette validator against
// this page's own paper background (#F1EDE3): all 15 clear the OKLCH
// lightness/chroma/CVD-separation checks; a few clear contrast only at the
// WARN tier, which is legal specifically because color here is always
// secondary to the community-label text next to it, never the sole carrier
// of identity. "Structural / Uncertain" deliberately breaks the pattern and
// uses the site's plain muted gray instead of a 15th hue - it marks the
// *absence* of a real topic, not a topic of its own, and should read as a
// flag, not as just another category.
const LANE_COLORS = {
  "Government, Law & Administration": "#a6243e",
  "Religion, Theology & the Church": "#009bc5",
  "Morality: Virtue & Vice": "#6e40ab",
  "Medicine, Body & Health": "#c7700b",
  "Science, Mathematics & Natural Philosophy": "#962c7a",
  "Nature, Landscape & Weather": "#6b9a2e",
  "Military & Warfare": "#1c58b8",
  "Trade, Finance & Commerce": "#00a3a4",
  "History, Genealogy, Nobility & Kinship": "#d25f6c",
  "Rhetoric & Persuasion": "#006e9d",
  "Literature, Drama & Poetic Diction": "#9772d3",
  "Domestic Life, Dress & Household": "#9c3c00",
  "Geography & Territory": "#c162a4",
  "Books, Learning & Scholarship": "#3c6e00",
  "Structural / Uncertain": "var(--muted)",
};
const LANE_ORDER = Object.keys(LANE_COLORS);

function laneColor(lane) {
  return LANE_COLORS[lane] || "var(--muted)";
}

// True when a card's community is a known non-topical grouping - either the
// new pipeline's own "Structural / Uncertain" lane, or (for regions/periods
// still on the older, pre-lane label files) the "(mixed)" tag the original
// ad hoc labeling pass wrote into the label text itself. See the vault's
// wiki/labeling-pipeline.md for why both signals exist side by side.
function isUncertain(p) {
  return p.lane === "Structural / Uncertain" || (p.community_label || "").toLowerCase().includes("(mixed)");
}

// Findings specific enough that stating them generically (e.g. "flag any
// two words that always share a community") would be a bigger, unrequested
// feature - see wiki/timeline-feature-plan.md: "system" was chosen as the
// demo word *despite* sharing a community with "reason" at every one of the
// 7 swept resolutions, in every period both are present - a genuine,
// verified property of this network, stated openly rather than picked
// around. Extend this map if another such finding gets documented.
const KNOWN_COLLISIONS = {
  system: "“system” shares a community with “reason” at every resolution in the sweep (0.1–2.0), in every period both appear - a genuine property of this network, not a display artifact. Below, “system” moves into a community together with “reason”; it does not separate out on its own.",
};

function renderNotes(word) {
  notesEl.innerHTML = "";
  const note = KNOWN_COLLISIONS[word];
  if (!note) return;
  const div = document.createElement("div");
  div.className = "timeline-note";
  div.textContent = note;
  notesEl.appendChild(div);
}

// Deliberately named, not threshold-detected: TCP's own construction has
// exactly one *structural* seam (EEBO-TCP's coverage ends at 1700, ECCO-TCP
// and Evans-TCP phase in from 1700 on - see wiki/timeline-feature-plan.md's
// "Other data findings" and docs/method.tex's Corpus section), which reads
// differently from the separate, gradual thinning of ECCO-TCP after 1780
// (already covered by its own "Corpus coverage" caveat elsewhere in this
// app) - a generic "biggest vocabulary drop" detector would sometimes flag
// the wrong one of those two, or both, and imply the same cause for both.
// The real vocabulary numbers themselves are never hardcoded - always read
// off this response's own vocab_size, so this stays accurate if the corpus
// is rebuilt.
const CORPUS_SEAM_BOUNDARY_YEAR = "1700";

function findCorpusSeam(periods) {
  for (let i = 1; i < periods.length; i++) {
    const prev = periods[i - 1], curr = periods[i];
    if (prev.has_data && curr.has_data &&
        prev.period.endsWith(`-${CORPUS_SEAM_BOUNDARY_YEAR}`) &&
        curr.period.startsWith(`${CORPUS_SEAM_BOUNDARY_YEAR}-`)) {
      return { from: prev.period, to: curr.period, fromVocab: prev.vocab_size, toVocab: curr.vocab_size };
    }
  }
  return null;
}

function renderCorpusSeamNote(periods) {
  const seam = findCorpusSeam(periods);
  if (!seam) {
    seamNoteEl.classList.add("hidden");
    return;
  }
  seamNoteEl.classList.remove("hidden");
  seamNoteEl.textContent = `Corpus seam, not a semantic event: ${seam.from} → ${seam.to}, vocabulary drops from ` +
    `${seam.fromVocab.toLocaleString()} to ${seam.toVocab.toLocaleString()} words - EEBO-TCP's coverage ends at ` +
    `${CORPUS_SEAM_BOUNDARY_YEAR}, ECCO-TCP and Evans-TCP phase in from there. (The corpus keeps thinning further ` +
    `after 1780 too, gradually rather than as a single seam - see "corpus coverage" below.)`;
}

function renderLaneLegend() {
  laneLegendEl.innerHTML = "";
  LANE_ORDER.forEach((lane) => {
    const item = document.createElement("span");
    item.className = "lane-legend-item";
    item.innerHTML = `<span class="lane-dot" style="background:${laneColor(lane)}"></span>${lane}`;
    laneLegendEl.appendChild(item);
  });
}
renderLaneLegend();

async function loadLabelCaveat() {
  const el = document.getElementById("label-caveat-text");
  if (!el) return;
  const res = await fetch("/api/label-caveat");
  const data = await res.json();
  el.textContent =
    `Each community name was written by reading its 25 most-connected words once - a reading aid, not a checked ` +
    `taxonomy. ${data.n_mixed} of ${data.n_total} communities (${data.mixed_pct}%) got flagged "(mixed)" during that ` +
    `read-through and sorted into the "Structural / Uncertain" lane: the words shared a grammatical pattern (verb ` +
    `forms, comparatives, name-like words, OCR noise) rather than an obvious topic - this corpus mixes English with ` +
    `Latin, French, Welsh, and other languages, and the clustering can pick up on shared form as easily as shared ` +
    `meaning. A thematic lane is a better sign than "Structural / Uncertain", but still not a guarantee the name is right.`;
}
loadLabelCaveat();

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
  if (inputEl.value.trim()) runTimeline(inputEl.value);
}

form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  runTimeline(inputEl.value);
});

async function runTimeline(rawWord) {
  const word = rawWord.trim().toLowerCase();
  if (!word) {
    statusEl.textContent = "Type a word to see its timeline.";
    resultsEl.classList.add("hidden");
    return;
  }
  inputEl.value = word;
  statusEl.textContent = `Loading "${word}"...`;
  resultsEl.classList.add("hidden");

  const regionParam = activeRegion ? `?region=${encodeURIComponent(activeRegion)}` : "";
  const res = await fetch(`/api/timeline/${encodeURIComponent(word)}${regionParam}`);
  const data = await res.json();

  const nFound = data.periods.filter((p) => p.found).length;
  if (nFound === 0) {
    statusEl.textContent = `"${word}" doesn't appear in any period of this corpus` +
      (activeRegion ? ` for the ${activeRegion} source` : "") + `. Try another word.`;
    return;
  }

  statusEl.textContent = "";
  renderNotes(data.word);
  renderTimeline(data);
  renderCorpusSeamNote(data.periods);
}

// Communities are numbered arbitrarily by Leiden - "#3" alone means nothing
// to a historian. The plain-English label is the primary signal; the number
// stays alongside it for anyone cross-referencing the underlying data.
function formatCommunity(id, label) {
  return label ? `${label} (#${id})` : `#${id}`;
}

let selectedCardEl = null;

function closeDrilldown() {
  drilldownEl.classList.add("hidden");
  if (selectedCardEl) selectedCardEl.classList.remove("selected");
  selectedCardEl = null;
}
drilldownCloseBtn.addEventListener("click", closeDrilldown);

async function openDrilldown(period, word, cardEl) {
  if (selectedCardEl) selectedCardEl.classList.remove("selected");
  selectedCardEl = cardEl;
  cardEl.classList.add("selected");
  drilldownEl.classList.remove("hidden");
  drilldownHeadingEl.textContent = `Loading "${word}" in ${period}...`;
  const data = await WordDetail.fetchDetail(period, word, activeRegion);
  if (!data.found) {
    drilldownHeadingEl.textContent = `"${word}" in ${period}`;
    drilldownMetaEl.innerHTML = "";
    drilldownNeighborsBody.innerHTML = "";
    return;
  }
  WordDetail.render(
    { heading: drilldownHeadingEl, meta: drilldownMetaEl, neighborsBody: drilldownNeighborsBody },
    data,
    (newWord) => { closeDrilldown(); runTimeline(newWord); },
  );
}

function renderTimeline(data) {
  resultsEl.classList.remove("hidden");
  trackEl.innerHTML = "";
  closeDrilldown();

  let prevFoundCard = null;

  data.periods.forEach((p, i) => {
    const col = document.createElement("div");
    col.className = "timeline-col";

    const periodLabel = document.createElement("div");
    periodLabel.className = "timeline-period-label";
    periodLabel.textContent = p.period;
    col.appendChild(periodLabel);

    const card = document.createElement("div");

    if (!p.has_data) {
      card.className = "timeline-card timeline-gap";
      card.innerHTML = `<span class="timeline-card-note">no data</span>`;
    } else if (!p.found) {
      card.className = "timeline-card timeline-absent";
      card.innerHTML = `<span class="timeline-card-note">not in vocabulary</span>`;
    } else {
      card.className = "timeline-card timeline-found" + (isUncertain(p) ? " timeline-uncertain" : "");
      const dot = `<span class="lane-dot" style="background:${laneColor(p.lane)}" title="${p.lane || "no lane yet"}"></span>`;
      const label = p.community_label
        ? `${dot}<span class="timeline-community-label">${p.community_label}</span>`
        : `${dot}<span class="timeline-community-label muted">unlabeled</span>`;
      const meta = [`#${p.community_raw}`];
      if (p.n_words_in_community) meta.push(`${p.n_words_in_community.toLocaleString()} words`);
      card.innerHTML = `${label}<span class="timeline-card-meta">${meta.join(" &middot; ")}</span>`;
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `${data.word} in ${p.period}: ${p.community_label || "unlabeled community"}. Open full neighbourhood.`);
      const open = () => openDrilldown(p.period, data.word, card);
      card.addEventListener("click", open);
      card.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          open();
        }
      });

      if (prevFoundCard && i > 0) {
        const arrow = document.createElement("div");
        arrow.className = "timeline-arrow" +
          (p.changed_from_prev === true ? " changed" : p.changed_from_prev === false ? " stable" : "");
        arrow.title = p.changed_from_prev === true ? "community changed since the previous period with data"
          : p.changed_from_prev === false ? "same community as the previous period with data"
          : "not comparable to the previous period";
        col.insertBefore(arrow, periodLabel);
      }
      prevFoundCard = card;
    }

    col.appendChild(card);
    trackEl.appendChild(col);
  });
}

async function init() {
  await loadRegions();
  if (SEED_WORD) {
    inputEl.value = SEED_WORD;
    runTimeline(SEED_WORD);
  }
}
init();
