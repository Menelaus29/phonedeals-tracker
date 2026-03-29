# Phone Deal Tracker

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Self-Hosted](https://img.shields.io/badge/Self--Hosted-yes-orange)]()

A self-hosted, single-user phone deal tracker for Vietnam. Monitors **Chotot**, **Shopee**, **Tiki**, **Lazada**, and **Facebook Marketplace/Groups** for phone listings matching your watchlist, and sends Telegram alerts when a deal is found. At the moment, the location filtering and listing day ages filtering features are NOt working. The fuzzy matching threshold also probably needs them working. I'll fix them when I have the time lolol. Please open an issue if you find a bug or have a feature request. Contributions are of course greatly appreciated.
---

## Features

- **5 sources**: Chotot, Shopee VN, Tiki, Lazada, Facebook Marketplace & Groups (manually defined using group URLs)
- **Watchlist**: Define 1–5 phone models with price range + condition (new/used/any)
- **Price range filtering**: Set both a `threshold` (max price) and `min_price` (min price) per model — filters out accessories, cases, and other junk listings
- **Fuzzy matching**: RapidFuzz token-based matching with abbreviation expansion (`IP→iPhone`, `SS→Samsung`, `promax→pro max`)
- **Strict model matching**: `S24` never matches `S24+` or `S24 Ultra`
- **Location filtering**: Set your city — supports aliases (`HN/SG/TPHCM/...`)
- **Telegram alerts**: Title, price, % below threshold, condition, source, direct link
- **Facebook session**: One-time manual login detected automatically — no terminal interaction needed. Session saved to disk for all future headless runs
- **Facebook session monitoring**: Automatic Telegram alert if your Facebook session expires or a CAPTCHA is encountered mid-crawl
- **SQLite**: Deduplicates listings, never alerts twice for the same URL. Unsent alerts are retried automatically on next startup
- **Scheduler**: Standard sites every 30 min, Facebook every 60 min

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Telegram bot ([create one via @BotFather](https://t.me/BotFather))
- Your Telegram `chat_id` (DM [@userinfobot](https://t.me/userinfobot))

### 2. Clone & Install

```bash
git clone https://github.com/your-username/phonedeals-crawler.git
cd phonedeals-crawler

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

### 3. Configure

```bash
# Copy the environment template
cp .env.example .env
```

Edit `.env` and fill in:
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID` — your personal chat ID

Edit `watchlist.yaml` to set your location and target phones:

```yaml
location: HCM   # or: HN, DN, SG, TPHCM, ...

watchlist:
  - model: iPhone 15 Pro
    condition: used
    threshold: 20000000   # max price: 20 million VND
    min_price: 8000000    # min price: 8 million VND (filters out cases/accessories)

  - model: Samsung Galaxy S24
    condition: any
    threshold: 18000000
    min_price: 5000000
```

> `min_price` is optional. If omitted, there is no lower bound.

### 4. Run

```bash
python main.py
```

On the very **first run**, a browser window will open for you to log in to Facebook. The tracker **automatically detects when you've logged in** (by watching for the Marketplace icon to appear) and continues without any terminal input. The session is saved — all future runs will be fully headless.

---

## Project Structure

```
phonedeals-crawler/
├── main.py              # Entry point & scheduler
├── config.py            # .env + watchlist.yaml loading
├── db.py                # SQLite persistence (listings.db)
├── matcher.py           # Fuzzy + model-strict matching engine
├── notifier.py          # Telegram alert sender (HTML mode)
├── watchlist.yaml       # YOUR watchlist config (edit this)
├── .env                 # YOUR secrets (never commit this)
├── .env.example         # Template for .env
├── requirements.txt
├── crawlers/
│   ├── base_crawler.py  # Abstract base class + Listing dataclass
│   ├── chotot.py        # Chotot.com (JSON API)
│   ├── shopee.py        # Shopee VN (internal API)
│   ├── tiki.py          # Tiki.vn (v2 API)
│   ├── lazada.py        # Lazada VN (embedded JSON + DOM fallback)
│   └── facebook.py      # FB Marketplace + Groups (Playwright DOM)
└── tests/
    └── test_matcher.py  # Unit tests for matching engine
```

---

## Configuration Reference

### `.env`

| Key | Description | Default |
|-----|-------------|---------|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather | *required* |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID | *required* |
| `BROWSER_DATA_DIR` | Persistent session folder for Facebook | `./browser_data` |
| `HEADLESS` | Run browser headlessly (`true`/`false`) | `true` |
| `FUZZY_THRESHOLD` | Min. fuzzy match score (0–100) | `85` |
| `MAX_AGE_DAYS` | Max listing age in days to consider | `15` |
| `FB_GROUP_URLS` | Comma-separated Facebook Group URLs | *(empty)* |

### `watchlist.yaml`

| Field | Values | Required | Description |
|-------|--------|----------|-------------|
| `location` | `HN`, `HCM`, `SG`, `DN`, `HP`, `CT`, ... | ✅ | Your city (supports many aliases) |
| `model` | String | ✅ | Phone model to track (be specific!) |
| `condition` | `new` / `used` / `any` | ✅ | Filter by condition |
| `threshold` | Integer (VND) | ✅ | Max price you'd pay |
| `min_price` | Integer (VND) | ❌ | Min price — filters out accessories/cases |

> **Model specificity matters.** `Samsung Galaxy S24` will NOT match `S24+` or `S24 Ultra`. This is intentional — different hardware, different price range.

---

## Telegram Alert Format

```
🔥 Deal Found!
📱 Oppo Find X8 — Không rõ (unknown)
💰 9.250.000₫  (11.9% below threshold)
🏪 Source: Facebook
📍 Hanoi
📝 Oppo Find X8 Hồng 12/256
🔗 https://www.facebook.com/marketplace/item/...
```

If your Facebook session expires or a CAPTCHA is detected, you'll receive a separate alert with step-by-step recovery instructions.

---

## Location Aliases

| City | Accepted Aliases |
|------|-----------------|
| Hanoi | `HN`, `hn`, `ha noi`, `hà nội`, `hanoi` |
| Ho Chi Minh | `HCM`, `SG`, `tphcm`, `sai gon`, `saigon`, `tp hcm`, `tp.hcm` |
| Da Nang | `DN`, `da nang`, `danang`, `đà nẵng` |
| Hai Phong | `HP`, `hai phong`, `haiphong` |
| Can Tho | `CT`, `can tho`, `cantho` |

---

## Running Tests

```bash
python -m pytest tests/ -v
```

---

## Maintenance

### Reset the seen-listings database

If you change your watchlist thresholds and want to re-evaluate all previously scraped listings:

```bash
python -c "import sqlite3; conn = sqlite3.connect('listings.db'); conn.execute('DELETE FROM listings'); conn.commit(); print('Database cleared.')"
```

### Reset the Facebook session

If your Facebook session has expired and you need to log in again:

```bash
# Windows
rmdir /s /q browser_data

# macOS/Linux
rm -rf browser_data
```

Then restart `python main.py`. The browser will open for you to log in again.

---

## Notes

- **Shopee / Tiki / Lazada** only list new items — `used` condition watchlist items are automatically skipped for these sources.
- **Facebook** crawl automatically detects session expiry and sends a Telegram alert with recovery steps. You do not need to watch the logs.
- **Retries**: If a Telegram alert fails to send (e.g. network issue), it is retried automatically the next time the tracker starts.
- Logs are written to both stdout and `tracker.log`.

---

## License

MIT
