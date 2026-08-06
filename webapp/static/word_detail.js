// Shared word-detail rendering, used by both /search (the original word
// search tab) and /timeline (each period card's drill-in, see the vault's
// wiki/timeline-feature-plan.md step 7 - "Drill-in via the existing
// /search endpoint and rendering, extracted for reuse"). No bundler in
// this project, so this is a plain global (window.WordDetail) loaded before
// either page's own script, not an ES module - matches how the rest of the
// webapp's static JS already works.
//
// Also the single source of truth for the subject-area taxonomy (colors +
// short display names) - timeline.js reads WordDetail.SUBJECTS/subjectShort/
// subjectColor rather than keeping its own copy, so the dot next to a group
// name means the same thing everywhere it appears.
//
// Callers own their own DOM ids/markup and pass element references in, so
// two copies of this view (search's page-level one, timeline's per-card
// drill-in) can exist without id collisions or shared mutable state here.
const WordDetail = (() => {
  // One fixed color per subject area (see the vault's wiki/labeling-pipeline.md
  // for how this taxonomy was derived from 707 real labels). Validated with
  // the dataviz skill's palette validator against this page's own paper
  // background (#F1EDE3). Keyed by the *internal* name the backend/label
  // files actually use (unchanged data contract); {short, color} is what
  // the UI shows. "No clear subject" is the one deliberate break in the
  // pattern: plain gray instead of a 15th hue, because it marks the
  // *absence* of a real topic, not a topic of its own.
  const SUBJECTS = {
    "Government, Law & Administration": { short: "Government", color: "#a6243e" },
    "Religion, Theology & the Church": { short: "Religion", color: "#009bc5" },
    "Morality: Virtue & Vice": { short: "Morality", color: "#6e40ab" },
    "Medicine, Body & Health": { short: "Medicine", color: "#c7700b" },
    "Science, Mathematics & Natural Philosophy": { short: "Science", color: "#962c7a" },
    "Nature, Landscape & Weather": { short: "Nature", color: "#6b9a2e" },
    "Military & Warfare": { short: "Military", color: "#1c58b8" },
    "Trade, Finance & Commerce": { short: "Trade", color: "#00a3a4" },
    "History, Genealogy, Nobility & Kinship": { short: "History", color: "#d25f6c" },
    "Rhetoric & Persuasion": { short: "Rhetoric", color: "#006e9d" },
    "Literature, Drama & Poetic Diction": { short: "Literature", color: "#9772d3" },
    "Domestic Life, Dress & Household": { short: "Domestic life", color: "#9c3c00" },
    "Geography & Territory": { short: "Geography", color: "#c162a4" },
    "Books, Learning & Scholarship": { short: "Learning", color: "#3c6e00" },
    "Structural / Uncertain": { short: "No clear subject", color: "var(--muted)" },
  };
  const NO_SUBJECT = "No clear subject";
  const SUBJECT_ORDER = Object.keys(SUBJECTS);

  function isUncertainLabel(label, lane) {
    return lane === "Structural / Uncertain" || (label || "").toLowerCase().includes("(mixed)");
  }

  function subjectShort(lane, label) {
    if (isUncertainLabel(label, lane)) return NO_SUBJECT;
    return (SUBJECTS[lane] && SUBJECTS[lane].short) || lane || NO_SUBJECT;
  }

  function subjectColor(lane, label) {
    if (isUncertainLabel(label, lane)) return "var(--muted)";
    return (SUBJECTS[lane] && SUBJECTS[lane].color) || "var(--muted)";
  }

  function subjectDot(lane, label) {
    return `<span class="lane-dot" style="background:${subjectColor(lane, label)}" title="${subjectShort(lane, label)}"></span>`;
  }

  // Groups are numbered arbitrarily by the underlying clustering, fresh in
  // every period - "group 3" alone means nothing across periods. The
  // plain-English name is the primary signal; the number stays alongside it
  // for anyone cross-referencing the underlying data. Strips the raw
  // "(mixed)" tag some label files still carry in the text itself - the
  // subject dot already says "no clear subject", so the tag would otherwise
  // say the same thing twice.
  function stripMixedTag(label) {
    return (label || "").replace(/\s*\(mixed\)\s*$/i, "").trim();
  }

  function formatGroup(id, label) {
    const name = stripMixedTag(label);
    return name ? `${name} (group ${id})` : `group ${id}`;
  }

  // HTML version with the subject dot in front - used anywhere a group is
  // shown as a standalone value (table cells, meta rows), not inside a
  // running sentence (formatGroup, plain text, covers that case).
  function formatGroupHtml(id, label, lane) {
    return `${subjectDot(lane, label)}${formatGroup(id, label)}`;
  }

  async function fetchDetail(period, word, region) {
    const regionParam = region ? `?region=${encodeURIComponent(region)}` : "";
    const res = await fetch(`/api/search/${encodeURIComponent(period)}/${encodeURIComponent(word)}${regionParam}`);
    return res.json();
  }

  // els: {heading, meta, neighborsBody} - DOM elements the caller already
  // has (an <h2>/similar, a <dl>, a <tbody>). onWordClick(word), if given,
  // is called when a neighbour word is clicked, so each host page decides
  // what "look this word up too" means for it (search re-searches in
  // place; timeline could jump to that word's own timeline).
  function render(els, data, onWordClick) {
    els.heading.textContent = `"${data.word}" in ${data.period}`;

    let changeHtml;
    if (data.prev_period === null || data.prev_period === undefined) {
      changeHtml = `<span class="muted">no earlier period with data to compare</span>`;
    } else if (data.prev_community === null || data.prev_community === undefined) {
      changeHtml = `<span class="muted">not present in ${data.prev_period}</span>`;
    } else if (data.changed_community) {
      changeHtml = `<strong class="flag-changed">moved</strong> since ${data.prev_period} ` +
        `(was ${formatGroup(data.prev_community, data.prev_community_label)})`;
    } else {
      changeHtml = `<strong class="flag-stable">stayed</strong> in the same group as ${data.prev_period}`;
    }

    els.meta.innerHTML = `
      <dt>Group</dt><dd>${formatGroupHtml(data.community, data.community_label, data.lane)}</dd>
      <dt>Links among its neighbours</dt><dd>${data.degree} connections (each word links out to its 15 closest neighbours, but a well-connected word can be linked back by many more than 15)</dd>
      <dt>Since previous period</dt><dd>${changeHtml}</dd>
    `;

    els.neighborsBody.innerHTML = "";
    data.neighbors.forEach((n) => {
      const tr = document.createElement("tr");
      if (n.community === data.community) tr.classList.add("same-community");
      const wordTd = document.createElement("td");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "word-link";
      btn.textContent = n.word;
      if (onWordClick) btn.addEventListener("click", () => onWordClick(n.word));
      wordTd.appendChild(btn);
      const simTd = document.createElement("td");
      simTd.textContent = n.similarity.toFixed(3);
      const commTd = document.createElement("td");
      commTd.innerHTML = formatGroupHtml(n.community, n.community_label, n.lane);
      tr.append(wordTd, simTd, commTd);
      els.neighborsBody.appendChild(tr);
    });
  }

  // ---- Journey view: the same word drawn as a path through the 14 subject
  // lanes across every period, instead of one period's neighbour table.
  // Added 2026-08-05 after Bernardo reviewed the first mockup and flagged
  // the original always-on 14-swatch legend as noise. Two fixes from that
  // round: a lane only gets a label the first time the word's own line
  // enters it (no separate legend at all - the chart is its own legend),
  // and every label is drawn in a final pass after every line and guide
  // track, then given the same paint-order halo .node-label already uses
  // in app.js's graph, so it always paints on top and cuts a clean gap
  // through whatever line crosses behind it instead of visually colliding
  // with it (the original complaint: "No clear subject" sitting on top of
  // the line into a steep jump like nature's 1670-1690 dip).
  const JOURNEY_ROW_H = 24;
  const JOURNEY_ROW_TOP = 14;
  const JOURNEY_COL_W = 42;
  const JOURNEY_COL_LEFT = 10;

  function journeyColX(i) { return JOURNEY_COL_LEFT + JOURNEY_COL_W * i + JOURNEY_COL_W / 2; }
  function journeyRowY(lane) { return JOURNEY_ROW_TOP + SUBJECT_ORDER.indexOf(lane) * JOURNEY_ROW_H + JOURNEY_ROW_H / 2; }

  const SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs, text) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function buildJourneySvg(periods) {
    const chartW = JOURNEY_COL_LEFT + JOURNEY_COL_W * periods.length + 30;
    const chartH = JOURNEY_ROW_TOP + JOURNEY_ROW_H * SUBJECT_ORDER.length + 34;
    const svg = svgEl("svg", { viewBox: `0 0 ${chartW} ${chartH}`, class: "journey-svg" });

    // Guide tracks, fixed SUBJECT_ORDER, same order used everywhere else in
    // the app - most stay a plain unlabeled line; only the lanes this word
    // actually visits get a name, drawn later, on top.
    SUBJECT_ORDER.forEach((lane) => {
      svg.appendChild(svgEl("line", {
        x1: JOURNEY_COL_LEFT, x2: chartW - 20, y1: journeyRowY(lane), y2: journeyRowY(lane),
        class: "journey-guide",
      }));
    });

    // Coverage-gap band - periods with has_data:false (no trained network
    // yet), the same honest-gap treatment used everywhere else in the app.
    const gapStart = periods.findIndex((p) => !p.has_data);
    if (gapStart >= 0) {
      const gapX = journeyColX(gapStart) - JOURNEY_COL_W / 2;
      svg.appendChild(svgEl("rect", {
        x: gapX, y: 0, width: chartW - 20 - gapX, height: chartH - 22, class: "journey-gap-band",
      }));
      svg.appendChild(svgEl("text", { x: gapX + 6, y: 11, class: "journey-gap-label" }, "no corpus data yet"));
    }

    const labelsToRender = []; // drawn in a final pass, after every line, so a label always paints on top
    const labeledLanes = new Set();
    let prevFoundIdx = null;

    periods.forEach((p, i) => {
      const x = journeyColX(i);
      if (!p.has_data) return; // covered by the gap band above
      if (!p.found) {
        svg.appendChild(svgEl("line", {
          x1: x, x2: x, y1: chartH - 22, y2: chartH - 17, class: "journey-notfound-tick",
        }));
        return;
      }
      const y = journeyRowY(p.lane);
      const color = subjectColor(p.lane, p.community_label);

      if (prevFoundIdx !== null && periods[prevFoundIdx].has_data) {
        const px = journeyColX(prevFoundIdx);
        const py = journeyRowY(periods[prevFoundIdx].lane);
        svg.appendChild(svgEl("line", {
          x1: px, y1: py, x2: x, y2: y, class: "journey-segment", style: `stroke:${color}`,
        }));
      }

      const laneChanged = prevFoundIdx !== null && periods[prevFoundIdx].lane !== p.lane;
      svg.appendChild(svgEl("circle", {
        cx: x, cy: y, r: i === 0 || laneChanged ? 4.5 : 3, class: "journey-point", style: `fill:${color}`,
      }));

      // A real community-level change (changed_from_prev) that stayed
      // inside the same broad lane - the exact gap the caveat text below
      // names. Marked even though the line itself reads flat here, on
      // purpose, not smoothed over.
      if (p.changed_from_prev === true && !laneChanged) {
        svg.appendChild(svgEl("path", {
          d: `M ${x} ${y - 9} l 3.5 5.5 l -3.5 5.5 l -3.5 -5.5 z`, class: "journey-subchange-tick",
        }));
      }

      if (!labeledLanes.has(p.lane)) {
        labeledLanes.add(p.lane);
        labelsToRender.push({ x, y, color, text: subjectShort(p.lane, p.community_label) });
      }
      prevFoundIdx = i;
    });

    labelsToRender.forEach(({ x, y, color, text }) => {
      const g = svgEl("g", { class: "journey-label" });
      g.appendChild(svgEl("circle", { cx: x + 7, cy: y - 8, r: 3, style: `fill:${color}` }));
      g.appendChild(svgEl("text", { x: x + 13, y: y - 5, style: `fill:${color}` }, text));
      svg.appendChild(g);
    });

    periods.forEach((p, i) => {
      svg.appendChild(svgEl("text", {
        x: journeyColX(i), y: chartH - 4, class: "journey-tick-label", "text-anchor": "middle",
      }, p.period.split("-")[0]));
    });

    return svg;
  }

  function renderJourney(container, data) {
    container.innerHTML = "";
    const found = data.periods.filter((p) => p.found);
    const lanes = new Set(found.map((p) => p.lane));
    const caption = document.createElement("p");
    caption.className = "journey-caption";
    caption.textContent = found.length
      ? `"${data.word}" is attested in ${found.length} of ${data.periods.length} periods, touching ${lanes.size} subject area${lanes.size === 1 ? "" : "s"}.`
      : `"${data.word}" does not appear in any period of this corpus.`;
    container.appendChild(caption);
    if (!found.length) return;

    const scroll = document.createElement("div");
    scroll.className = "journey-scroll";
    scroll.appendChild(buildJourneySvg(data.periods));
    container.appendChild(scroll);

    const caveat = document.createElement("p");
    caveat.className = "journey-caveat";
    caveat.innerHTML = "Coarser than the rest of this page on purpose: a flat line means the word stayed in the " +
      "same broad subject area, even if the specific group underneath changed - the small red ticks mark that " +
      "case, a real change the line itself does not show.";
    container.appendChild(caveat);
  }

  async function fetchTimeline(word, region) {
    const regionParam = region ? `?region=${encodeURIComponent(region)}` : "";
    const res = await fetch(`/api/timeline/${encodeURIComponent(word)}${regionParam}`);
    return res.json();
  }

  // Wires the Neighbours/Journey toggle wherever a word is drilled into
  // (/search's own results, /timeline's per-period drill-in) - one
  // implementation so the two views can't quietly drift apart, same
  // reasoning as the rest of this file. els: {toggle, neighbors, journey}
  // DOM elements the caller already has. getWord()/getRegion() are read
  // live at click/refresh time rather than passed once, since the word and
  // region on screen change after this is wired up. Returns {refresh}: the
  // caller invokes it every time it shows a new word so an already-open
  // Journey tab reloads instead of quietly showing the previous word.
  function attachJourneyToggle(els, getWord, getRegion) {
    let cacheKey = null;
    let cachedData = null;
    let view = "neighbors";

    async function load() {
      if (view !== "journey") return;
      const word = getWord();
      const region = getRegion();
      const key = `${word}::${region || ""}`;
      if (key === cacheKey) {
        renderJourney(els.journey, cachedData);
        return;
      }
      els.journey.innerHTML = `<p class="journey-caption">Loading...</p>`;
      const data = await fetchTimeline(word, region);
      cachedData = data;
      cacheKey = key;
      renderJourney(els.journey, data);
    }

    function setView(v) {
      view = v;
      els.toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
      els.neighbors.classList.toggle("hidden", v !== "neighbors");
      els.journey.classList.toggle("hidden", v !== "journey");
      load();
    }

    els.toggle.innerHTML = `
      <button type="button" data-view="neighbors" class="active">Neighbours</button>
      <button type="button" data-view="journey">Journey</button>
    `;
    els.toggle.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-view]");
      if (btn) setView(btn.dataset.view);
    });

    return { refresh: load };
  }

  return {
    SUBJECTS, SUBJECT_ORDER, NO_SUBJECT,
    isUncertainLabel, subjectShort, subjectColor, subjectDot,
    formatGroup, formatGroupHtml, fetchDetail, render,
    attachJourneyToggle,
  };
})();
