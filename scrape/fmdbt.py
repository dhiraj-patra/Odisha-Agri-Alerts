from datetime import datetime
import html
import json
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://fmdbt.agriodisha.nic.in"
STATE_FILE = Path(__file__).resolve().parent.parent / "fmdbt_state.json"

FINANCIAL_YEAR = "2026-27"
DISTRICT_NAME = "SUNDERGARH"
DISTRICT_CODE = "30"

IMPLEMENTS = {
    "SPL-POW- WEEDER": 17,
    "Reaper": 3,
    "Power Tiller": 2,
    "Transplanter": 4,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://fmdbt.agriodisha.nic.in/public/chktarget",
}


def send_telegram_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch_target_report(fin_year, dist_code, imp_id):
    url = f"{BASE_URL}/report/loadpulltarget?finyr={fin_year}&district={dist_code}&imp={imp_id}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        time.sleep(5)
        response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_categories(report_data):
    if not report_data:
        return None
    row = report_data[0]

    return {
        "General": {
            "target": int(row.get("targetgen", 0)),
            "achieved": int(row.get("bookingnormal", 0)),
            "available": int(row.get("phybal", 0)),
        },
        "Scheduled Caste (SC)": {
            "target": int(row.get("targetsc", 0)),
            "achieved": int(row.get("bookingsc", 0)),
            "available": int(row.get("scbal", 0)),
        },
        "Scheduled Tribe (ST)": {
            "target": int(row.get("targetst", 0)),
            "achieved": int(row.get("bookingst", 0)),
            "available": int(row.get("stbal", 0)),
        },
    }


def format_alert_message(district_name, fin_year, implement_name, categories):
    current_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y %I:%M %p")
    escaped_imp = html.escape(implement_name)
    lines = [
        f"🎯 <b>Target Alert for {escaped_imp}</b>",
        "",
        f"<b>Date:</b> {current_time}",
        f"<b>Financial Year:</b> {html.escape(fin_year)}",
        f"<b>District:</b> {html.escape(district_name)}",
        f"<b>Implement:</b> {escaped_imp}",
        "",
        "<b>Target Status:</b>",
    ]
    for cat_name, vals in categories.items():
        lines.extend([
            "",
            f"• <b>{html.escape(cat_name)}:</b>",
            f"    - Target: {vals['target']}",
            f"    - Achieved: {vals['achieved']}",
            f"    - Available: <b>{vals['available']}</b>",
        ])
    lines.extend([
        "",
        "<b>Book here:</b> https://fmdbt.agriodisha.nic.in",
    ])
    return "\n".join(lines)


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        sys.exit("Error: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variable.")

    state = load_state()
    state_changed = False
    alerts_sent = 0

    for imp_name, imp_id in IMPLEMENTS.items():
        try:
            report_data = fetch_target_report(FINANCIAL_YEAR, DISTRICT_CODE, imp_id)
        except requests.RequestException as exc:
            print(f"Error fetching report for {imp_name}: {exc}")
            continue

        categories = parse_categories(report_data)
        if not categories:
            print(f"No target data returned for {imp_name}.")
            continue

        prev_report = state.get(imp_name)
        if prev_report == categories:
            print(f"No changes for {imp_name}.")
            continue

        print(f"Report change detected for {imp_name}! Sending alert...")

        message = format_alert_message(
            DISTRICT_NAME, FINANCIAL_YEAR, imp_name, categories
        )
        send_telegram_message(bot_token, chat_id, message)
        alerts_sent += 1
        time.sleep(1)

        state[imp_name] = categories
        state_changed = True

    if state_changed:
        save_state(state)
        print("Updated fmdbt_state.json saved.")

    print(f"Finished FMDBT check. Alerts sent: {alerts_sent}.")


if __name__ == "__main__":
    main()
