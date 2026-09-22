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

  async function loadState() {
    state = await api("/api/dashboard");
    try {
      state.live = await api("/api/trading/live");
    } catch (e) {
      state.live = null;
    }
    $("#planTag").style.display = "";
    renderAll();
    return state;
  }

  async function showTelegramLogin() {
    showError("");
    renderEmptyState("Sign in with Telegram to continue.");
    const login = $("#loginPanel");
    if (!login) return false;
    login.classList.remove("hidden");
    try {
      const meta = await api("/api/meta");
      window._botUsername = meta.bot_username;
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

        const codeBtn = document.createElement("button");
        codeBtn.type = "button";
        codeBtn.className = "redirect-btn code-login";
        codeBtn.textContent = "Authorize in the bot (works everywhere)";
        codeBtn.addEventListener("click", startCodeLogin);
        wrap.appendChild(codeBtn);

        holder.appendChild(wrap);
      }
    } catch (e) {}
    return false;
  }

  let codePollActive = false;

  async function startCodeLogin() {
    try {
      showError("");
      renderEmptyState("Getting a login code…");
      const r = await api("/api/auth/code", { method: "POST" });
      const box = $("#codeLoginBox");
      box.classList.remove("hidden");
      $("#codeText").textContent = r.code;
      $("#codeWait").textContent =
        "Open Telegram → @" + (window._botUsername || "the bot") +
        " → send: /login " + r.code;
      pollForLogin(r.code);
    } catch (e) {
      renderEmptyState(e.message);
    }
  }

  async function pollForLogin(code) {
    codePollActive = true;
    const box = $("#codeLoginBox");
    for (let i = 0; i < 75; i++) {
      if (!codePollActive) return;
      await new Promise((res) => setTimeout(res, 2000));
      try {
        const r = await api("/api/auth/poll?code=" + encodeURIComponent(code));
        if (r.token) {
          codePollActive = false;
          box.classList.add("hidden");
          setToken(r);
          await loadState();
          return;
        }
      } catch (e) {}
    }
    codePollActive = false;
    $("#codeWait").textContent =
      "Code expired. Repeat the login to get a fresh code.";
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
      await loadState();
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

  async function renderLive() {
    const panel = $("#livePanel .card-body");
    const live = state.live;
    if (!live) {
      panel.classList.add("empty");
      panel.textContent = "Live trading is not configured yet.";
      return;
    }
    panel.classList.remove("empty");

    const globalOn = !!live.live_enabled_global;
    $("#liveChip").textContent = globalOn ? "LIVE READY" : "DEMO / DRY-RUN";
    $("#liveChip").classList.toggle("chip-armed", globalOn);

    const conns = live.connections || [];
    const saved = live.saved_strategies || [];
    const trades = live.recent_live_trades || [];

    const connRows = conns.length
      ? conns
          .map(
            (c) =>
              rowHTML(
                `<div><b>${escapeHtml(c.label || c.platform || "cTrader")}</b><div class="sub">${escapeHtml(c.account_id)} · ${c.mode === "live" ? "💵 live" : "🧪 demo"} · token ${c.has_token ? "stored (encrypted)" : "missing"}</div></div>` +
                  `<div class="val"><button class="btn mini ${c.is_live ? "disabled" : ""}" data-arm="${c.id}" ${c.is_live ? "disabled" : ""}>${c.is_live ? "Armed ✓" : "Arm for trading"}</button></div>`
              )
          )
          .join("")
      : '<div class="sub" style="padding:8px 0">No broker connections yet. Connect a cTrader account below.</div>';

    const stratOptions = saved.length
      ? saved
          .map((s) => `<option value="${escapeHtml(s.key)}">${escapeHtml(s.name)}</option>`)
          .join("")
      : '<option value="">No saved strategies — will use Momentum preset</option>';

    const pending = trades.filter((t) => t.status === "PENDING_CONFIRM" || (t.awaiting_confirmation));
    const pendingRows = pending.length
      ? pending
          .map(
            (t) =>
              rowHTML(
                `<div><b>${t.symbol} ${t.direction}</b><div class="sub">Awaiting your ✅/❌ in Telegram</div></div>` +
                  `<div class="val warn"><b>PENDING</b></div>`
              )
          )
          .join("")
      : '<div class="sub" style="padding:8px 0">No trade is waiting for confirmation.</div>';

    const tradeRows = trades.length
      ? trades
          .map(
            (t) =>
              rowHTML(
                `<div><b>${t.symbol} ${t.direction}</b><div class="sub">${escapeHtml(t.strategy_name || t.strategy_key)} · ${t.dry_run ? "dry-run" : "REAL"} · ${escapeHtml((t.reason || t.status).slice(0, 60))}</div></div>` +
                  `<div class="${t.status === "PENDING_CONFIRM" ? "warn" : t.dry_run ? "flat" : t.status === "CLOSED" ? "up" : "accent"}"><b>${t.status}</b></div>`
              )
          )
          .join("")
      : '<div class="sub" style="padding:8px 0">No live executions yet.</div>';

    panel.innerHTML =
      `<div class="row"><div><b>Global live switch</b></div><div class="val ${globalOn ? "accent" : "warn"}">${globalOn ? "LIVE TRADING ENABLED" : "ONLY DEMO / DRY-RUN"}</div></div>` +
      '<div class="row"><div class="sub">No automatic execution</div><div class="val sub">Every order waits for your ✅ in Telegram</div></div>' +
      '<h4 class="section-h">Broker connections</h4>' +
      connRows +
      '<h4 class="section-h">Connect a cTrader account</h4>' +
      `<div class="live-form">
        <input id="liveLabel" class="inp" placeholder="Label (e.g. My cTrader account)" value="My cTrader account" />
        <select id="liveMode" class="inp">
          <option value="demo">🧪 Demo account (simulated funds)</option>
          <option value="live">💵 Live account (real money)</option>
        </select>
        <button id="liveOAuthBtn" class="btn btn-primary">Connect with cTID (OAuth)</button>
        <div class="sub" style="margin-top:6px">Step 1: create an app at id.ctrader.com → Open API → Your applications (set redirect URI to https://crypto-watchman.onrender.com/api/trading/live/ctid/callback). Step 2: paste the Client ID + Client Secret below, then click Connect with cTID.</div>
        <input id="liveAccount" class="inp" placeholder="Account ID (manual)" />
        <input id="liveToken" class="inp" type="password" placeholder="Access token (manual)" />
        <input id="liveClientId" class="inp" type="password" placeholder="Client ID (Open API app - REQUIRED for OAuth)" />
        <input id="liveClientSecret" class="inp" type="password" placeholder="Client secret (Open API app - REQUIRED for OAuth)" />
        <button id="liveSaveBtn" class="btn">Save connection (encrypted)</button>
        <div class="sub" style="margin-top:6px">One open trade per account — a new trade waits until the last one is fulfilled.</div>
      </div>` +
      '<h4 class="section-h">Run a setup</h4>' +
      `<div class="live-form">
        <select id="liveStrategy" class="inp">${stratOptions}</select>
        <input id="liveSymbol" class="inp" placeholder="Pair (e.g. BTCUSD)" value="BTCUSD" />
        <button id="liveDryRun" class="btn">Dry-run preview</button>
        <button id="liveGo" class="btn btn-danger">Propose trade (confirm in Telegram)</button>
      </div>` +
      '<h4 class="section-h">Awaiting confirmation</h4>' +
      pendingRows +
      '<h4 class="section-h">Recent executions</h4>' +
      tradeRows;

    $("#liveSaveBtn") &&
      $("#liveSaveBtn").addEventListener("click", () => saveLiveConnection());
    $("#liveOAuthBtn") &&
      $("#liveOAuthBtn").addEventListener("click", () => startCtidOAuth());
    $("#liveDryRun") &&
      $("#liveDryRun").addEventListener("click", () => runLiveTrade(true));
    const go = $("#liveGo");
    go && go.addEventListener("click", () => runLiveTrade(false));
    panel.querySelectorAll("[data-arm]").forEach((b) =>
      b.addEventListener("click", () => armLiveConnection(b.dataset.arm))
    );
  }

  function isTelegramWebApp() {
    return !!(window.Telegram && window.Telegram.WebApp);
  }

  function openOAuthUrl(url) {
    // The Telegram Mini App WebView blocks normal navigation to external hosts,
    // so external links must go through the platform's openExternalLink (it
    // opens the default browser). Outside Telegram we navigate in place so the
    // OAuth callback returns to the same tab.
    if (isTelegramWebApp()) {
      const tw = window.Telegram.WebApp;
      if (tw.openExternalLink) {
        tw.openExternalLink(url);
        return false;
      }
      const w = window.open(url, "_blank");
      if (w) return false;
    }
    window.location.href = url;
    return true;
  }

  async function startCtidOAuth() {
    const v = (id) => (document.getElementById(id) || {}).value || "";
    const label = v("liveLabel");
    const mode = v("liveMode") || "demo";
    try {
      const res = await api("/api/trading/live/ctid/start", {
        method: "POST",
        body: JSON.stringify({
          client_id: v("liveClientId"),
          client_secret: v("liveClientSecret"),
          mode,
          label,
        }),
      });
      if (res && res.authorize_url) {
        const sameTab = openOAuthUrl(res.authorize_url);
        if (sameTab && isTelegramWebApp()) {
          showError("Opening cTrader authorization…");
        }
        if (!sameTab) {
          showError(
            "Opening the cTrader grant page in your browser. If nothing opened, tap this link:\n" +
              res.authorize_url +
              "\n(After granting access you'll be returned here automatically.)"
          );
        }
      } else {
        showError(res.detail ? String(res.detail) : "Could not start authorization.");
      }
    } catch (e) {
      showError(e.message);
    }
  }

  function handleCtidReturn() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("ctid");
    if (!code) return;
    if (code === "ok") {
      showError("cTrader account connected. Toggle Arm for trading to begin.");
    } else {
      showError("cTrader authorization did not complete — try again.");
    }
    const url = new URL(window.location.href);
    url.searchParams.delete("ctid");
    url.searchParams.delete("reason");
    window.history.replaceState({}, "", url.toString());
  }

  async function saveLiveConnection() {
    try {
      const v = (id) => (document.getElementById(id) || {}).value || "";
      const res = await api("/api/trading/live/connections", {
        method: "POST",
        body: JSON.stringify({
          label: v("liveLabel"),
          account_id: v("liveAccount"),
          access_token: v("liveToken"),
          client_id: v("liveClientId"),
          client_secret: v("liveClientSecret"),
          mode: v("liveMode") || "demo",
          is_live: false,
        }),
      });
      await loadState();
      showError(res.detail ? String(res.detail) : "Connection saved (encrypted).");
    } catch (e) {
      showError(e.message);
    }
  }

  async function armLiveConnection(id) {
    try {
      await api(`/api/trading/live/connections/${id}/arm`, {
        method: "POST",
        body: JSON.stringify({ is_live: true }),
      });
      await loadState();
    } catch (e) {
      showError(e.message);
    }
  }

  async function runLiveTrade(dry) {
    const sym = (document.getElementById("liveSymbol") || {}).value || "BTCUSD";
    const strat = (document.getElementById("liveStrategy") || {}).value || "momentum";
    try {
      const res = await api("/api/trading/live/execute", {
        method: "POST",
        body: JSON.stringify({
          symbol: sym.trim().toUpperCase(),
          strategy_key: strat,
          dry_run: dry,
        }),
      });
      if (res.awaiting_confirmation) {
        showError("Proposal sent — confirm it in Telegram (✅ Confirm). Nothing was placed.");
      } else {
        showError(res.reason || (res.allowed ? "Dry-run preview recorded." : "Rejected."));
      }
      await loadState();
    } catch (e) {
      showError(e.message);
    }
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
      handleCtidReturn();
      if (await tokenFromFragment()) {
        await loadState();
        return;
      }
      const fromQuery = await tryLoginFromQuery();
      if (fromQuery) {
        await loadState();
        return;
      }
      const ok = await authenticate();
      if (!ok) return;
      await loadState();
    } catch (e) {
      renderEmptyState(e.message);
    }
  }

  function renderAll() {
    renderPortfolio();
    renderWallets();
    renderOpps();
    renderLive();
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