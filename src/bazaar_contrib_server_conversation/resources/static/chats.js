// The conversations page: on a wide screen a conversation opens beside the
// list without the list being drawn again, the newest when none is chosen;
// the messages start at the newest, or at the one the search found, and
// take in new ones as the update bus tells of them; a day is headed once
// however the messages came in; the agent at work counts on.
(() => {
  const wide = () => document.documentElement.dataset.layout === "wide";

  const mark = () => {
    const here = location.pathname + location.search;
    for (const row of document.querySelectorAll(".convs .conv")) {
      row.classList.toggle("on", row.getAttribute("href") === here);
    }
  };

  const open = (address, panes) => {
    htmx.ajax("GET", panes, { target: "#opened", swap: "innerHTML" });
    // htmx draws it again when the reader comes back to it
    if (address) history.pushState({ htmx: true }, "", address);
    mark();
  };

  // a day is headed where its first message is, whichever piece brought it
  const tidy = (messages) => {
    let day = null;
    for (const item of [...messages.children]) {
      if (item.classList.contains("day")) {
        if (item.dataset.day === day) item.remove();
        else day = item.dataset.day;
      } else if (item.dataset.day) day = item.dataset.day;
    }
  };

  // the words the search met, marked where the message says them
  const highlight = (bubble, query) => {
    const terms = query.split(/\s+/).filter(Boolean).map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!bubble || !terms.length) return;
    const pattern = new RegExp(`(${terms.join("|")})`, "gi");
    const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT);
    const texts = [];
    while (walker.nextNode()) texts.push(walker.currentNode);
    for (const text of texts) {
      const parts = text.data.split(pattern);
      if (parts.length < 2) continue;
      text.replaceWith(
        ...parts.map((part, index) => {
          if (index % 2 === 0) return document.createTextNode(part);
          const mark = document.createElement("mark");
          mark.textContent = part;
          return mark;
        }),
      );
    }
  };

  // the conversation opens at its newest, or at the message the search met;
  // pictures taking their room as they come keep it there
  const settle = (messages) => {
    tidy(messages);
    const hit = messages.querySelector(".msg.hit");
    const place = () => {
      if (hit) hit.scrollIntoView({ block: "center" });
      else messages.scrollTop = messages.scrollHeight;
    };
    if (hit) highlight(hit.querySelector(".bubble"), messages.dataset.mark || "");
    place();
    // until the reader scrolls by themselves
    let held = true;
    for (const name of ["wheel", "touchmove", "keydown"]) {
      messages.addEventListener(name, () => (held = false), { once: true, passive: true });
    }
    for (const picture of messages.querySelectorAll("img")) {
      if (!picture.complete) picture.addEventListener("load", () => held && place(), { once: true });
    }
  };

  const tick = () => {
    for (const part of document.querySelectorAll("#working[data-now]")) {
      part.dataset.drawn ||= String(Date.now());
      const now = Number(part.dataset.now) + Date.now() - Number(part.dataset.drawn);
      const words = JSON.parse(part.dataset.words);
      for (const timer of part.querySelectorAll("[data-since]")) {
        const seconds = Math.max(0, Math.floor((now - Number(timer.dataset.since)) / 1000));
        const [h, m, s] = [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60];
        const form = h ? words.h : m ? words.m : words.s;
        timer.textContent = form.replace("{h}", h).replace("{m}", m).replace("{s}", s);
      }
    }
  };

  // read back from the newest, the way to it shows below the messages, and
  // goes once the newest is in sight again
  document.addEventListener(
    "scroll",
    (event) => {
      const messages = event.target;
      const way = messages.id === "msgs" && messages.parentElement.querySelector(".to-latest:has([data-latest])");
      if (!way) return;
      const newest = [...messages.querySelectorAll(".msg, .sys")].at(-1);
      way.hidden = !newest || newest.getBoundingClientRect().top < messages.getBoundingClientRect().bottom;
    },
    true,
  );
  document.addEventListener("click", (event) => {
    if (!event.target.closest?.("[data-latest]")) return;
    const messages = document.getElementById("msgs");
    messages.scrollTo({ top: messages.scrollHeight, behavior: "smooth" });
  });

  // ahead of the page's own way of following links: what is handled here
  // goes no further
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[data-panes]");
    if (link && wide() && !event.metaKey && !event.ctrlKey && !event.shiftKey) {
      event.preventDefault();
      event.stopPropagation();
      open(link.getAttribute("href"), link.dataset.panes);
      return;
    }
    // the header's search keeps the search to the conversation
    const within = event.target.closest?.("[data-within]");
    if (within && within.tagName === "BUTTON") {
      const finder = document.querySelector(".top .finder");
      if (wide() && finder) {
        finder.querySelector("[data-within]").value = within.dataset.within;
        finder.querySelector("input[name=q]").focus();
      } else {
        htmx.ajax("GET", `/ui/chats/?q=&in=${encodeURIComponent(within.dataset.within)}`, {
          target: "body",
          swap: "innerMorph show:top",
          push: "true",
        });
      }
      event.stopPropagation();
      return;
    }
    // the header's reminders are those beside the messages
    const reminders = event.target.closest?.("[data-reminders]");
    if (reminders && wide()) {
      event.preventDefault();
      event.stopPropagation();
      document.getElementById("reminders")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, true);

  // the wide bar's agent picker and the phone list's both belong to the
  // filter form: only the one changed goes with it
  document.addEventListener("change", (event) => {
    const picker = event.target;
    if (!(picker instanceof HTMLSelectElement) || picker.name !== "agent" || picker.form?.id !== "filter") return;
    for (const other of picker.form.elements) {
      if (other !== picker && other.name === "agent") other.disabled = true;
    }
    picker.form.requestSubmit();
  });

  // what the conversation follows changed: the newer messages come in below
  document.addEventListener("bazaar:change", (event) => {
    const follower = event.target;
    if (follower.dataset?.topic !== "chats.thread") return;
    const messages = follower.closest(".msgs");
    if (messages.querySelector(".newer")) return;
    const last = [...messages.querySelectorAll(".msg[data-id], .sys[data-id]")].pop();
    if (!last) return;
    htmx.ajax("GET", `${follower.dataset.newer}?after=${encodeURIComponent(last.dataset.id)}`, {
      target: "#working",
      swap: "beforebegin",
    });
  });

  document.addEventListener("htmx:after:swap", (event) => {
    const target = event.target;
    // the whole page changed in place: it opens as a page loaded does
    if (event.detail.ctx?.target === document.body) {
      const messages = document.querySelector(".msgs");
      if (messages) settle(messages);
      first() || mark();
      return;
    }
    if (target.id === "opened") {
      const messages = target.querySelector(".msgs");
      if (messages) settle(messages);
      return;
    }
    if (target.matches?.(".pane.list")) return first() || mark();
    const messages = target.closest?.(".msgs") || document.querySelector(".msgs");
    if (!messages) return;
    tidy(messages);
    if (messages.dataset.below) {
      messages.scrollTop = messages.scrollHeight - Number(messages.dataset.below);
      delete messages.dataset.below;
    }
  });

  // older messages come in above those read: what was in view stays where
  // it was, the next older ones waiting until the reader scrolls up again
  document.addEventListener("htmx:before:swap", (event) => {
    const older = event.target;
    if (!older.classList?.contains("older")) return;
    const messages = older.closest(".msgs");
    messages.dataset.below = String(messages.scrollHeight - messages.scrollTop);
  });

  // a wide page with none chosen opens the first of its list, once there is
  // one; one chosen while the page was changing names itself in the address
  const first = () => {
    const opened = document.getElementById("opened");
    const row = document.querySelector(".convs .conv[data-panes]");
    if (!opened || opened.children.length || !row || !wide()) return false;
    if (location.pathname !== "/ui/chats/") return false;
    htmx.ajax("GET", row.dataset.panes, { target: "#opened", swap: "innerHTML" });
    row.classList.add("on");
    return true;
  };

  addEventListener("DOMContentLoaded", () => {
    const messages = document.querySelector(".msgs");
    if (messages) settle(messages);
    first() || mark();
    setInterval(tick, 1000);
  });
})();
