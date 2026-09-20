/* Watchman Mini App - single page, no build step. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  let token = null;
  let state = null;

  const TELEGRAM = window.Telegram && window.Telegram.WebApp;

  function fmt(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    return n.toLocaleString(undefined, {
      minimumFractionDigits: digits ?? (n >= 1000 ? 2 : 4),
      maximumFractionDigits: digits ?? (n >= 1000 ? 2 : 4),
    });
  }

  function fmtUsd(n) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    return "$" + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function pctClass(v) {
    if (v === null || v === undefined) return "flat";
    return v > 0 ? "up" : v < 0 ? "down" : "flat";
  }

  function pctSign(v) {
    if (v === null || v === undefined) return "—";
    return (v > 0 ? "+" : "") + v.toFixed(2) + "%";
  }

  function rowHTML(children) {
    return `<div class="row">${children}</div>`;
  }

  function showError(msg) {
    const el = $("#errorBox");
    if (msg) {
      el.textContent = msg;
      el.classList.remove("hidden");
    } else {
      el.classList.add("hidden");
    }
  }

  function renderEmptyState(msg) {
    $$(".card-body").forEach((el) => {
      if ((el.textContent || "").trim() === "Loading…") {
        el.classList.add("empty");
        el.textContent = "Sign in via Telegram to load data.";
      }
    });
    showError(msg);
  }

  async function api(path, options) {
    const headers = { "Content-Type": "application/json" };
    if (token) headers["X-App-Token"] = token;
    const res = await fetch(path, { ...options, headers });
    if (res.status === 401) throw new Error("Session expired — reopen the Mini App from Telegram.");
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    return res.json();
  }

  function devTokenFromUrl() {
    return new URLSearchParams(window.location.search).get("dev_token");
  }

  function authFieldsFromQuery() {
    const q = new URLSearchParams(window.location.search);
    if (!(q.has("hash") && q.has("id") && q.has("auth_date"))) return null;
    const fields = {};
    ["id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash"].forEach((k) => {
      if (q.has(k)) fields[k] = q.get(k);
    });
    return fields;
  }

  async function tryLoginFromQuery() {
    const fields = authFieldsFromQuery();
    if (!fields) return false;
    try {
      showError("");
      const data = await api("/api/auth", {
        method: "POST",
        body: JSON.stringify({ widget: fields }),
      });
      setToken(data);
      history.replaceState({}, "", window.location.pathname);
      return true;
    } catch (e) {
      history.replaceState({}, "", window.location.pathname);
      renderEmptyState("Login failed: " + e.message);
      return false;
    }
  }

  async function authenticate() {
    if (token) return true;
    const body = {};
    if (TELEGRAM && TELEGRAM.initData) {
      body.init_data = TELEGRAM.initData;
    } else if (devTokenFromUrl()) {
      body.dev_token = devTokenFromUrl();
    } else {
      return await showTelegramLogin();
    }
    const data = await api("/api/auth", {
      method: "POST",
      body: JSON.stringify(body),
    });
    setToken(data);
    return true;
  }

  function setToken(data) {
    token = data.token;
    const plan = document.querySelector("#planTag");
    plan.textContent = data.user.plan.toUpperCase();
    plan.classList.add("accent");
    const login = $("#loginPanel");
    if (login) login.classList.add("hidden");
  }

  async function showTelegramLogin() {
    showError("");
    renderEmptyState("Sign in with Telegram to continue.");
    const login = $("#loginPanel");
    if (!login) return false;
    login.classList.remove("hidden");
    try {
      const meta = await api("/api/meta");
      const holder = $("#tg-login-widget");
      if (!meta.bot_username) {
        if (holder) {
          holder.innerHTML =
            '<div class="sub">Sign-in with Telegram is not configured for this deployment yet.</div>';
        }
        return false;
      }
      if (meta.bot_id && holder && !document.querySelector("#tg-login-widget .redirect-wrap")) {
        const url =
          "https://oauth.telegram.org/auth?bot_id=" + meta.bot_id +
          "&origin=" + encodeURIComponent(location.origin) +
          "&request_access=write&lang=en&return_to=" +
          encodeURIComponent(location.origin + "/api/auth/return");
        const wrap = document.createElement("div");
        wrap.className = "redirect-wrap";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "redirect-btn";
        btn.textContent = "Continue with the browser login →";
        btn.addEventListener("click", () => {
          console.log("watchman: redirect login ->", url);
          renderEmptyState("Opening Telegram authorization…");
          window.location.href = url;
        });
        wrap.appendChild(btn);
        const code = document.createElement("div");
        code.className = "meta";
        code.textContent = url;
        wrap.appendChild(code);
        holder.appendChild(wrap);
      }
    } catch (e) {}
    return false;
  }

  /* called by the Telegram Login Widget after the user approves */
  window.onTelegramAuth = async (user) => {
    try {
      showError("");
      const data = await api("/api/auth", {
        method: "POST",
        body: JSON.stringify({ widget: user }),
      });
      setToken(data);
      state = await api("/api/dashboard");
      renderAll();
    } catch (e) {
      renderEmptyState(e.message);
    }
  };

  function renderPortfolio() {
    const panel = $("#portfolioPanel .card-body");
    const pnlEl = $("#pnlRow");
    const items = state.portfolio || [];

    if (!items.length) {
      panel.classList.add("empty");
      panel.textContent = "No assets yet. Add them with 📊 Portfolio in the bot.";
      pnlEl.classList.add("hidden");
      return;
    }
    panel.classList.remove("empty");
    panel.innerHTML = items.map((p) =>
      rowHTML(
        '<div class="sub">' +
        `<h4 class="sym">${p.symbol}</h4>` +
        `<div class="sub">Entry ${fmt(p.entry_price)}</div>` +
        "</div>" +
        `<div class="val ${pctClass(p.change_pct)}">` +
        `<div>${p.price != null ? fmt(p.price) : "—"}</div>` +
        `<div class="sub ${pctClass(p.change_pct)}">${pctSign(p.change_pct)}</div>` +
        "</div>"
      )
    ).join("");

    const avg = state.portfolio_pnl_pct;
    if (avg !== null && avg !== undefined) {
      pnlEl.classList.remove("hidden");
      pnlEl.className = "pnl " + pctClass(avg);
      pnlEl.textContent = "Aggregate P/L " + pctSign(avg);
    } else {
      pnlEl.classList.add("hidden");
    }
  }

  function renderWallets() {
    const panel = $("#walletsPanel .card-body");
    const wallets = state.wallets || [];

    if (!wallets.length) {
      panel.classList.add("empty");
      panel.textContent = "No wallets connected. Use 👛 Wallet in the bot.";
      return;
    }
    panel.classList.remove("empty");
    const total = fmtUsd(state.wallet_total_usd);
    const rows = [
      `<div class="row"><div><b>Total on-chain value</b></div><div class="val accent"><b>${total}</b></div></div>`,
    ];
    wallets.forEach((w) => {
      const name = w.label || w.network;
      const balances = w.balances.length
        ? w.balances
            .slice(0, 5)
            .map((b) => `${b.symbol} ${fmt(b.quantity)}`)
            .join(" · ")
        : "no balances";
      rows.push(
        rowHTML(
          `<div><b>${name}</b><div class="sub">${w.address_short}</div></div>` +
          `<div class="val"><div>${fmtUsd(w.total_value_usd)}</div><div class="sub">${balances}</div></div>`
        )
      );
    });
    panel.innerHTML = rows.join("");
  }

  function renderOpps() {
    const panel = $("#oppsPanel .card-body");
    const opps = state.opportunities || [];

    if (!opps.length) {
      panel.classList.add("empty");
      panel.textContent = "No scores yet. Tap 💡 Opportunities in the bot and hit 🔄 Recompute.";
      return;
    }
    panel.classList.remove("empty");
    panel.innerHTML = opps
      .map((o) => {
        const parts = (o.components || [])
          .map((c) => `${c.source} ${c.score >= 0 ? "+" : ""}${c.score.toFixed(1)}`)
          .join(" · ");
        return rowHTML(
          `<div><b>${o.symbol}</b><div class="sub">${parts || "no catalysts"}</div></div>` +
          `<div class="val"><span class="badge ${o.priority}">${o.priority}</span><div class="${pctClass(o.total_score)}">${o.total_score >= 0 ? "+" : ""}${o.total_score.toFixed(0)}</div></div>`
        );
      })
      .join("");
  }

  function renderAlerts() {
    const panel = $("#alertsPanel .card-body");
    const alerts = state.alerts || [];
    if (!alerts.length) {
      panel.classList.add("empty");
      panel.textContent = "No active alerts.";
    } else {
      panel.classList.remove("empty");
      panel.innerHTML = alerts
        .map((a) =>
          rowHTML(
            `<div><b>${a.symbol}</b><div class="sub">${a.description}</div></div>` +
            `<div class="val flat">ID ${a.id}</div>`
          )
        )
        .join("");
    }

    const notifPanel = $("#notifsPanel .card-body");
    const notifs = state.notifications || [];
    if (!notifs.length) {
      notifPanel.classList.add("empty");
      notifPanel.textContent = "No notifications yet.";
    } else {
      notifPanel.classList.remove("empty");
      notifPanel.innerHTML = notifs
        .map((n) =>
          rowHTML(`<div class="desc">${escapeHtml(n.message)}</div><div class="sub"></div>`)
        )
        .join("");
    }
  }

  function renderNews() {
    const panel = $("#newsPanel .card-body");
    const news = state.news || [];
    if (!news.length) {
      panel.classList.add("empty");
      panel.textContent = "No AI news analysis yet. Check back after the next scan.";
      return;
    }
    panel.classList.remove("empty");
    panel.innerHTML = news
      .map(
        (n) =>
          `<div class="card2-row">
             <div class="row">
               <div><b>${n.symbol}</b> <span class="pill ${n.sentiment}">${n.sentiment_emoji} ${n.sentiment}</span></div>
               <div class="val sub">Impact ${n.impact_level} (${n.impact_score}/100) · Conf ${n.confidence}%</div>
             </div>
             ${n.reason ? `<div class="desc line-clamp">${escapeHtml(n.reason)}</div>` : ""}
             <div class="meta">${n.article_count} article(s) · ${n.direction} bias</div>
           </div>`
      )
      .join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function tokenFromFragment() {
    const m = window.location.hash.match(/#app_token=([^&]+)/);
    if (m) {
      token = decodeURIComponent(m[1]);
      history.replaceState({}, "", window.location.pathname);
      return true;
    }
    if (window.location.hash.indexOf("#app_error=1") === 0) {
      history.replaceState({}, "", window.location.pathname);
      renderEmptyState("Login failed — the signature from Telegram did not verify.");
      return false;
    }
    return false;
  }

  async function load() {
    try {
      showError("");
      if (await tokenFromFragment()) {
        state = await api("/api/dashboard");
        $("#planTag").style.display = "";
        renderAll();
        return;
      }
      const fromQuery = await tryLoginFromQuery();
      if (fromQuery) {
        state = await api("/api/dashboard");
        $("#planTag").style.display = "";
        renderAll();
        return;
      }
      const ok = await authenticate();
      if (!ok) return;
      state = await api("/api/dashboard");
      $("#planTag").style.display = "";
      renderAll();
    } catch (e) {
      renderEmptyState(e.message);
    }
  }

  function renderAll() {
    renderPortfolio();
    renderWallets();
    renderOpps();
    renderAlerts();
    renderNews();
  }

  /* Tab switching */
  $$(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".tab-panel").forEach((p) => {
        p.classList.toggle("hidden", p.id !== `tab-${btn.dataset.tab}`);
      });
    });
  });

  $("#refreshBtn").addEventListener("click", () => load());

  if (TELEGRAM) {
    TELEGRAM.ready();
    TELEGRAM.expand();
  }

  load(false);
})();