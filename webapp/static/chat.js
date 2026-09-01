// Chat UI for the grounded discovery engine (/api/chat). Renders the user's
// question, the assistant's answer, and - the point of the whole thing - the
// Evidence records behind that answer, each as a chip carrying its reliability
// tier and citation. Nothing here interprets the data; it only displays what
// the server returned.

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

  // one Evidence record -> a chip. tier drives the colour; citation is the
  // checkable "region · period · resolution · metric" tag; the claim shows on
  // hover so the panel stays scannable.
  function evidenceChip(ev) {
    const chip = el("span", "ev-chip ev-" + (ev.tier || "inferred"));
    chip.appendChild(el("span", "ev-tier", ev.tier || ""));
    chip.appendChild(el("span", "ev-cite", ev.citation || ev.source || ""));
    chip.title = (ev.claim || "") + (ev.caveat ? "\n\n⚠ " + ev.caveat : "");
    return chip;
  }

  function addEvidence(evidence) {
    if (!evidence || !evidence.length) return;
    const panel = el("div", "ev-panel");
    panel.appendChild(el("div", "ev-panel-title", "Evidence used"));
    const list = el("div", "ev-list");
    evidence.forEach((ev) => list.appendChild(evidenceChip(ev)));
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
