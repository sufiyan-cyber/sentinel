// Aura — wallet, shopping and payment UI for the chat page.
// Loaded after app.js and shares its globals (source, thread, addBubble, ...).
// Nothing here mentions the defense: the user sees a wallet, a shop, a
// receipt, and — when a payment is over their budget — a question.

(() => {
  const inr = (paise) => {
    const n = Math.abs(Number(paise) || 0) / 100;
    return "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
  };
  const el = (tag, cls, html) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  };
  const on = (name, fn) => source.addEventListener(name, (e) => fn(JSON.parse(e.data)));
  const place = (node) => { clearHero(); thread.appendChild(node); enter(node); stick(); };

  // ------------------------------------------------------------- wallet
  const pill = $("pill-wallet");
  const amount = $("wallet-amount");
  const pop = $("wallet-pop");
  let balance = null;
  let wallet = null;

  function renderPop() {
    if (!wallet) return;
    const rows = (wallet.ledger || []).slice().reverse().map((r) =>
      `<li><span>${r.title || r.ref}</span><b class="${r.amount_paise > 0 ? "in" : ""}">${r.amount_paise > 0 ? "+" : "−"}${inr(r.amount_paise)}</b></li>`).join("");
    const typed = document.getElementById("wp-amount")?.value || "";
    pop.innerHTML = `<div class="wp-head"><span>Wallet</span><b>${inr(wallet.balance_paise)}</b></div>
      <form class="wp-add" id="wp-add">
        <label for="wp-amount">Add money</label>
        <div class="wp-row">
          <span>₹</span><input id="wp-amount" inputmode="decimal" autocomplete="off" placeholder="Amount" aria-label="Amount in rupees">
          <button class="btn primary" type="submit">Add</button>
        </div>
        <div class="wp-quick">${[500, 1000, 5000].map((v) => `<button type="button" data-v="${v}">+₹${v.toLocaleString("en-IN")}</button>`).join("")}</div>
        <div class="wp-msg" id="wp-msg" role="status"></div>
      </form>
      <ul>${rows || '<li class="none">No activity yet</li>'}</ul>
      <div class="wp-foot" id="wp-limit"></div>`;
    document.getElementById("wp-amount").value = typed;
    wireTopup();
  }

  on("wallet", (d) => {
    wallet = d;
    const from = balance ?? d.balance_paise;
    balance = d.balance_paise;
    if (reduced || from === balance) {
      amount.textContent = inr(balance);
    } else {
      const start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / 800);
        amount.textContent = inr(from + (balance - from) * (1 - Math.pow(1 - t, 3)));
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      pill.classList.add("pulse");
      setTimeout(() => pill.classList.remove("pulse"), 900);
    }
    pill.classList.toggle("low", balance / d.opening_paise < 0.25);
    renderPop();
  });

  let topupNote = "";
  function wireTopup() {
    const form = document.getElementById("wp-add");
    const field = document.getElementById("wp-amount");
    const msg = document.getElementById("wp-msg");
    if (topupNote) { msg.textContent = topupNote; msg.className = "wp-msg ok"; }
    form.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => { field.value = b.dataset.v; field.focus(); }));
    form.onsubmit = async (e) => {
      e.preventDefault();
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      msg.className = "wp-msg"; msg.textContent = "Adding…";
      try {
        const r = await fetch("/api/wallet/topup", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ amount_inr: field.value }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || "Could not add money.");
        const via = d.provider === "cashfree" ? "Cashfree sandbox order" : d.provider === "razorpay" ? "Razorpay test order" : "demo order";
        topupNote = `Added. ${via} ${d.order_id}`;
        field.value = "";
        renderPop();
      } catch (err) {
        msg.className = "wp-msg bad"; msg.textContent = err.message;
        button.disabled = false;
      }
    };
  }

  pop.onclick = (e) => e.stopPropagation();
  pill.onclick = (e) => { e.stopPropagation(); topupNote = ""; renderPop(); pop.classList.toggle("open"); };
  document.addEventListener("click", () => pop.classList.remove("open"));

  // --------------------------------------------------------- shop view
  let shopCard = null;
  on("shop_view", (d) => {
    if (!shopCard) {
      shopCard = el("div", "shopview",
        `<div class="sv-bar"><i></i><i></i><i></i><span></span></div><iframe title="SparrowMart" loading="lazy"></iframe>`);
      place(shopCard);
    }
    shopCard.querySelector("span").textContent = `sparrowmart.local${decodeURIComponent(d.url)}`;
    shopCard.querySelector("iframe").src = d.url;
    stick();
  });

  // ----------------------------------------------------------- payment
  let noted = false;
  on("payment", (d) => {
    if (d.status === "paid") {
      const stamp = d.mode !== "test" ? "DEMO PAYMENT" : d.provider === "cashfree" ? "CASHFREE · SANDBOX" : "RAZORPAY · TEST MODE";
      place(el("div", "receipt",
        `<div class="rc-top"><span class="rc-ok">Paid</span><span class="rc-stamp">${stamp}</span></div>
         <div class="rc-amount">${inr(d.amount_paise)}</div>
         <div class="rc-item"></div>
         <dl><dt>Order</dt><dd class="mono"></dd><dt>Paid from</dt><dd>Wallet</dd></dl>`));
      const card = thread.lastElementChild;
      card.querySelector(".rc-item").textContent = d.title;
      card.querySelector("dd.mono").textContent = d.order_id;
    } else if (!noted) {
      noted = true;
      const note = el("div", "paynote");
      note.textContent = d.reason === "insufficient funds"
        ? "Payment not completed: the wallet balance is too low."
        : "Payment not completed. Nothing was charged to your wallet.";
      place(note);
    }
  });

  // ------------------------------------------- over-budget confirmation
  on("stepup_request", (d) => {
    if (d.kind !== "mandate") return;
    const box = el("div", "approve",
      `<div class="ap-kicker">Over your budget</div>
       <div class="ap-title">Pay <b></b> for this?</div>
       <div class="ap-item"></div>
       <div class="ap-why"></div>
       <div class="ap-row"><button class="btn" data-ok="0">Decline</button><button class="btn primary" data-ok="1">Approve payment</button></div>`);
    box.querySelector("b").textContent = inr(d.amount_paise);
    box.querySelector(".ap-item").textContent = d.title || d.sku || "";
    box.querySelector(".ap-why").textContent = `You asked to stay within ${inr(d.max_paise)}. This is ${inr(d.amount_paise - d.max_paise)} more.`;
    box.querySelectorAll("button").forEach((b) => {
      b.onclick = async () => {
        box.querySelectorAll("button").forEach((x) => (x.disabled = true));
        box.classList.add(b.dataset.ok === "1" ? "is-approved" : "is-declined");
        box.querySelector(".ap-kicker").textContent = b.dataset.ok === "1" ? "Approved" : "Declined";
        await fetch("/api/stepup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved: b.dataset.ok === "1" }),
        });
      };
    });
    place(box);
  });

  on("mandate", (d) => {
    const foot = document.getElementById("wp-limit");
    if (foot) foot.textContent = `Budget for the current request: ${inr(d.max_paise)}`;
  });

  // ------------------------------------- runs started from the dashboard
  on("scenario_start", (d) => {
    thread.innerHTML = "";
    trace = null;
    shopCard = null;
    noted = false;
    clearHero();
    if (d.sample) {
      const step = addStep(`Attached document: ${d.sample}`);
      finishStep(step, true);
      trace = null;
    }
    addBubble("me", d.prompt);
    busy = true;
    $("btn-send").disabled = true;
    window.auraOrb?.busy(true);
  });

  on("done", () => { shopCard = null; noted = false; });

  // --------------------------------------- movie seats and flight look-ups
  const row = (dl, label, value) => {
    if (!value) return;
    const dt = el("dt"); dt.textContent = label;
    const dd = el("dd"); dd.textContent = value;
    dl.append(dt, dd);
  };

  on("booking", (d) => {
    const f = d.fields || {};
    const movie = d.kind === "movies";
    const card = el("div", `booking ${movie ? "is-movie" : "is-flight"}`);
    const head = el("div", "bk-head");
    head.innerHTML = `${icon(movie ? "i-ticket" : "i-plane", "", 16)}<span></span><em></em>`;
    head.querySelector("span").textContent = movie ? "BookMyShow" : "MakeMyTrip";
    head.querySelector("em").textContent = f.Status || "Not booked";
    card.appendChild(head);

    const title = el("div", "bk-title");
    if (movie) {
      title.textContent = f.Movie || "Movie";
    } else {
      const [from, to] = String(f.Route || "").split(/\s+to\s+/i);
      title.innerHTML = to ? "<b></b><i>→</i><b></b>" : "<b></b>";
      const parts = title.querySelectorAll("b");
      parts[0].textContent = from || f.Route || "Flight";
      if (parts[1]) parts[1].textContent = to;
    }
    card.appendChild(title);

    const dl = el("dl");
    if (movie) {
      row(dl, "Cinema", f.Cinema); row(dl, "Date", f.Date); row(dl, "Showtime", f.Showtime);
      row(dl, "Prices", f.Prices); row(dl, "Free seats", f.Availability); row(dl, "Picked", f.Seats); row(dl, "Total", f.Price);
    } else {
      row(dl, "Date", f.Date); row(dl, "Airline", [f.Airline, f.Flight].filter(Boolean).join(" · "));
      row(dl, "Departs", f.Departure); row(dl, "Arrives", f.Arrival);
      row(dl, "Duration", f.Duration); row(dl, "Fare", f.Fare);
    }
    card.appendChild(dl);

    const more = f["Other shows"] || f["Other options"];
    if (more) {
      const other = el("div", "bk-more");
      other.textContent = `${movie ? "Also showing at" : "Other flights"}: ${more}`;
      card.appendChild(other);
    }
    if (d.screenshot) {
      const shot = el("img", "bk-shot");
      shot.alt = movie ? "The seat map as the assistant left it" : "The search results the assistant read";
      shot.src = `data:image/png;base64,${d.screenshot}`;
      shot.onclick = () => shot.classList.toggle("is-big");
      card.appendChild(shot);
    }
    const foot = el("div", "bk-foot");
    foot.innerHTML = "<span>Nothing was booked or paid for.</span>";
    if (d.url) {
      const link = el("a", "btn");
      link.href = d.url; link.target = "_blank"; link.rel = "noopener";
      link.textContent = movie ? "Open on BookMyShow" : "Open on MakeMyTrip";
      foot.appendChild(link);
    }
    card.appendChild(foot);
    place(card);
  });

  // ------------------------------- the rail highlights whichever panel is open
  const PANELS = {
    "drawer-files": "btn-nav-files", "drawer-history": "btn-nav-history",
    "modal-models": "btn-nav-models", "modal-settings": "btn-nav-settings",
  };
  const syncRail = () => {
    const open = Object.keys(PANELS).find((id) => $(id)?.classList.contains("is-open"));
    document.querySelectorAll(".rail-btn").forEach((b) => b.classList.remove("active"));
    $(open ? PANELS[open] : "btn-nav-home")?.classList.add("active");
  };
  Object.keys(PANELS).forEach((id) => {
    const node = $(id);
    if (node) new MutationObserver(syncRail).observe(node, { attributes: true, attributeFilter: ["class"] });
  });
})();
