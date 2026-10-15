#!/usr/bin/env python3
from datetime import datetime
import hashlib
import html
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

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
        "parse_mode": "HTML",
    }
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def format_message(li):
    parts = []
    if li.text:
        parts.append(html.escape(li.text))

    for el in li:
        if el.tag == "br":
            parts.append("\n")
        elif el.tag == "a":
            url = urljoin(BASE_URL, el.get("href", "").strip())
            text = html.escape(el.text_content().strip())
            parts.append(f' <a href="{url}">{text}</a>')
        elif el.tag != "img":
            parts.append(html.escape(el.text_content()))

        if el.tail:
            parts.append(html.escape(el.tail))

    lines = [" ".join(line.split()) for line in "".join(parts).splitlines()]
    return "\n".join(line for line in lines if line)


def parse_notifications(html_content):
    document = lxml.html.fromstring(html_content)
    notifications = []

    for li in document.xpath("//*[@id='WhatsNew']//li"):
        message = format_message(li)
        if message:
            item_id = hashlib.sha256(message.encode("utf-8")).hexdigest()
            notifications.append({"id": item_id, "message": message})

    return notifications


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen_hashes": []}


def save_state(latest_hash):
    data = {"seen_hashes": [latest_hash]}
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        sys.exit("Error: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variable.")

    state = load_state()
    seen_hashes = state.get("seen_hashes", [])

    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=30)
    except requests.RequestException:
        time.sleep(10)
        response = requests.get(BASE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    notifications = parse_notifications(response.text)

    if not notifications:
        print("No notifications found on page.")
        return

    if not seen_hashes:
        new_notifications = [notifications[0]]
    else:
        new_notifications = []
        for item in notifications:
            if item["id"] == seen_hashes[0]:
                break
            new_notifications.append(item)

    if not new_notifications:
        print("No new notifications.")
        return

    current_date = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y")
    for item in reversed(new_notifications):
        message_text = f"<b>Date:</b> {current_date}\n\n{item['message']}"
        send_telegram_message(bot_token, chat_id, message_text)
        time.sleep(1)

    save_state(notifications[0]["id"])
    print(f"Sent {len(new_notifications)} notification(s).")


if __name__ == "__main__":
    main()
