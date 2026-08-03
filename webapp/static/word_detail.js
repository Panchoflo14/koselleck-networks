// Shared word-detail rendering, used by both /buscador (the original word
// search tab) and /timeline (each period card's drill-in, see the vault's
// wiki/timeline-feature-plan.md step 7 - "Drill-in via the existing
// /buscador endpoint and rendering, extracted for reuse"). No bundler in
// this project, so this is a plain global (window.WordDetail) loaded before
// either page's own script, not an ES module - matches how the rest of the
// webapp's static JS already works.
//
// Callers own their own DOM ids/markup and pass element references in, so
// two copies of this view (buscador's page-level one, timeline's per-card
// drill-in) can exist without id collisions or shared mutable state here.
const WordDetail = (() => {
  // Communities are numbered arbitrarily by Leiden - "#3" alone means
  // nothing to a historian. The plain-English label is the primary signal;
  // the number stays alongside it for anyone cross-referencing the
  // underlying data.
  function formatCommunity(id, label) {
    return label ? `${label} (#${id})` : `#${id}`;
  }

  async function fetchDetail(period, word, region) {
    const regionParam = region ? `?region=${encodeURIComponent(region)}` : "";
    const res = await fetch(`/api/search/${encodeURIComponent(period)}/${encodeURIComponent(word)}${regionParam}`);
    return res.json();
  }

  // els: {heading, meta, neighborsBody} - DOM elements the caller already
  // has (an <h2>/similar, a <dl>, a <tbody>). onWordClick(word), if given,
  // is called when a neighbour word is clicked, so each host page decides
  // what "look this word up too" means for it (buscador re-searches in
  // place; timeline could jump to that word's own timeline).
  function render(els, data, onWordClick) {
    els.heading.textContent = `"${data.word}" in ${data.period}`;

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

    els.meta.innerHTML = `
      <dt>Community</dt><dd>${formatCommunity(data.community, data.community_label)}</dd>
      <dt>Degree</dt><dd>${data.degree} connections within its top-15 neighbours</dd>
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
      commTd.textContent = formatCommunity(n.community, n.community_label);
      tr.append(wordTd, simTd, commTd);
      els.neighborsBody.appendChild(tr);
    });
  }

  return { formatCommunity, fetchDetail, render };
})();
