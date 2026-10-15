#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import lxml.html
import requests

BASE_URL = "https://agrisnetodisha.ori.nic.in/"
STATE_FILE = Path(__file__).resolve().parent / "state.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def send_telegram_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def format_message(text, links):
    message = f"Agrisnet Odisha - What's New:\n\n{text}"
    if len(links) == 1:
        return f"{message}\n\nLink: {links[0]}"
    if len(links) > 1:
        links_list = "\n".join(f"- {link}" for link in links)
        return f"{message}\n\nLinks:\n{links_list}"
    return message


def parse_notifications(html):
    document = lxml.html.fromstring(html)
    notifications = []

    for li in document.xpath("//*[@id='WhatsNew']//li"):
        raw_text = " ".join(li.text_content().split())
        text = re.sub(r"\s*Click Here\.?\s*$", "", raw_text, flags=re.IGNORECASE).strip()
        if not text:
            continue

        links = []
        for anchor in li.xpath(".//a"):
            href = anchor.get("href")
            if href:
                resolved_url = urljoin(BASE_URL, href.strip())
                if resolved_url not in links:
                    links.append(resolved_url)

        fingerprint = f"{text.lower()}|{'|'.join(sorted(links))}".encode("utf-8")
        item_id = hashlib.sha256(fingerprint).hexdigest()

        notifications.append({"id": item_id, "text": text, "links": links})

    return notifications


def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "seen_ids": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"initialized": False, "seen_ids": []}


def save_state(seen_ids):
    data = {
        "initialized": True,
        "seen_ids": seen_ids[:300],
    }
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        sys.exit("Error: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variable.")

    state = load_state()
    seen_ids = set(state.get("seen_ids", []))
    is_initialized = state.get("initialized", False) and bool(seen_ids)

    response = requests.get(BASE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    notifications = parse_notifications(response.text)

    if not notifications:
        print("No notifications found on page.")
        return

    if not is_initialized:
        print("Initializing state. Sending latest notification...")
        first_item = notifications[0]
        send_telegram_message(
            bot_token,
            chat_id,
            format_message(first_item["text"], first_item["links"]),
        )
        all_ids = [item["id"] for item in notifications]
        save_state(all_ids)
        return

    new_notifications = [item for item in notifications if item["id"] not in seen_ids]
    if not new_notifications:
        print("No new notifications.")
        return

    for item in reversed(new_notifications):
        send_telegram_message(
            bot_token,
            chat_id,
            format_message(item["text"], item["links"]),
        )
        time.sleep(1.5)

    new_ids = [item["id"] for item in new_notifications]
    updated_seen_ids = new_ids + [
        item_id for item_id in state.get("seen_ids", []) if item_id not in set(new_ids)
    ]
    save_state(updated_seen_ids)
    print(f"Sent {len(new_notifications)} new notification(s).")


if __name__ == "__main__":
    main()
