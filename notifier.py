"""
notifier.py
───────────
Sends Telegram alerts for found deals via the Telegram Bot API.

Message format:
    🔥 Deal Found!
    📱 iPhone 15 Pro — Cũ (used)
    💰 18,500,000₫  (7.5% below threshold)
    🏪 Source: Chotot
    📍 Ho Chi Minh
    🔗 https://...
"""

import logging
import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_deal_alert(
    *,
    title: str,
    price: int,
    pct_below: float,
    condition: str,
    source: str,
    location: str,
    url: str,
    matched_model: str,
    bot_token: str,
    chat_id: str,
) -> bool:
    """
    Send a formatted deal alert via Telegram.
    Returns True on success, False on failure (so caller can omit marking as alerted).
    """
    condition_label = {
        "new": "Mới (new)",
        "used": "Cũ (used)",
        "unknown": "Không rõ (unknown)",
    }.get(condition.lower(), condition)

    price_str = f"{price:,}₫".replace(",", ".")

    text = (
        f"🔥 *Deal Found!*\n"
        f"📱 *{_escape(matched_model)}* — {_escape(condition_label)}\n"
        f"💰 *{price_str}*  \\(_{pct_below:.1f}%_ below threshold\\)\n"
        f"🏪 Source: {_escape(source.title())}\n"
        f"📍 {_escape(location)}\n"
        f"📝 _{_escape(title)}_\n"
        f"🔗 {url}"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=bot_token),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        log.info("Alert sent for: %s", title)
        return True
    except requests.RequestException as exc:
        log.error("Failed to send Telegram alert: %s", exc)
        return False


def send_startup_ping(*, bot_token: str, chat_id: str) -> None:
    """Sends a simple ping when the bot starts so you know it's alive."""
    payload = {
        "chat_id": chat_id,
        "text": "✅ Phone Deal Tracker started\\. Monitoring listings\\.\\.\\.",
        "parse_mode": "MarkdownV2",
    }
    try:
        requests.post(
            TELEGRAM_API.format(token=bot_token),
            json=payload,
            timeout=15,
        ).raise_for_status()
        log.info("Startup ping sent.")
    except requests.RequestException as exc:
        log.warning("Could not send startup ping: %s", exc)


def _escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))
