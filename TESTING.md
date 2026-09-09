# Crypto & Forex Watchman — Testing Guide

## Prerequisites

1. Bot is running locally or via Docker
2. `.env` configured with `TELEGRAM_BOT_TOKEN`
3. Redis running (for listing monitor dedup)
4. PostgreSQL running (Docker auto-starts it)

---

## Interface Overview

The bot has two interfaces — **Reply Keyboard** (persistent menu buttons at the bottom) and **commands** (still work for power users).

### Reply Keyboard Buttons

After `/start`, a permanent menu appears at the bottom of Telegram:

| Row | Buttons |
|-----|---------|
| 1 | `📊 Portfolio` `🔔 Alerts` `📈 Analytics` |
| 2 | `📢 Sentiment` `🐋 Whales` `🧠 Digest` |
| 3 | `⚙️ Settings` `❓ Help` |

Tap any button to navigate. Sub-menus use **inline buttons** inside the message.

---

## 1. Getting Started

### /start — Registration + Menu
```
/start
```
- Expected: Welcome message + reply keyboard menu appears
- Verifies: User auto-created in DB, admin gets `pro` tier if `ADMIN_TELEGRAM_ID` matches

### ❓ Help
Tap `❓ Help` or type `/help`
- Expected: Alert types explained, commands still listed

### ⚙️ Settings
Tap `⚙️ Settings` or type `/settings`
- Expected: Shows Telegram ID, plan tier, portfolio count, active alerts

---

## 2. Portfolio Management

### ➕ Add Asset (via menu)
1. Tap `📊 Portfolio`
2. Tap inline button **➕ Add Asset**
3. Bot asks for symbol + price
4. Type: `BTC 62900`
- Expected: Confirmation "BTC added at 62,900.00"
- **Edge**: Type invalid text → error
- **Edge**: Free tier >5 assets → limit error
- **Edge**: Type `📊 Portfolio` or any menu button during input → cancels back to menu

### 📊 View Portfolio
Tap `📊 Portfolio`
- Expected: Lists all assets with current price, PnL%, entry price
- Shows asset count / limit

### ✖ Remove Asset (via menu)
1. Tap `📊 Portfolio`
2. Tap **✖ Remove Asset**
3. Tap the asset to remove
4. Confirm with **✅ Yes, remove**
- Expected: Asset + its alerts deleted
- **Edge**: No assets → "No assets to remove"

### Commands (still work)
```
/add_asset ETH 3400
/portfolio
/remove_asset ETH
```

---

## 3. Price Alerts

### ➕ Add Alert (via menu)
1. Tap `🔔 Alerts`
2. Tap **➕ Add Alert**
3. Tap an asset (inline buttons)
4. Choose alert type:
   - **📈 Above price** — price above target
   - **📉 Below price** — price below target
   - **📊 Move %** — ±X% from entry
   - **💹 Volume** — 24h volume exceeds $X
   - **🌊 Volatility** — daily move > Xx normal
   - **📢 Sentiment** — Reddit+news mentions > X
   - **🐋 Whale** — on-chain tx ≥ X units
5. Type the target value

- Expected: Alert set confirmation with ID
- **Edge**: Type menu button during input → cancels back

### 📋 View Alerts
Tap `🔔 Alerts`
- Expected: Lists all active alerts with ID, symbol, trigger description

### ✖ Remove Alert (via menu)
1. Tap `🔔 Alerts`
2. Tap **✖ Remove Alert**
3. Tap the alert to remove
4. Confirm

### Commands
```
/add_alert BTC above 70000
/add_alert BTC move 5
/add_alert BTC whale 10
/alerts
/remove_alert 1
```

---

## 4. Analytics & Intelligence

### 📈 Analytics
Tap `📈 Analytics` or type `/analytics`
- Expected: Best/worst performer, per-asset PnL breakdown

### 📢 Sentiment
Tap `📢 Sentiment` or type `/sentiment`
- Expected: Reddit + news mention counts per asset
- Verifies: Scans r/cryptocurrency + CoinTelegraph + CoinDesk RSS

### 🐋 Whales
Tap `🐋 Whales` or type `/whale`
- Expected: Recent large BTC (≥10 BTC) + ETH (≥100 ETH) transactions
- Without `ETHERSCAN_API_KEY`: BTC only

### 🧠 Digest
Tap `🧠 Digest` or type `/digest`
- Expected: AI-generated market summary or plain-text stats if no LLM key set
- Provider chain: OpenAI → OpenRouter → Groq → plain-text

---

## 5. Listing Monitor (Pro/Premium)

- Polls Binance, Bybit, Coinbase every 2 minutes
- Alerts only sent to `pro`/`premium` users
- Admin (`ADMIN_TELEGRAM_ID`) is auto-pro
- Requires Redis
- First run: cache init only (no alerts)
- Monitor logs:
  ```
  docker logs watchman_app -f | Select-String listing
  ```

---

## 6. Auto-Features

### Daily Digest
- Sent at 09:00 to Pro/Premium users via APScheduler
- Test: Temporarily change `hour=9` in `main.py` to test sooner

### Whale Auto-Alerts
- Every 30s within `check_all_alerts()` (no separate schedule)
- In-memory dedup (`_seen_whale_txids`) prevents repeat alerts

### Sentiment Auto-Alerts
- Checked every 15 min (rate-limit cooldown)
- `/add_alert BTC sentiment 10`

---

## 7. Admin Auto-Pro

- Set `ADMIN_TELEGRAM_ID` in `.env`
- First `/start` auto-grants `pro` plan
- `/settings` should show `PRO`

---

## 8. Infrastructure Checks

### Health Endpoint
```bash
curl http://localhost:8000/health
```
Expected: `{"status": "healthy", "bot_configured": true}`

### Docker Logs
```bash
docker compose logs -f watchman_app
```
Look for: scheduler started, bot polling, no errors

### Database
```bash
docker compose exec db psql -U postgres -d crypto_watchman -c "SELECT id, telegram_id, plan FROM users;"
```

### Redis
```bash
docker compose exec db_redis redis-cli keys '*'
```
Expected: `seen_listings:binance`, `seen_listings:bybit`, `seen_listings:coinbase`

---

## 9. Quick Smoke Test (Button Sequence)

```
/start
📊 Portfolio → ➕ Add Asset → "BTC 62900"
📊 Portfolio → ➕ Add Asset → "ETH 3400"
📊 Portfolio
🔔 Alerts → ➕ Add Alert → BTC → 📈 Above → "70000"
🔔 Alerts → ➕ Add Alert → BTC → 📊 Move → "5"
🔔 Alerts → ➕ Add Alert → BTC → 🐋 Whale → "10"
🔔 Alerts
📈 Analytics
📢 Sentiment
🐋 Whales
🧠 Digest
⚙️ Settings
🔔 Alerts → ✖ Remove Alert → (tap alert) → confirm
📊 Portfolio → ✖ Remove Asset → ETH → confirm
```

### Equivalent Command Sequence
```
/start
/add_asset BTC 62900
/add_asset ETH 3400
/portfolio
/add_alert BTC above 70000
/add_alert BTC move 5
/add_alert BTC volume 50000000000
/add_alert BTC volatility 2
/add_alert BTC sentiment 10
/add_alert BTC whale 10
/alerts
/analytics
/sentiment
/whale
/digest
/settings
/remove_alert 1
/remove_asset ETH
```
