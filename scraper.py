#!/usr/bin/env python3
"""
Agrisnet Odisha - Telegram Alert Bot

Scrapes "What's New" announcements from https://agrisnetodisha.ori.nic.in/
and posts new updates to a Telegram channel or chat.

Architecture inspired by ezioMJ/Movie-Alert:
- Stateless execution triggered via GitHub Actions cron or manual dispatch.
- Persistent state stored in state.json committed back to the repository.
- Robust deduplication preventing duplicate Telegram notifications.
"""

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import lxml.html
import requests

# Base directory paths and state location
ROOT = Path(__file__).resolve().parent
STATE_PATH = Path(os.environ.get("STATE_PATH", ROOT / "state.json"))

# Configuration constants (overrideable via environment variables)
TARGET_URL = os.environ.get("TARGET_URL", "https://agrisnetodisha.ori.nic.in/")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
SEND_FIRST_ON_INIT = os.environ.get("SEND_FIRST_ON_INIT", "true").lower() in ("true", "1", "yes")
MAX_HISTORY_ITEMS = int(os.environ.get("MAX_HISTORY_ITEMS", "300"))

# Desktop browser headers to bypass basic user-agent filtering
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


def load_env_file(filepath: Path = ROOT / ".env") -> None:
    """Simple parser to load local .env variables if present, without requiring third-party libraries."""
    if not filepath.exists():
        return
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception as exc:
        print(f"[Warn] Failed reading .env: {exc}")


def load_json(path: Path, default: Any = None) -> Any:
    """Safely read and parse a JSON file."""
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        print(f"[Error] Failed to read {path}: {exc}")
        return default


def save_json(path: Path, data: Any) -> None:
    """Save data to JSON with UTF-8 encoding and clean indentation."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def fetch_page() -> str:
    """Fetch target HTML content directly."""
    resp = requests.get(
        TARGET_URL,
        headers=DEFAULT_HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


def clean_notification_text(raw_text: str) -> str:
    """Normalize text and strip trailing 'Click Here' boilerplate."""
    text = " ".join(raw_text.split())
    text = re.sub(r"\s*Click Here\.?\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def compute_item_id(text: str, links: List[str]) -> str:
    """Generate deterministic SHA-256 hash for deduplication."""
    canonical = " ".join(text.lower().split()) + "|" + "|".join(sorted(links))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_whats_new(html_content: str, base_url: str) -> List[Dict[str, Any]]:
    """
    Extract notification items from the '#WhatsNew' tab pane.
    Returns list of dicts with 'id', 'text', 'links'.
    """
    doc = lxml.html.fromstring(html_content)

    try:
        whats_new_elem = doc.get_element_by_id("WhatsNew")
    except KeyError:
        elems = doc.xpath("//*[translate(@id, 'WHATSNEW', 'whatsnew')='whatsnew']")
        if elems:
            whats_new_elem = elems[0]
        else:
            raise ValueError("Could not find the 'WhatsNew' notification container on the page.")

    li_elements = whats_new_elem.xpath(".//li")
    items: List[Dict[str, Any]] = []

    for li in li_elements:
        raw_text = li.text_content()
        cleaned_text = clean_notification_text(raw_text)
        if not cleaned_text:
            continue

        # Extract all hyperlinks and resolve relative URLs
        links: List[str] = []
        for a in li.xpath(".//a"):
            href = a.get("href")
            if href:
                full_url = urllib.parse.urljoin(base_url, href.strip())
                if full_url not in links:
                    links.append(full_url)

        item_id = compute_item_id(cleaned_text, links)
        items.append({
            "id": item_id,
            "text": cleaned_text,
            "links": links,
        })

    return items


def format_telegram_message(item: Dict[str, Any]) -> str:
    """
    Format notification in clean minimalist plain text with raw clickable links.
    Plain text avoids Markdown/HTML parsing crashes on special administrative characters.
    """
    text = item["text"]
    links = item.get("links", [])

    lines = [
        "Agrisnet Odisha - What's New:",
        "",
        text,
    ]

    if len(links) == 1:
        lines.append("")
        lines.append(f"Link: {links[0]}")
    elif len(links) > 1:
        lines.append("")
        lines.append("Links:")
        for link in links:
            lines.append(f"- {link}")

    return "\n".join(lines)


def send_telegram_alert(token: str, chat_id: str, message: str) -> None:
    """Send notification message to Telegram channel or chat."""
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }
    resp = requests.post(api_url, json=payload, timeout=30)
    resp.raise_for_status()


def main() -> int:
    """Main scraping and alerting workflow."""
    load_env_file()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        sys.exit("Error: Missing required environment variables 'TELEGRAM_BOT_TOKEN' and/or 'TELEGRAM_CHAT_ID'.")

    state = load_json(STATE_PATH, default={}) or {}
    seen_ids: List[str] = state.get("seen_ids", [])
    initialized: bool = state.get("initialized", False) and len(seen_ids) > 0

    # 1. Fetch live page
    try:
        html = fetch_page()
    except requests.RequestException as exc:
        print(f"[Error] Failed to fetch {TARGET_URL}: {exc}")
        return 0

    # 2. Parse notifications
    try:
        items = parse_whats_new(html, TARGET_URL)
    except Exception as exc:
        print(f"[Error] Failed to parse notifications: {exc}")
        return 1

    print(f"[Scrape] Found {len(items)} notifications on Agrisnet Odisha.")

    if not items:
        print("[Warn] No notification items parsed from page.")
        return 0

    current_ids = [item["id"] for item in items]

    # 3. Handle Initial Run (Bootstrap)
    if not initialized:
        print("[Init] First run detected. Initializing state database...")
        if SEND_FIRST_ON_INIT:
            first_item = items[0]
            msg = format_telegram_message(first_item)
            print("[Init] Posting 1st notification to Telegram...")
            send_telegram_alert(token, chat_id, msg)
            print("[Telegram] 1st notification sent successfully.")

        # Save all current items as seen to avoid flooding historical notifications
        state["initialized"] = True
        state["seen_ids"] = current_ids
        state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        save_json(STATE_PATH, state)
        print(f"[Init] State saved with {len(current_ids)} notifications marked as seen.")
        return 0

    # 4. Handle Subsequent Runs (Detect brand new updates)
    seen_set = set(seen_ids)
    new_items = [item for item in items if item["id"] not in seen_set]

    if not new_items:
        print("[Scraper] No new notifications found. (Everything up-to-date)")
        return 0

    print(f"[Scraper] Found {len(new_items)} NEW notification(s)!")

    # Post oldest new item first so telegram channel timeline stays chronological
    for idx, item in enumerate(reversed(new_items)):
        msg = format_telegram_message(item)
        print(f"[Alert {idx+1}/{len(new_items)}] Sending notification to Telegram...")
        send_telegram_alert(token, chat_id, msg)
        print(f"[Telegram] Alert {idx+1} sent.")
        # Polite rate limiting between multiple messages
        time.sleep(1.5)

    # 5. Persist updated state
    new_ids = [item["id"] for item in new_items]
    updated_seen_ids = new_ids + [i for i in seen_ids if i not in set(new_ids)]
    state["seen_ids"] = updated_seen_ids[:MAX_HISTORY_ITEMS]
    state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    save_json(STATE_PATH, state)
    print(f"[Scraper] State updated with {len(new_items)} new notification(s).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
