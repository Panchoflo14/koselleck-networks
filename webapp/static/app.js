// Neighborhood-first word explorer. Default view is one word and its
// immediate neighbours (/api/graph/<label>?focus=<word>), not the full
// per-period network - mixed-audience testing on the old "dump the whole
// graph" version found it unreadable. An explicit "full network" toggle
// still exposes the old top-k-per-Leiden-community sample for anyone who
// wants to browse rather than start from a word. Node color persists and
// re-aligns across periods (align_to, see backend) so the same color
// across a transition means "same community lineage", not a coincidence -
// except when the active Leiden resolution changes, which resets it (a
// different resolution is a genuinely different partition, not a
// relabeling of the same one).

const svg = d3.select("#graph-svg");
const tooltip = document.getElementById("tooltip");
const infoPanel = document.getElementById("info-panel");
const statusEl = document.getElementById("status");
const sliderEl = document.getElementById("period-slider");
const periodLabelEl = document.getElementById("period-label");
const ticksEl = document.getElementById("period-ticks");
const kInput = document.getElementById("k-input");
const kRow = document.getElementById("k-row");
const fullToggle = document.getElementById("full-toggle");
const focusForm = document.getElementById("focus-form");
const focusInput = document.getElementById("focus-input");
const focusClear = document.getElementById("focus-clear");
const focusPromptEl = document.getElementById("focus-prompt");
const resetViewBtn = document.getElementById("reset-view");
const legendCommunitiesEl = document.getElementById("legend-communities");
const findingsBanner = document.getElementById("findings-banner");
const findingsHeadlineEl = document.getElementById("findings-headline");
const sparklineEl = document.getElementById("sparkline");
const resolutionReadoutEl = document.getElementById("resolution-readout");
const caveatDetails = document.getElementById("caveat");
const caveatText = document.getElementById("caveat-text");
const changedPanel = document.getElementById("changed-panel");
const changedSummaryEl = document.getElementById("changed-summary");
const changedListEl = document.getElementById("changed-list");
const regionRow = document.getElementById("region-row");
const regionToggleEl = document.getElementById("region-toggle");
const sidebarTabs = [...document.querySelectorAll(".sidebar-tab")];
const sidebarPages = { method: document.getElementById("page-method"), findings: document.getElementById("page-findings") };

const SEED_PERIOD = document.body.dataset.seedPeriod;
const SEED_WORD = document.body.dataset.seedWord;

// Ink palette (8 slots) designed against the paper background (#F1EDE3),
// as text color rather than filled shapes - see wiki/webapp-redesign-plan
// in the project's Obsidian cell for the contrast/distinguishability math
// behind these specific values. The array order maximizes perceptual
// distance between *adjacent* slots, so assignColors() below picks slots
// deliberately rather than in first-seen order, to actually use that.
// Two slots recalculated after real-use feedback: the original rose
// (#b45680, 3.92:1) and old gold (#ab7d00, 3.17:1) leaned on the
// bold-text AA exception (3:1) instead of clearing 4.5:1 outright, and in
// practice that wasn't legible enough. Every slot now clears AA normal
// contrast against #F1EDE3 on its own.
const PALETTE = ["#00768a", "#a8372a", "#1b4ba9", "#993366", "#2d7917", "#7f51c1", "#8a6400", "#7d2278"];

let periods = [];
let REGIONS = []; // e.g. ["american", "british"] - whatever this deployment actually has built, see /api/regions
let activeRegion = null; // null = combined; otherwise one of REGIONS
let currentIndex = 0;
let focusWord = SEED_WORD || "";
let activeResolution = 1.0; // which Leiden resolution's community column drives color/changed-words
const positions = new Map(); // word -> {x, y}, persists layout across periods
const communityColorMap = new Map(); // community id -> color; cleared on resolution change (see setResolution)
let currentNodesById = new Map();
let similarityToFocus = new Map(); // neighbour word -> cosine similarity to focusWord, rebuilt on every renderGraph
let communityLabels = new Map(); // raw community id (this period's actual Leiden id) -> plain-English label, refetched per period/resolution
let allTransitions = [];
let medianByResolution = new Map(); // resolution -> historical median migration_fraction
let lastHeadlinePct = null; // set by updateFindingsBanner, read by updateChangedPanel's copy

let width = 0;
let height = 0;
let simulation = null;
let zoom = null;
let pinnedFocusActive = false; // true when the focused word is fixed dead-center (see renderGraph)

function sizeSvg() {
  const frame = document.querySelector(".graph-frame");
  width = frame.clientWidth;
  height = frame.clientHeight;
}
window.addEventListener("resize", sizeSvg);

function fontSizeFor(degree) {
  return Math.max(11, Math.min(24, 11 + Math.sqrt(degree) * 1.6));
}

function collideRadiusFor(d) {
  return Math.max(16, d.id.length * fontSizeFor(d.degree) * 0.3);
}

// Greedily assigns each not-yet-seen community id the palette slot farthest
// (by array distance, a proxy for the perceptual distance the array was
// ordered by) from every slot already in use - instead of first-seen
// insertion order, which scrambled the adjacency-optimized ordering and let
// two poorly-separated colors end up next to each other on screen by pure
// chance of visit history.
function assignColors(communityIds) {
  const usedSlots = new Set([...communityColorMap.values()].map((c) => PALETTE.indexOf(c)));
  communityIds.forEach((c) => {
    if (communityColorMap.has(c)) return;
    if (usedSlots.size === 0) {
      communityColorMap.set(c, PALETTE[0]);
      usedSlots.add(0);
      return;
    }
    let best = 0;
    let bestDist = -1;
    for (let s = 0; s < PALETTE.length; s++) {
      const dist = Math.min(...[...usedSlots].map((u) => Math.min(Math.abs(s - u), PALETTE.length - Math.abs(s - u))));
      if (dist > bestDist) { bestDist = dist; best = s; }
    }
    communityColorMap.set(c, PALETTE[best]);
    usedSlots.add(best);
  });
}

function colorFor(community) {
  if (!communityColorMap.has(community)) assignColors([community]);
  return communityColorMap.get(community);
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Whether a period has data for whatever's currently selected (combined, or
// one region) - a region can have gaps (e.g. no American arm before 1639)
// that the combined period doesn't, so this can't just read p.has_data once
// a region is active.
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

async function setRegion(region) {
  if (region === activeRegion) return;
  activeRegion = region;
  // Leiden ids from one region's network have no correspondence to another
  // region's (or the combined network's) ids - a fresh color slot per id is
  // correct here for the same reason a resolution change clears it too.
  communityColorMap.clear();
  renderRegionToggle();
  buildTicks();
  await loadTransitions();
  loadPeriod(currentIndex);
}

async function loadTransitions() {
  const params = activeRegion ? `?region=${encodeURIComponent(activeRegion)}` : "";
  const res = await fetch(`/api/transitions${params}`);
  allTransitions = await res.json();
  const resolutions = [...new Set(allTransitions.map((t) => t.resolution))];
  medianByResolution = new Map(resolutions.map((r) => [
    r, median(allTransitions.filter((t) => t.resolution === r).map((t) => t.migration_fraction)),
  ]));
}

// Runs once at load, not per period - the mixed-fraction is a property of
// the whole labeling pass (all 308 communities), not of whatever's on
// screen right now.
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

async function loadPeriods() {
  const res = await fetch("/api/periods");
  periods = await res.json();
  sliderEl.max = periods.length - 1;
  buildTicks();
  sizeSvg();
  setupZoom();

  const seedIndex = periods.findIndex((p) => p.label === SEED_PERIOD);
  currentIndex = seedIndex >= 0 ? seedIndex : 0;
  sliderEl.value = currentIndex;
  focusInput.value = focusWord;

  await loadRegions();
  await loadTransitions();
  loadLabelCaveat();
  await loadPeriod(currentIndex);
}

function buildTicks() {
  ticksEl.innerHTML = "";
  periods.forEach((p) => {
    const has = periodHasData(p);
    const tick = document.createElement("span");
    tick.className = "tick" + (has ? "" : " gap");
    tick.title = p.label + (has ? "" : " (no data)");
    ticksEl.appendChild(tick);
  });
}

function prevPopulatedLabel(index) {
  for (let i = index - 1; i >= 0; i--) {
    if (periods[i] && periodHasData(periods[i])) return periods[i].label;
  }
  return null;
}

// "input" fires on every pixel of drag - fetching + re-running the force
// simulation on each one made dragging visibly lag, and even a short
// debounce still fired mid-drag on a slow/paused gesture. "change" fires
// exactly once a range input's drag actually ends (mouseup), or once per
// discrete step for keyboard/click - so it alone is "only when stopped",
// no timer needed. "input" now only updates the (cheap) label text live,
// it never triggers a fetch or a re-render.
sliderEl.addEventListener("input", () => {
  const label = periods[Number(sliderEl.value)];
  if (label) periodLabelEl.textContent = label.label;
});
sliderEl.addEventListener("change", () => {
  currentIndex = Number(sliderEl.value);
  loadPeriod(currentIndex);
});

fullToggle.addEventListener("change", () => loadPeriod(currentIndex));
kInput.addEventListener("change", () => { if (fullToggle.checked) loadPeriod(currentIndex); });

focusForm.addEventListener("submit", (ev) => {
  ev.preventDefault();
  focusOn(focusInput.value);
});

focusClear.addEventListener("click", () => {
  focusWord = "";
  focusInput.value = "";
  loadPeriod(currentIndex);
});

resetViewBtn.addEventListener("click", () => {
  if (pinnedFocusActive) {
    svg.transition().duration(300).call(zoom.transform, d3.zoomIdentity);
  } else {
    fitToView([...currentNodesById.values()]);
  }
});

// Same button, two jobs depending on state: with a word focused (in either
// mode now, see pinFocus above) it snaps back to that word, which is the
// "I searched it, panned/zoomed around the full graph, and now can't find
// it again" fix; with nothing focused it's the original whole-sample fit.
function updateResetButtonLabel() {
  resetViewBtn.textContent = pinnedFocusActive && focusWord ? `Find "${focusWord}"` : "Reset view";
}

sidebarTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    sidebarTabs.forEach((t) => t.classList.toggle("active", t === tab));
    Object.entries(sidebarPages).forEach(([name, el]) => el.classList.toggle("inactive", name !== tab.dataset.page));
  });
});

const wordPeriodsCache = new Map(); // word -> [period labels it appears in], chronological

async function getWordPeriods(word) {
  if (wordPeriodsCache.has(word)) return wordPeriodsCache.get(word);
  const res = await fetch(`/api/word-periods/${encodeURIComponent(word)}`);
  const data = await res.json();
  wordPeriodsCache.set(word, data.periods);
  return data.periods;
}

async function focusOn(word) {
  focusWord = word.trim().toLowerCase();
  focusInput.value = focusWord;
  if (focusWord) {
    // Placing a new word shouldn't dead-end on "not here" if the word just
    // isn't in whatever period happens to be on screen - jump straight to
    // the earliest period where it actually exists instead.
    const wordPeriods = await getWordPeriods(focusWord);
    const currentLabel = periods[currentIndex] ? periods[currentIndex].label : null;
    if (wordPeriods.length && !wordPeriods.includes(currentLabel)) {
      const idx = periods.findIndex((p) => p.label === wordPeriods[0]);
      if (idx >= 0) {
        currentIndex = idx;
        sliderEl.value = idx;
      }
    }
  }
  loadPeriod(currentIndex);
}

function setResolution(r) {
  if (r === activeResolution) return;
  activeResolution = r;
  // A different resolution is a different partition, not a relabeling of
  // the same one - carrying colors over would imply a correspondence that
  // doesn't exist, so every community earns a fresh slot.
  communityColorMap.clear();
  loadPeriod(currentIndex);
}

function showFocusPrompt(html) {
  focusPromptEl.innerHTML = html;
  focusPromptEl.classList.remove("hidden");
  if (simulation) simulation.stop();
  svg.selectAll("*").remove();
}

function hideFocusPrompt() {
  focusPromptEl.classList.add("hidden");
}

async function loadPeriod(index) {
  const period = periods[index];
  if (!period) return;
  periodLabelEl.textContent = period.label;
  hideTooltip();

  if (!periodHasData(period)) {
    const regionNote = activeRegion ? ` for the ${activeRegion} source` : "";
    statusEl.textContent = `${period.label}: no data in this period${regionNote} - corpus coverage gap.`;
    showFocusPrompt(`<strong>${escapeHtml(period.label)}</strong> has no data${escapeHtml(regionNote)} - a corpus coverage gap.`);
    findingsBanner.classList.add("hidden");
    changedPanel.classList.add("hidden");
    hideInfoPanel();
    communityLabels = new Map();
    renderLegend();
    return;
  }

  const prevLabel = prevPopulatedLabel(index);
  const full = fullToggle.checked;
  kRow.style.display = full ? "" : "none";

  const params = new URLSearchParams();
  params.set("res", String(activeResolution));
  if (full) {
    params.set("full", "1");
    params.set("k", kInput.value || "12");
  }
  if (focusWord) params.set("focus", focusWord);
  if (prevLabel) params.set("align_to", prevLabel);
  if (activeRegion) params.set("region", activeRegion);

  // Community labels (plain-English themes, see webapp/app.py's get_labels)
  // are region-specific - each region's own Leiden run assigns completely
  // different ids to completely different word groups, so this fetches
  // whichever region's own labels file the backend has (tooltip/legend
  // already fall back to "#<id>" when communityLabels has nothing for
  // that id, e.g. a region whose labels haven't been generated yet).
  const labelsParams = new URLSearchParams({ res: String(activeResolution) });
  if (activeRegion) labelsParams.set("region", activeRegion);
  const [res, labelsRes] = await Promise.all([
    fetch(`/api/graph/${encodeURIComponent(period.label)}?${params.toString()}`),
    fetch(`/api/community-labels/${encodeURIComponent(period.label)}?${labelsParams.toString()}`),
  ]);
  const data = await res.json();
  communityLabels = new Map(Object.entries(await labelsRes.json()));

  if (data.needs_focus) {
    showFocusPrompt(
      `Type a word above to see its neighbourhood in <strong>${escapeHtml(period.label)}</strong> ` +
      `- try "${escapeHtml(SEED_WORD || "reason")}", or switch on the full network.`
    );
    statusEl.textContent = "";
    renderLegend();
  } else if (!full && focusWord && data.focus_found === false) {
    const known = wordPeriodsCache.get(focusWord);
    showFocusPrompt(
      known && known.length === 0
        ? `"${escapeHtml(focusWord)}" doesn't appear in any period of this corpus. Try another word.`
        : `"${escapeHtml(focusWord)}" doesn't appear in ${escapeHtml(period.label)}. ` +
          `Try another word, or switch on the full network.`
    );
    statusEl.textContent = "";
    renderLegend();
  } else {
    hideFocusPrompt();
    if (full && focusWord && data.focus_found === false) {
      statusEl.textContent = `"${focusWord}" doesn't appear in ${period.label}. Showing the top-connected words per cluster instead.`;
    } else if (focusWord) {
      statusEl.textContent = full
        ? `${period.label} - full network, focused on "${focusWord}".`
        : `${period.label} - "${focusWord}" and its nearest neighbours.`;
    } else {
      statusEl.textContent = `${period.label} - top ${kInput.value || 12} most-connected words per cluster.`;
    }

    const sortedCommunities = [...new Set(data.nodes.map((n) => n.community))].sort((a, b) => a - b);
    assignColors(sortedCommunities);
    renderGraph(data, full);
    renderLegend();
  }

  updateFindingsBanner(period, prevLabel);
  updateChangedPanel(period, prevLabel);
  updateFocusInfoPanel();
}

function renderLegend() {
  legendCommunitiesEl.innerHTML = "";
  const nodesList = [...currentNodesById.values()];
  [...communityColorMap.entries()]
    .filter(([community]) => nodesList.some((n) => n.community === community))
    .forEach(([community, color]) => {
      // A swatch's color is the align_to-remapped id (for cross-period
      // continuity, arbitrary and internal); the number shown next to it
      // has to be this period's actual raw Leiden id instead, or it won't
      // match the number the word-search tab shows for the same word in
      // the same period (that tab never remaps ids at all).
      const member = nodesList.find((n) => n.community === community);
      const rawId = member ? member.community_raw : community;
      const label = member ? communityLabels.get(String(rawId)) : null;
      const item = document.createElement("span");
      item.className = "legend-size-item";
      item.innerHTML = `<span class="ink-swatch" style="color:${color}">Aa</span> ` +
        (label ? `${escapeHtml(label)} (#${rawId})` : `#${rawId}`);
      legendCommunitiesEl.appendChild(item);
    });
}

function setupZoom() {
  zoom = d3.zoom()
    .scaleExtent([0.2, 6])
    .on("zoom", (event) => svg.select("g.zoom-layer").attr("transform", event.transform));
  svg.call(zoom);
}

// The bounding box of the current nodes, framed with padding - called once
// the force layout settles so the initial view is already well-framed
// instead of requiring the user to zoom/pan just to see everything (the
// "I see the graph outside my view" complaint).
function fitToView(nodes, padding = 40) {
  if (!nodes.length || !zoom) return;
  const xs = nodes.map((n) => n.x);
  const ys = nodes.map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const bw = Math.max(maxX - minX, 1);
  const bh = Math.max(maxY - minY, 1);
  const scale = Math.min(6, 0.9 * Math.min((width - padding * 2) / bw, (height - padding * 2) / bh));
  const tx = width / 2 - scale * (minX + maxX) / 2;
  const ty = height / 2 - scale * (minY + maxY) / 2;
  svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}

// The zoom-layer/links/nodes groups are created once and reused across
// every renderGraph call from then on - see renderGraph's comment for why.
function ensureGraphDom() {
  if (!svg.select("g.zoom-layer").empty()) return;
  const root = svg.append("g").attr("class", "zoom-layer");
  root.append("g").attr("class", "links");
  root.append("g").attr("class", "nodes");
}

function renderGraph(data, full) {
  ensureGraphDom();

  // Pin the focused word dead-center in the frame (see below) instead of
  // leaving it to the force layout and then waiting for the whole
  // simulation to settle before fitToView can even compute a bounding box -
  // that wait was the "takes too long to auto-center" complaint. With a
  // fixed anchor, the spawn point for brand-new nodes is just that same
  // center, always - no more waiting on a settled layout to even know where
  // "center" is. This used to be neighbourhood-mode-only (`!full && ...`);
  // dropped that restriction so searching a word while "full network" is on
  // also pins it center and grows the sample outward from there, instead of
  // the word landing wherever the force layout happens to put it among a
  // couple hundred other nodes - the "I searched it and then lost it in the
  // full graph" complaint.
  const pinFocus = data.nodes.some((n) => n.focused);
  const anchor = { x: width / 2, y: height / 2 };

  const nodes = data.nodes.map((n) => {
    if (pinFocus && n.focused) return { ...n, x: anchor.x, y: anchor.y, fx: anchor.x, fy: anchor.y };
    const prior = positions.get(n.id);
    if (prior) return { ...n, x: prior.x, y: prior.y, fx: null, fy: null };
    // Brand-new node (wasn't on screen a moment ago): spawn close to the
    // anchor with a small spread, instead of scattered across 60% of the
    // whole canvas. That wide scatter used to be the real source of the
    // "shake": the charge force had to violently drag dozens of far-flung
    // new nodes into place on every single focus change, perturbing
    // already-settled nodes along the way. Spawning near where they'll
    // roughly end up leaves far less energy for the simulation to burn off.
    return { ...n, x: anchor.x + (Math.random() - 0.5) * 80, y: anchor.y + (Math.random() - 0.5) * 80, fx: null, fy: null };
  });
  currentNodesById = new Map(nodes.map((n) => [n.id, n]));
  const links = data.edges
    .filter((e) => currentNodesById.has(e.source) && currentNodesById.has(e.target))
    .map((e) => ({ ...e }));

  // Edge weight *is* cosine similarity (see src/network.py) - in
  // neighbourhood mode every visible word is a direct graph neighbour of
  // focusWord, so this is a plain lookup, not a recomputation. Built from
  // `links` before d3's forceLink mutates source/target from ids into node
  // references. In full-network mode most words aren't focusWord's
  // neighbour at all (no top-15 edge), so the tooltip simply omits the line.
  similarityToFocus = new Map();
  if (focusWord) {
    links.forEach((e) => {
      if (e.source === focusWord) similarityToFocus.set(e.target, e.weight);
      else if (e.target === focusWord) similarityToFocus.set(e.source, e.weight);
    });
  }

  // Only one community "belongs" to the current view in neighborhood mode -
  // the focused word's. Dimming every other community keeps at most two
  // ink saturations competing on screen at once, instead of every distinct
  // neighbour community shouting at full strength (the "mezcla de colores
  // se ve horrible" complaint). Full-network mode has no single protagonist
  // community, so there dimming would just look like an error - skip it.
  const focusNode = focusWord ? currentNodesById.get(focusWord) : null;
  const focusCommunity = focusNode ? focusNode.community : null;

  // D3's general update pattern instead of tearing down and re-appending
  // every element on every call: a link/node that persists between two
  // renders is the *same* DOM element getting cheap attribute/style
  // updates, not a fresh one - and .node-label's CSS transition (see
  // style.css) turns a recolor or a dim/undim into a fade instead of a cut.
  // A departing node fades out and is removed instead of just vanishing.
  const linkGroup = svg.select("g.links");
  const nodeGroup = svg.select("g.nodes");

  linkGroup.selectAll("line")
    .data(links, (d) => `${d.source}--${d.target}`)
    .join(
      (enter) => enter.append("line").attr("class", "link").style("opacity", 0),
      (update) => update,
      (exit) => exit.transition().duration(200).style("opacity", 0).remove(),
    )
    .attr("stroke-width", (d) => Math.max(0.6, d.weight * 2.5))
    .style("opacity", 0.35);

  nodeGroup.selectAll("text")
    .data(nodes, (d) => d.id)
    .join(
      (enter) => enter.append("text")
        .attr("class", "node-label")
        .attr("x", (d) => d.x)
        .attr("y", (d) => d.y)
        .text((d) => d.id)
        .style("fill-opacity", 0)
        .on("mouseenter", (event, d) => showTooltip(event, d))
        .on("mousemove", (event) => moveTooltip(event))
        .on("mouseleave", hideTooltip)
        .on("click", (event, d) => { if (d.id !== focusWord) focusOn(d.id); }),
      (update) => update,
      (exit) => exit.transition().duration(200).style("fill-opacity", 0).remove(),
    )
    // The searched-for word is always plain dark ink, whatever community it
    // landed in - legibility for the one word every user is guaranteed to
    // look for shouldn't depend on which of the 8 hues it happened to get.
    .style("font-size", (d) => fontSizeFor(d.degree) + "px")
    .style("fill", (d) => d.focused ? "#23201B" : colorFor(d.community))
    .style("fill-opacity", (d) => (full || focusCommunity === null || d.community === focusCommunity) ? 1 : 0.3);

  if (!nodes.length) return;

  // One simulation persists for the page's whole life instead of a fresh
  // d3.forceSimulation() every render. A fresh simulation always starts at
  // alpha 1 (full energy) regardless of how settled the layout already
  // was - restarting the *same* simulation at a modest alpha below is a
  // gentle nudge instead of a full reboot every time a word is clicked.
  if (!simulation) {
    simulation = d3.forceSimulation()
      .force("link", d3.forceLink().id((d) => d.id).distance(50).strength(0.3))
      .force("charge", d3.forceManyBody().strength(-110))
      .force("collide", d3.forceCollide((d) => collideRadiusFor(d)).strength(0.9))
      .force("x", d3.forceX().strength(0.03))
      .force("y", d3.forceY().strength(0.03))
      .on("tick", () => {
        svg.select("g.links").selectAll("line")
          .attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
          .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
        svg.select("g.nodes").selectAll("text")
          .attr("x", (d) => d.x).attr("y", (d) => d.y);
        simulation.nodes().forEach((n) => positions.set(n.id, { x: n.x, y: n.y }));
      })
      // Only relevant with no word focused (browsing the full sample with
      // nothing searched): with a pinned focus word the view is already
      // correctly centered from tick one, see the immediate zoom reset
      // below - waiting for the layout to fully settle before framing it
      // would reintroduce the same delay this pin was built to remove.
      .on("end", () => { if (!pinnedFocusActive) fitToView(simulation.nodes()); });
  }

  pinnedFocusActive = pinFocus;
  updateResetButtonLabel();
  simulation.nodes(nodes);
  simulation.force("link").links(links);
  simulation.force("x").x(width / 2);
  simulation.force("y").y(height / 2);
  simulation.alpha(0.5).restart();

  if (pinFocus) {
    svg.transition().duration(300).call(zoom.transform, d3.zoomIdentity);
  }
}

function renderSparkline(rows) {
  sparklineEl.innerHTML = "";
  [...rows].sort((a, b) => a.resolution - b.resolution).forEach((r) => {
    const bar = document.createElement("div");
    bar.className = "sparkline-bar"
      + (r.resolution === activeResolution ? " active" : "")
      + (r.resolution === 1.0 ? " default" : "");
    bar.style.height = Math.max(2, Math.round(r.migration_fraction * 30)) + "px";
    bar.title = `clustering detail ${r.resolution}: ${Math.round(r.migration_fraction * 100)}% migration - click to view at this setting`;
    bar.addEventListener("click", () => setResolution(r.resolution));
    sparklineEl.appendChild(bar);
  });
  resolutionReadoutEl.textContent = activeResolution === 1.0
    ? "detail: 1.0 (paper default)"
    : `detail: ${activeResolution}`;
}

function updateFindingsBanner(period, prevLabel) {
  if (!prevLabel) {
    findingsBanner.classList.add("hidden");
    lastHeadlinePct = null;
    return;
  }
  const rows = allTransitions.filter((t) => t.period_from === prevLabel && t.period_to === period.label);
  if (!rows.length) {
    findingsBanner.classList.add("hidden");
    lastHeadlinePct = null;
    return;
  }
  findingsBanner.classList.remove("hidden");
  const headline = rows.find((r) => r.resolution === activeResolution) || rows[0];
  const pct = Math.round(headline.migration_fraction * 100);
  const medianPct = Math.round((medianByResolution.get(activeResolution) || 0) * 100);
  lastHeadlinePct = pct;
  findingsHeadlineEl.innerHTML =
    `${escapeHtml(prevLabel)} &rarr; ${escapeHtml(period.label)}: <strong>${pct}%</strong> of the ` +
    `${headline.n_shared_words.toLocaleString()} shared words changed community at clustering-detail ${activeResolution} ` +
    `(historical median at this setting: ${medianPct}%).`;
  renderSparkline(rows);

  const isSattelzeitClose = prevLabel === "1780-1800" && period.label === "1800-1820";
  caveatText.textContent = isSattelzeitClose
    ? `This transition shares only ${headline.n_shared_words.toLocaleString()} words with the period before it - the ` +
      `smallest shared vocabulary in the whole timeline. A subsampling control (shrinking a known-stable pair, ` +
      `1660-1680 to 1680-1700, down to the same token count) reproduced a very similar migration jump on periods ` +
      `with no real reorganization - so on the current corpus this spike cannot yet be told apart from a small-sample ` +
      `artifact, at any resolution. More corpus for 1800-1900 (Gutenberg) is the prerequisite for testing this honestly.`
    : `Migration_fraction tends to run higher whenever shared vocabulary is small, independent of any real semantic ` +
      `reorganization - read this transition's ${pct}% together with its ${headline.n_shared_words.toLocaleString()} ` +
      `shared words, not on its own.`;
  caveatDetails.open = isSattelzeitClose;
}

async function updateChangedPanel(period, prevLabel) {
  if (!prevLabel) {
    changedPanel.classList.add("hidden");
    return;
  }
  const regionParam = activeRegion ? `&region=${encodeURIComponent(activeRegion)}` : "";
  const res = await fetch(`/api/changed/${encodeURIComponent(period.label)}?res=${activeResolution}${regionParam}`);
  const data = await res.json();
  if (!data.n_changed) {
    changedPanel.classList.add("hidden");
    return;
  }
  changedPanel.classList.remove("hidden");
  const pctText = lastHeadlinePct === null ? "" : `the ${lastHeadlinePct}% above, `;
  changedSummaryEl.textContent =
    `These are the actual words moving between ${data.period_from} and ${data.period_to} - ${pctText}` +
    `made concrete rather than left as a statistic. ${data.n_changed} of ${data.n_shared_words} words shared ` +
    `with ${data.period_from} landed in a different community; the ${data.words.length} most-connected are shown ` +
    `here. Click any word to recenter the graph on it.`;
  changedListEl.innerHTML = "";
  data.words.forEach((w) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = w;
    btn.addEventListener("click", () => focusOn(w));
    li.appendChild(btn);
    changedListEl.appendChild(li);
  });
}

// "community #5" means nothing to a historian on its own - shows the
// Claude-assigned plain-English theme alongside it wherever one exists
// (see webapp/app.py's get_labels; looked up by this period's raw Leiden
// id, not the align_to color-continuity id).
function communityLabelText(d) {
  const label = communityLabels.get(String(d.community_raw));
  return label ? `${escapeHtml(label)} (community #${d.community_raw})` : `community #${d.community_raw}`;
}

function showTooltip(event, d) {
  let body = `degree ${d.degree} &middot; ${communityLabelText(d)}`;
  const sim = similarityToFocus.get(d.id);
  if (focusWord && d.id !== focusWord && sim !== undefined) {
    body += `<br>cosine similarity to "${escapeHtml(focusWord)}": <strong>${sim.toFixed(3)}</strong>`;
  }
  tooltip.innerHTML = `<strong>${escapeHtml(d.id)}</strong><br>${body}`;
  tooltip.classList.remove("hidden");
  moveTooltip(event);
}

function moveTooltip(event) {
  const frame = document.querySelector(".graph-frame").getBoundingClientRect();
  tooltip.style.left = (event.clientX - frame.left + 12) + "px";
  tooltip.style.top = (event.clientY - frame.top + 12) + "px";
}

function hideTooltip() {
  tooltip.classList.add("hidden");
}

async function updateFocusInfoPanel() {
  if (!focusWord) {
    hideInfoPanel();
    return;
  }
  const node = currentNodesById.get(focusWord);
  if (!node) {
    hideInfoPanel();
    return;
  }

  const period = periods[currentIndex];
  infoPanel.classList.remove("hidden");
  infoPanel.innerHTML = infoPanelBody(node, `<span class="muted">checking continuity with previous period...</span>`);

  const prevLabel = prevPopulatedLabel(currentIndex);
  if (!prevLabel) {
    infoPanel.innerHTML = infoPanelBody(node, `<span class="muted">no earlier period with data to compare.</span>`);
    return;
  }

  const regionParam = activeRegion ? `?region=${encodeURIComponent(activeRegion)}` : "";
  const [curRes, prevRes] = await Promise.all([
    fetch(`/api/neighbors/${encodeURIComponent(period.label)}/${encodeURIComponent(node.id)}${regionParam}`).then((r) => r.json()),
    fetch(`/api/neighbors/${encodeURIComponent(prevLabel)}/${encodeURIComponent(node.id)}${regionParam}`).then((r) => r.json()),
  ]);

  let continuityHtml = `<span class="muted">"${escapeHtml(node.id)}" not found in ${prevLabel}.</span>`;
  if (curRes.found && prevRes.found) {
    const curSet = new Set(curRes.neighbors);
    const prevSet = new Set(prevRes.neighbors);
    const union = new Set([...curSet, ...prevSet]);
    const overlap = [...curSet].filter((w) => prevSet.has(w)).length;
    const continuity = union.size ? overlap / union.size : 0;
    continuityHtml = `continuity vs ${prevLabel}: <strong>${Math.round(continuity * 100)}%</strong> of neighbours shared`;
  }

  infoPanel.innerHTML = infoPanelBody(node, continuityHtml);
  document.getElementById("info-close").addEventListener("click", hideInfoPanel);
}

function infoPanelBody(d, extraHtml) {
  return `<strong>${escapeHtml(d.id)}</strong><br>degree ${d.degree} &middot; ${communityLabelText(d)}<br>${extraHtml}<br><button id="info-close">close</button>`;
}

function hideInfoPanel() {
  infoPanel.classList.add("hidden");
  infoPanel.innerHTML = "";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

loadPeriods();
