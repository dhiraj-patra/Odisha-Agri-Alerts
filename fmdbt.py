#!/usr/bin/env python3
"""FMDBT Target Monitoring Bot for Agriculture-Bot.

Monitors Odisha FMDBT public target portal for district target allocation,
achieved bookings, and positive availability for configured implements.
"""

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
STATE_FILE = Path(__file__).resolve().parent / "fmdbt_state.json"

FINANCIAL_YEAR = "2026-27"
TARGET_DISTRICT = "SUNDERGARH"
TARGET_IMPLEMENTS = ["SPL-POW- WEEDER", "Reaper", "Power Tiller"]

FALLBACK_DISTRICT_CODE = "30"
FALLBACK_IMPLEMENT_IDS = {
    "SPL-POW- WEEDER": 17,
    "Reaper": 3,
    "Power Tiller": 2,
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
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def resolve_district_code(session, district_name):
    url = f"{BASE_URL}/report/getAllDistrictsfromPG"
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        for item in response.json():
            if item.get("dist_nm", "").strip().upper() == district_name.strip().upper():
                return str(item.get("dist_code"))
    except Exception as exc:
        print(f"Warning: Failed to fetch district list dynamically ({exc}). Using fallback.")
    return FALLBACK_DISTRICT_CODE


def resolve_implement_ids(session, target_implements):
    mapping = {}
    url = f"{BASE_URL}/report/getImplementsfromPG"
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        all_implements = response.json()
        for target in target_implements:
            target_clean = target.strip().lower()
            for item in all_implements:
                imp_name = item.get("Implement", "").strip().lower()
                if imp_name == target_clean:
                    mapping[target] = item.get("Impl_id")
                    break
    except Exception as exc:
        print(f"Warning: Failed to fetch implements dynamically ({exc}). Using fallbacks.")

    for target in target_implements:
        if target not in mapping and target in FALLBACK_IMPLEMENT_IDS:
            mapping[target] = FALLBACK_IMPLEMENT_IDS[target]
    return mapping


def fetch_target_report(session, fin_year, dist_code, imp_id):
    url = f"{BASE_URL}/report/loadpulltarget?finyr={fin_year}&district={dist_code}&imp={imp_id}"
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException:
        time.sleep(5)
        response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def to_int(val, fallback=0):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return fallback


def parse_categories(report_data):
    if not report_data or not isinstance(report_data, list):
        return None
    row = report_data[0]

    gen_target = to_int(row.get("targetgen"))
    gen_achieved = to_int(row.get("bookingnormal"))
    gen_avail = to_int(row.get("phybal"), max(0, gen_target - gen_achieved))

    sc_target = to_int(row.get("targetsc"))
    sc_achieved = to_int(row.get("bookingsc"))
    sc_avail = to_int(row.get("scbal"), max(0, sc_target - sc_achieved))

    st_target = to_int(row.get("targetst"))
    st_achieved = to_int(row.get("bookingst"))
    st_avail = to_int(row.get("stbal"), max(0, st_target - st_achieved))

    return {
        "General": {
            "target": gen_target,
            "achieved": gen_achieved,
            "available": gen_avail,
        },
        "Scheduled Caste (SC)": {
            "target": sc_target,
            "achieved": sc_achieved,
            "available": sc_avail,
        },
        "Scheduled Tribe (ST)": {
            "target": st_target,
            "achieved": st_achieved,
            "available": st_avail,
        },
    }


def format_alert_message(district_name, fin_year, implement_name, positive_categories):
    current_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y %I:%M %p")
    lines = [
        "🚜 <b>FMDBT Target Alert</b>",
        f"<b>Date:</b> {current_time}",
        f"<b>Financial Year:</b> {html.escape(fin_year)}",
        f"<b>District:</b> {html.escape(district_name)}",
        f"<b>Implement:</b> {html.escape(implement_name)}",
        "",
        "<b>Available Targets:</b>",
    ]
    for cat_name, vals in positive_categories.items():
        lines.append(
            f"• <b>{html.escape(cat_name)}:</b> "
            f"Available: <b>{vals['available']}</b> "
            f"(Target: {vals['target']}, Achieved: {vals['achieved']})"
        )
    lines.extend([
        "",
        '🔗 <a href="https://fmdbt.agriodisha.nic.in/public/chktarget">FMDBT Target Portal</a>',
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

    session = requests.Session()
    dist_code = resolve_district_code(session, TARGET_DISTRICT)
    implement_mapping = resolve_implement_ids(session, TARGET_IMPLEMENTS)

    for imp_name in TARGET_IMPLEMENTS:
        imp_id = implement_mapping.get(imp_name)
        if imp_id is None:
            print(f"Skipping {imp_name}: implement ID could not be resolved.")
            continue

        try:
            report_data = fetch_target_report(session, FINANCIAL_YEAR, dist_code, imp_id)
        except requests.RequestException as exc:
            print(f"Error fetching report for {imp_name}: {exc}")
            continue

        categories = parse_categories(report_data)
        if not categories:
            print(f"No target data returned for {imp_name}.")
            continue

        positive_cats = {
            cat: vals for cat, vals in categories.items() if vals["available"] > 0
        }

        imp_state = state.setdefault(imp_name, {})
        prev_alerted = imp_state.get("last_alerted")

        if not positive_cats:
            print(f"No positive availability for {imp_name}.")
            if prev_alerted is not None:
                imp_state["last_alerted"] = None
                state_changed = True
            if imp_state.get("last_report") != categories:
                imp_state["last_report"] = categories
                state_changed = True
            continue

        # Positive availability detected
        if prev_alerted == positive_cats:
            print(
                f"No change in positive availability for {imp_name} "
                f"({len(positive_cats)} category/categories already alerted)."
            )
            if imp_state.get("last_report") != categories:
                imp_state["last_report"] = categories
                state_changed = True
            continue

        # New availability or values changed
        print(f"Positive target availability detected for {imp_name}! Sending alert...")
        message = format_alert_message(
            TARGET_DISTRICT, FINANCIAL_YEAR, imp_name, positive_cats
        )
        send_telegram_message(bot_token, chat_id, message)
        alerts_sent += 1
        time.sleep(1)

        imp_state["last_alerted"] = positive_cats
        imp_state["last_report"] = categories
        state_changed = True

    if state_changed:
        save_state(state)
        print("Updated fmdbt_state.json saved.")

    print(f"Finished FMDBT check. Alerts sent: {alerts_sent}.")


if __name__ == "__main__":
    main()
