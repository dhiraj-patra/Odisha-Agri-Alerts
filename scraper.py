#!/usr/bin/env python3
import hashlib
import json
import os
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
    extra_links = [link for link in links if link not in text]
    if extra_links:
        return f"{text}\n\n" + "\n".join(extra_links)
    return text


def parse_notifications(html):
    document = lxml.html.fromstring(html)
    notifications = []

    for li in document.xpath("//*[@id='WhatsNew']//li"):
        raw_text = li.text_content()
        text = "\n".join(" ".join(line.split()) for line in raw_text.splitlines() if line.strip())
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
        return {"seen_hashes": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"seen_hashes": []}


def save_state(latest_hash):
    data = {
        "seen_hashes": [latest_hash],
    }
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        sys.exit("Error: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variable.")

    state = load_state()
    seen_hashes = state.get("seen_hashes", [])

    response = requests.get(BASE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    notifications = parse_notifications(response.text)

    if not notifications:
        print("No notifications found on page.")
        return

    latest_item = notifications[0]

    # First run: send latest notification and save its hash
    if not seen_hashes:
        print("Initializing state. Sending latest notification...")
        send_telegram_message(
            bot_token,
            chat_id,
            format_message(latest_item["text"], latest_item["links"]),
        )
        save_state(latest_item["id"])
        return

    last_seen_hash = seen_hashes[0]
    if latest_item["id"] == last_seen_hash:
        print("No new notifications.")
        return

    # Collect all new notifications that appeared above the last seen one
    new_notifications = []
    for item in notifications:
        if item["id"] == last_seen_hash:
            break
        new_notifications.append(item)

    for item in reversed(new_notifications):
        send_telegram_message(
            bot_token,
            chat_id,
            format_message(item["text"], item["links"]),
        )
        time.sleep(1.5)

    save_state(latest_item["id"])
    print(f"Sent {len(new_notifications)} new notification(s).")


if __name__ == "__main__":
    main()
