"""
config.py
─────────
Loads environment variables from .env and parses watchlist.yaml.
All other modules import their configuration from here.
"""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Playwright ────────────────────────────────────────────────────────────────
BROWSER_DATA_DIR: str = os.getenv("BROWSER_DATA_DIR", "./browser_data")
HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"

# ── Matching ──────────────────────────────────────────────────────────────────
FUZZY_THRESHOLD: int = int(os.getenv("FUZZY_THRESHOLD", "85"))
MAX_AGE_DAYS: int = int(os.getenv("MAX_AGE_DAYS", "15"))

# ── Facebook ──────────────────────────────────────────────────────────────────
_fb_raw = os.getenv("FB_GROUP_URLS", "")
FB_GROUP_URLS: list[str] = [u.strip() for u in _fb_raw.split(",") if u.strip()]


def load_watchlist() -> dict:
    """
    Parses watchlist.yaml and returns a dict with:
        {
            "location": "Ho Chi Minh",     # always fully normalized
            "items": [
                {"model": "iPhone 15 Pro", "condition": "used", "threshold": 20000000},
                ...
            ]
        }
    """
    watchlist_path = Path(__file__).parent / "watchlist.yaml"

    if not watchlist_path.exists():
        raise FileNotFoundError(
            f"watchlist.yaml not found at {watchlist_path}. "
            "Copy watchlist.yaml from the repo and fill in your preferences."
        )

    with open(watchlist_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Normalize top-level location
    raw_location = data.get("location", "")
    from matcher import normalize_location  # deferred to avoid circular import

    data["location"] = normalize_location(str(raw_location))

    # Validate watchlist items
    items = data.get("watchlist", [])
    if not items:
        raise ValueError("watchlist.yaml has no items in 'watchlist'. Add at least one model.")
    if len(items) > 5:
        raise ValueError("watchlist.yaml can have a maximum of 5 models.")

    for item in items:
        if "model" not in item or "threshold" not in item or "condition" not in item:
            raise ValueError(
                f"Every watchlist item must have 'model', 'condition', and 'threshold'. "
                f"Bad entry: {item}"
            )
        item["condition"] = item["condition"].lower()
        if item["condition"] not in ("new", "used", "any"):
            raise ValueError(
                f"'condition' must be 'new', 'used', or 'any'. Got: {item['condition']}"
            )
        item["threshold"] = int(item["threshold"])
        item["min_price"] = int(item.get("min_price", 0))

    data["items"] = items
    return data


def validate_config() -> None:
    """Runs at startup to catch missing critical config early."""
    errors = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set in .env")
    if not TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID is not set in .env")
    if errors:
        raise EnvironmentError(
            "Missing required configuration:\n" + "\n".join(f"  • {e}" for e in errors)
        )
