// Timeline tab: one word, tracked across every configured period, backed by
// a single /api/timeline/<word> call (see webapp/app.py) rather than one
// /api/search round-trip per period. See the vault's
// wiki/timeline-feature-plan.md for why this replaced the graph explorer as
// the primary interface - the 2026-07-29 meeting's most repeated,
// most concrete ask was "show which group a word belonged to, across
// periods", not a force-directed diagram.
//
// Vocabulary (2026-08-03, per an Opus-planned pass after Panch flagged that
// "community" and "lane" mean nothing without inside knowledge): the backend
// API and the on-disk label files still use "community"/"lane" - that data
// contract does not change here. This file is where those get translated to
// "group" and "subject area" for every string a person actually reads. One
// concept, one word, everywhere: the same translation happens in
// word_detail.js (shared with /search) and search.js.

const form = document.getElementById("timeline-form");
const inputEl = document.getElementById("timeline-input");
const statusEl = document.getElementById("timeline-status");
const resultsEl = document.getElementById("timeline-results");
const trackEl = document.getElementById("timeline-track");
const trackWrapEl = document.querySelector(".timeline-track-wrap");
const regionRow = document.getElementById("region-row");
const regionToggleEl = document.getElementById("region-toggle");
const subjectLegendEl = document.getElementById("subject-legend");
const notesEl = document.getElementById("timeline-notes");
const seamNoteEl = document.getElementById("corpus-seam-note");
const drilldownEl = document.getElementById("drilldown");
const drilldownHeadingEl = document.getElementById("drilldown-heading");
const drilldownMetaEl = document.getElementById("drilldown-meta");
const drilldownNeighborsBody = document.getElementById("drilldown-neighbors-body");
const drilldownCloseBtn = document.getElementById("drilldown-close");
const rangeFullBtn = document.getElementById("range-full");
const rangeSattelzeitBtn = document.getElementById("range-sattelzeit");
const verdictEl = document.getElementById("timeline-verdict");
const findingsHeadlineEl = document.getElementById("findings-headline");
const findingsCaveatTextEl = document.getElementById("findings-caveat-text");

const SEED_WORD = document.body.dataset.seedWord;

// Search boxes are expected to behave like search boxes: clicking in to
// search again should let a new word fully replace the old one, not insert
// into it. Without this, clicking in and typing over the seeded/previous
// word appends instead of replacing (e.g. "system" -> "systemsystem").
inputEl.addEventListener("focus", () => inputEl.select());

let REGIONS = [];
let activeRegion = null; // null = combined; otherwise one of REGIONS
let requestSeq = 0; // guards against a slow in-flight search resolving after a newer one

// Subject taxonomy (colors, short names, "no clear subject" detection) now
// lives once in word_detail.js (WordDetail.SUBJECTS etc) so the dot next to
// a group name means the same thing on /timeline and in the drill-in/
// /search tables - these are thin wrappers so the rest of this file reads
// the same as before the move.
const SUBJECT_ORDER = WordDetail.SUBJECT_ORDER;

function isUncertain(p) {
  return WordDetail.isUncertainLabel(p.community_label, p.lane);
}

function subjectShort(p) {
  return WordDetail.subjectShort(p.lane, p.community_label);
}

function subjectColor(p) {
  return WordDetail.subjectColor(p.lane, p.community_label);
}

// The "(mixed)" tag is still how compile()d label files flag a group as
// having no clear subject (see src/label_communities.py) - detected above,
// then stripped here so it never appears twice (once as the tag, once as
// the subject line already saying "No clear subject").
function groupName(p) {
  return (p.community_label || "").replace(/\s*\(mixed\)\s*$/i, "").trim();
}

// Findings specific enough that stating them generically (e.g. "flag any
// two words that always share a group") would be a bigger, unrequested
// feature - see wiki/timeline-feature-plan.md: "system" was chosen as the
// demo word *despite* sharing a group with "reason" at every one of the 7
// swept resolutions, in every period both are present - a genuine, verified
// property of this network, stated openly rather than picked around.
// Extend this map if another such finding gets documented.
const KNOWN_COLLISIONS = {
  system: "“system” shares a group with “reason” at every resolution in the sweep (0.1–2.0), in every period both appear - a genuine property of this network, not a display artifact. Below, “system” moves into a group together with “reason”; it does not separate out on its own.",
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
// differently from the separate, gradual thinning of ECCO-TCP after 1780.
// A generic "biggest vocabulary drop" detector would sometimes flag the
// wrong one of those two, or both, and imply the same cause for both. The
// real vocabulary numbers themselves are never hardcoded - always read off
// this response's own vocab_size, so this stays accurate if the corpus is
// rebuilt.
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
    `after 1780 too, gradually rather than as a single seam.)`;
}

function renderSubjectLegend() {
  subjectLegendEl.innerHTML = "";
  SUBJECT_ORDER.forEach((internalName) => {
    const s = WordDetail.SUBJECTS[internalName];
    const item = document.createElement("span");
    item.className = "lane-legend-item";
    item.innerHTML = `<span class="lane-dot" style="background:${s.color}"></span>${s.short}`;
    subjectLegendEl.appendChild(item);
  });
}
renderSubjectLegend();

async function loadLabelCaveat() {
  const el = document.getElementById("label-caveat-text");
  if (!el) return;
  const res = await fetch("/api/label-caveat");
  const data = await res.json();
  el.textContent =
    `Each group's name was written by reading its 25 most-connected words once - a reading aid, not a checked ` +
    `taxonomy. ${data.n_mixed} of ${data.n_total} groups (${data.mixed_pct}%) got flagged as having no clear ` +
    `subject during that read-through: the words shared a grammatical pattern (verb forms, comparatives, ` +
    `name-like words, OCR noise) rather than an obvious topic - this corpus mixes English with Latin, French, ` +
    `Welsh, and other languages, and the underlying clustering can pick up on shared form as easily as shared ` +
    `meaning. A named subject is a better sign than "No clear subject", but still not a guarantee the name is right.`;
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
  const mySeq = ++requestSeq; // a fast search started right after this one wins, not whichever fetch resolves first
  inputEl.value = word;
  statusEl.textContent = `Loading "${word}"...`;
  resultsEl.classList.add("hidden");

  const regionParam = activeRegion ? `?region=${encodeURIComponent(activeRegion)}` : "";
  const res = await fetch(`/api/timeline/${encodeURIComponent(word)}${regionParam}`);
  const data = await res.json();
  if (mySeq !== requestSeq) return; // a newer search superseded this one while it was in flight

  const nFound = data.periods.filter((p) => p.found).length;
  if (nFound === 0) {
    statusEl.textContent = `"${word}" doesn't appear in any period of this corpus` +
      (activeRegion ? ` for the ${activeRegion} source` : "") + `. Try another word.`;
    return;
  }

  statusEl.textContent = "";
  renderNotes(data.word);
  renderVerdict(data);
  renderTimeline(data);
  renderCorpusSeamNote(data.periods);
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
  // A real bug, not a nice-to-have: opening the drilldown used to leave the
  // viewport scroll position untouched, so on any normal-height screen it
  // rendered entirely below the fold - clicking a card visibly did nothing.
  drilldownEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
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

// A period label is "<start>-<end>" (20-year bins). The Sattelzeit band
// covers every bin that overlaps 1770-1830 at all, even partially - with
// 20-year bins that is 1760-1780 through 1820-1840, not a false-precision
// 1770/1830 cut a bin boundary can't actually represent.
const SATTELZEIT_START = 1770;
const SATTELZEIT_END = 1830;

function periodYears(label) {
  const [start, end] = label.split("-").map(Number);
  return { start, end };
}

function overlapsSattelzeit(label) {
  const { start, end } = periodYears(label);
  return start < SATTELZEIT_END && end > SATTELZEIT_START;
}

// Band 4 of the 2026-08-03 IA schema: the verdict, stated in one sentence,
// before any chart - the thing a reader who never looks at the strip still
// leaves knowing. Picks the transition that best answers the project's own
// question (did this word's group change during the Sattelzeit) rather
// than always describing the same fixed period pair, since different
// words move at different times or not at all.
function findVerdictTransition(periods) {
  const foundIdx = periods.map((_, i) => i).filter((i) => periods[i].found);
  if (foundIdx.length <= 1) return { type: "single", idx: foundIdx[0] };

  const changes = foundIdx.filter((i) => periods[i].changed_from_prev === true);
  if (!changes.length) {
    return { type: "stable", firstIdx: foundIdx[0], lastIdx: foundIdx[foundIdx.length - 1] };
  }
  const sattelzeitChanges = changes.filter((i) => overlapsSattelzeit(periods[i].period));
  const idx = sattelzeitChanges.length ? sattelzeitChanges[sattelzeitChanges.length - 1] : changes[changes.length - 1];
  return { type: "moved", idx, inSattelzeit: sattelzeitChanges.includes(idx) };
}

function renderVerdict(data) {
  const periods = data.periods;
  const v = findVerdictTransition(periods);
  let text;
  if (v.type === "single") {
    const p = periods[v.idx];
    text = `“${data.word}” appears in only one period (${p.period}), as ${groupName(p) || "an unlabeled group"} - not enough data to see it move.`;
  } else if (v.type === "stable") {
    const first = periods[v.firstIdx], last = periods[v.lastIdx];
    text = `“${data.word}” stayed in the same group, ${groupName(first) || "unlabeled"}, across every period it appears - ${first.period} through ${last.period}.`;
  } else {
    const curr = periods[v.idx];
    const prev = periods[v.idx - 1];
    const where = v.inSattelzeit ? "inside the Sattelzeit window (1770–1830)" : "outside the Sattelzeit window (1770–1830)";
    text = `“${data.word}” moved from ${groupName(prev) || "an unlabeled group"} into ${groupName(curr) || "an unlabeled group"} ` +
      `between ${prev.period} and ${curr.period}, ${where}.`;
  }
  verdictEl.textContent = text;
}

function renderTimeline(data) {
  resultsEl.classList.remove("hidden");
  trackEl.innerHTML = "";
  closeDrilldown();

  let prevFoundCard = null;
  let prevFoundName = null;

  data.periods.forEach((p, i) => {
    const col = document.createElement("div");
    col.className = "timeline-col";
    if (overlapsSattelzeit(p.period)) col.classList.add("in-sattelzeit");

    const arrowSlot = document.createElement("div");
    arrowSlot.className = "timeline-arrow-slot";
    col.appendChild(arrowSlot);

    const periodLabel = document.createElement("div");
    periodLabel.className = "timeline-period-label";
    periodLabel.textContent = p.period;
    col.appendChild(periodLabel);

    const card = document.createElement("div");

    if (!p.has_data) {
      card.className = "timeline-slot-empty";
      card.innerHTML = `<span class="timeline-card-note">no texts for this period</span>`;
    } else if (!p.found) {
      card.className = "timeline-card timeline-thin";
      card.innerHTML = `<span class="timeline-card-note">not in these texts</span>`;
    } else {
      card.className = "timeline-card timeline-found";
      // Same fact the arrow-between-columns already carries
      // (p.changed_from_prev), restated on the card itself as a
      // specimen-reclassification stamp - see the CSS comment on
      // .timeline-reclassified-stamp for why this is label-and-color,
      // not color alone.
      const reclassified = p.changed_from_prev === true;
      if (reclassified) card.classList.add("timeline-reclassified");
      const subject = subjectShort(p);
      const name = groupName(p) || "unlabeled";

      // A real finding from analyzing the full corpus (2026-08-04): among
      // every "stayed" transition, 57.6% still get a different label text
      // between the two periods, because each period's label is written
      // independently from just its own top-25 words - a common outcome,
      // not a rare edge case. First tried as its own line on the card
      // ("previously: X" / "same group, called X before") - Panch found
      // that visually cluttered and unfocused, reverted. The signal now
      // lives only in the arrow's own word (see below: "renamed" instead
      // of "stayed"), nothing added to the card face itself.
      const nameChanged = p.changed_from_prev !== null && prevFoundName && prevFoundName !== name;
      card.innerHTML = `
        ${reclassified ? '<span class="timeline-reclassified-stamp">Reclassified</span>' : ""}
        <span class="timeline-subject-line">
          <span class="lane-dot" style="background:${subjectColor(p)}"></span>${subject}
        </span>
        <span class="timeline-group-name">${name}</span>
        <span class="timeline-card-meta">No. ${p.community_raw}${p.n_words_in_community ? ` &middot; ${p.n_words_in_community.toLocaleString()} specimens` : ""}</span>
      `;
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `${data.word} in ${p.period}: ${name}, ${subject}. Open full neighbourhood.`);
      const open = () => openDrilldown(p.period, data.word, card);
      card.addEventListener("click", open);
      card.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          open();
        }
      });

      if (prevFoundCard && i > 0) {
        const state = p.changed_from_prev === true ? "changed" : p.changed_from_prev === false ? "stable" : "";
        if (state) {
          // Back to two words, not three (2026-08-04: a "renamed" third
          // state was tried and dropped - Panch found it confusing rather
          // than clarifying, on top of quietly redefining what "moved"
          // measures would corrupt the same align_communities() figure
          // the headline finding banner reports). moved/stayed is the
          // real, single signal migration_fraction is built on; the
          // "name changed but the group didn't" nuance lives only in this
          // tooltip, for whoever hovers, and never competes with the
          // two-word scan reading.
          const arrow = document.createElement("div");
          arrow.className = "timeline-arrow " + state;
          arrow.textContent = state === "changed" ? "moved" : "stayed";
          if (state === "changed") {
            arrow.title = "moved to a different group since the previous period with data";
          } else if (nameChanged) {
            arrow.title = `the same underlying group persists (judged by its whole membership), but its ` +
              `written name changed - it was called “${prevFoundName}” in the previous period. Each ` +
              `period's name is drawn only from that period's own most-connected words, so the name can ` +
              `drift even when the group itself does not.`;
          } else {
            arrow.title = "the same underlying group persists, by name and by membership, since the previous period with data";
          }
          arrowSlot.appendChild(arrow);
        }
      }
      prevFoundCard = card;
      prevFoundName = name;
    }

    col.appendChild(card);
    trackEl.appendChild(col);
  });

  renderSattelzeitBand();
  scrollToRange(currentRange);
}

function renderSattelzeitBand() {
  trackEl.querySelector(".sattelzeit-band")?.remove();
  const cols = [...trackEl.querySelectorAll(".timeline-col.in-sattelzeit")];
  if (!cols.length) return;
  const first = cols[0], last = cols[cols.length - 1];
  const band = document.createElement("div");
  band.className = "sattelzeit-band";
  band.style.left = `${first.offsetLeft}px`;
  band.style.width = `${last.offsetLeft + last.offsetWidth - first.offsetLeft}px`;
  band.innerHTML = `<span class="sattelzeit-band-label">Sattelzeit, periods overlapping 1770&ndash;1830</span>`;
  trackEl.insertBefore(band, trackEl.firstChild);
}

let currentRange = "sattelzeit";

function scrollToRange(range) {
  currentRange = range;
  rangeFullBtn.classList.toggle("active", range === "full");
  rangeSattelzeitBtn.classList.toggle("active", range === "sattelzeit");
  const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  if (range === "full") {
    trackEl.scrollTo({ left: 0, behavior });
    return;
  }
  const target = trackEl.querySelector(".timeline-col.in-sattelzeit");
  if (target) trackEl.scrollTo({ left: Math.max(0, target.offsetLeft - 16), behavior });
}
rangeFullBtn.addEventListener("click", () => scrollToRange("full"));
rangeSattelzeitBtn.addEventListener("click", () => scrollToRange("sattelzeit"));
trackEl.tabIndex = 0;
trackEl.addEventListener("keydown", (ev) => {
  if (ev.key === "Home") { ev.preventDefault(); scrollToRange("full"); }
  if (ev.key === "End") { ev.preventDefault(); scrollToRange("sattelzeit"); }
});

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Band 6 of the 2026-08-03 IA schema: robustness, always visible under the
// evidence - not per-word (this is the project's own aggregate result, the
// same regardless of which word is being searched) and not per-region
// (the headline claim is specifically about the combined corpus; region
// splits are their own separate robustness check, browsable on /graph).
// Loaded once, not re-fetched per search. Reuses the exact figures and
// wording /graph's Findings tab already computed - see app.js's
// updateFindingsBanner, this is that same content relocated to the
// primary page instead of duplicating the science.
const SATTELZEIT_CLOSE_FROM = "1780-1800";
const SATTELZEIT_CLOSE_TO = "1800-1820";
const HEADLINE_RESOLUTION = 1.0;

async function loadHeadlineFindings() {
  const res = await fetch("/api/transitions");
  const allTransitions = await res.json();

  const closeRows = allTransitions.filter((t) => t.period_from === SATTELZEIT_CLOSE_FROM && t.period_to === SATTELZEIT_CLOSE_TO);
  const headline = closeRows.find((t) => t.resolution === HEADLINE_RESOLUTION);
  if (!headline) return; // combined transitions not built yet - degrade to no findings block, not an error

  const medianAtHeadlineRes = median(
    allTransitions.filter((t) => t.resolution === HEADLINE_RESOLUTION).map((t) => t.migration_fraction),
  );
  const pct = Math.round(headline.migration_fraction * 100);
  const medianPct = Math.round(medianAtHeadlineRes * 100);

  findingsHeadlineEl.innerHTML =
    `The project's own result, combined corpus: ${escapeHtml(SATTELZEIT_CLOSE_FROM)} &rarr; ${escapeHtml(SATTELZEIT_CLOSE_TO)}, the transition that closes the Sattelzeit window - ` +
    `<strong>${pct}%</strong> of the ${headline.n_shared_words.toLocaleString()} shared words moved to a different group ` +
    `(historical median across every other transition: ${medianPct}%).`;

  findingsCaveatTextEl.textContent =
    `This transition shares only ${headline.n_shared_words.toLocaleString()} words with the period before it - the smallest ` +
    `shared vocabulary in the whole timeline. A subsampling control (shrinking a known-stable pair, 1660-1680 to ` +
    `1680-1700, down to the same token count) reproduced a very similar migration jump on periods with no real ` +
    `reorganization - so on the current corpus this spike cannot yet be told apart from a small-sample artifact, at ` +
    `any resolution. More corpus for 1800-1900 (Gutenberg) is the prerequisite for testing this honestly.`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function init() {
  await loadRegions();
  loadHeadlineFindings();
  if (SEED_WORD) {
    inputEl.value = SEED_WORD;
    runTimeline(SEED_WORD);
  }
}
init();
