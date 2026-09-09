// Chat UI for the grounded discovery engine (/api/chat). Renders the user's
// question, the assistant's answer, and - the point of the whole thing - the
// Evidence records behind that answer, each as a readable row: its claim
// (plain language, primary), a small reliability swatch, and its citation
// (quiet, technical, secondary). Nothing here interprets the data; it only
// displays what the server returned.

(function () {
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function addBubble(role, text) {
    const wrap = el("div", "chat-msg chat-msg-" + role);
    const body = el("div", "chat-bubble");
    body.textContent = text;
    wrap.appendChild(body);
    log.appendChild(wrap);
    log.scrollTop = log.scrollHeight;
    return wrap;
  }

  // Tier letters for the compact swatch - never color-only (a deuteranope
  // or screen-reader user needs the letter/aria-label too), but a full word
  // repeated on every one of a dozen rows was most of the readability
  // problem this rewrite fixes. Full word still lives in the title tooltip
  // and an sr-only span.
  const TIER_LETTER = { measured: "M", inferred: "I", unreliable: "F" };
  const TIER_WORD = { measured: "measured", inferred: "inferred", unreliable: "flagged" };

  // One Evidence record -> one compact row. The claim (already plain-language
  // from tools.py, see src/rag/tools.py's 2026-09-08 rewrite) is the primary
  // text on the line; the tier is a small color+letter badge (not a repeated
  // word) and the citation drops the period (already in the claim text) to
  // cut redundancy. A response with many similar-shaped facts (e.g. a word's
  // per-period trajectory) used to render each as its own bordered,
  // multi-line card - readable alone, but a wall of near-identical boxes once
  // stacked. Rows fix the compounding case without losing anything: same
  // claim, same tier, same citation, just far less chrome per line.
  function evidenceItem(ev) {
    const tier = ev.tier || "inferred";
    const item = el("div", "ev-item ev-" + tier);

    const swatch = el("span", "ev-swatch ev-swatch-" + tier);
    swatch.textContent = TIER_LETTER[tier] || "?";
    swatch.title = TIER_WORD[tier] || tier;
    const swatchLabel = el("span", "sr-only", (TIER_WORD[tier] || tier) + ": ");
    swatch.appendChild(swatchLabel);
    item.appendChild(swatch);

    item.appendChild(el("p", "ev-claim", ev.claim || ""));

    const citeParts = (ev.citation || ev.source || "")
      .split(" · ")
      .filter((p) => !/^\d{4}-\d{4}$/.test(p));
    item.appendChild(el("span", "ev-cite", citeParts.join(" · ")));

    if (ev.caveat) {
      const details = el("details", "caveat ev-caveat");
      details.appendChild(el("summary", null, "why this one needs care"));
      details.appendChild(el("p", null, ev.caveat));
      item.appendChild(details);
    }
    return item;
  }

  // The whole panel is one <details> disclosure, closed by default - the
  // Perplexity/Claude/ChatGPT pattern this is headed toward: the answer
  // reads on its own, and "how do I know that" is a single deliberate click
  // away rather than a wall of citations sitting under every reply whether
  // you asked for them or not.
  function addEvidence(evidence) {
    if (!evidence || !evidence.length) return;
    const panel = el("details", "ev-panel");
    const summary = el("summary", "ev-panel-title", `Sources (${evidence.length})`);
    panel.appendChild(summary);
    const list = el("div", "ev-list");
    evidence.forEach((ev) => list.appendChild(evidenceItem(ev)));
    panel.appendChild(list);

    log.appendChild(panel);
    log.scrollTop = log.scrollHeight;
  }

  function setBusy(busy) {
    sendBtn.disabled = busy;
    input.disabled = busy;
    sendBtn.textContent = busy ? "Thinking…" : "Ask";
  }

  async function ask(question) {
    addBubble("user", question);
    setBusy(true);
    const thinking = addBubble("bot", "…");
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        thinking.querySelector(".chat-bubble").textContent =
          (data && data.error) ? data.error : "Something went wrong.";
        thinking.classList.add("chat-msg-error");
        return;
      }
      thinking.querySelector(".chat-bubble").textContent =
        data.answer || "(no answer)";
      if (data.provider) {
        thinking.appendChild(el("div", "chat-provider", "via " + data.provider));
      }
      addEvidence(data.evidence);
    } catch (e) {
      thinking.querySelector(".chat-bubble").textContent =
        "Could not reach the server.";
      thinking.classList.add("chat-msg-error");
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    ask(q);
  });

  // Enter sends, Shift+Enter makes a newline.
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  document.querySelectorAll(".chat-example").forEach((b) => {
    b.addEventListener("click", () => {
      input.value = b.textContent.trim();
      input.focus();
    });
  });
})();
