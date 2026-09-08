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

  // One Evidence record -> a readable row. The claim (already plain-language
  // from tools.py, see src/rag/tools.py's 2026-09-08 rewrite) is the primary,
  // always-visible text - it used to be the *only* thing hidden behind a
  // hover tooltip, with the raw tier name and technical citation as the only
  // visible content, exactly backwards for a historian reader (and useless
  // on touch, where hover doesn't exist). Tier is now a small color-coded
  // swatch + word (still the project's own measured/inferred/unreliable
  // vocabulary - that's an established epistemic framework, not jargon to
  // rename), the citation moves to a quiet mono tag, and a caveat - when
  // present - uses the same <details class="caveat"> disclosure pattern the
  // rest of the app already uses (see graph.html/timeline.html) instead of
  // only surfacing in a tooltip.
  function evidenceItem(ev) {
    const tier = ev.tier || "inferred";
    const item = el("div", "ev-item ev-" + tier);

    item.appendChild(el("p", "ev-claim", ev.claim || ""));

    const meta = el("div", "ev-meta");
    const swatch = el("span", "ev-swatch ev-swatch-" + tier);
    swatch.textContent = tier === "unreliable" ? "flagged" : tier;
    meta.appendChild(swatch);
    meta.appendChild(el("span", "ev-cite", ev.citation || ev.source || ""));
    item.appendChild(meta);

    if (ev.caveat) {
      const details = el("details", "caveat ev-caveat");
      details.appendChild(el("summary", null, "why this one needs care"));
      details.appendChild(el("p", null, ev.caveat));
      item.appendChild(details);
    }
    return item;
  }

  function addEvidence(evidence) {
    if (!evidence || !evidence.length) return;
    const panel = el("div", "ev-panel");
    panel.appendChild(el("div", "ev-panel-title", "Sources for this answer"));
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
