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

  return {
    SUBJECTS, SUBJECT_ORDER, NO_SUBJECT,
    isUncertainLabel, subjectShort, subjectColor, subjectDot,
    formatGroup, formatGroupHtml, fetchDetail, render,
  };
})();
