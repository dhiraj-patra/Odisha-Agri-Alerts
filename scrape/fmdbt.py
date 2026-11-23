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
    "Combine": 9,
}

REPORTS = {
    "state": {
        "title": "State-wise Target Report",
        "location_label": "State",
        "location_name": "ODISHA",
        "url": f"{BASE_URL}/report/checkDatainStateTargetReport?finyr={FINANCIAL_YEAR}&imp={{implement_id}}",
    },
    "district": {
        "title": "District-wise Target Report",
        "location_label": "District",
        "location_name": DISTRICT_NAME,
        "url": f"{BASE_URL}/report/loadpulltarget?finyr={FINANCIAL_YEAR}&district={DISTRICT_CODE}&imp={{implement_id}}",
    },
    "mela": {
        "title": "Mela Target Report",
        "location_label": "District",
        "location_name": DISTRICT_NAME,
        "url": f"{BASE_URL}/report/checkDataInMelaTarget?finyr={FINANCIAL_YEAR}&imp={{implement_id}}&dist={DISTRICT_CODE}",
    },
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
    url = f"https://api.telegram.org/bot{token}/sendRichMessage"
    payload = {
        "chat_id": chat_id,
        "rich_message": {
            "html": text,
        },
    }
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def load_state():
    if STATE_FILE.exists():
        state_data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if any(report_type in state_data for report_type in ("district", "state", "mela")):
            return state_data
        return {"district": state_data, "state": {}, "mela": {}}
    return {"district": {}, "state": {}, "mela": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch_target_report(url):
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


def generate_table(title, categories):
    header_cells = "".join(f'<th colspan="3">{html.escape(cat)}</th>' for cat in categories)
    sub_header_cells = "".join("<th>Target</th><th>Achieved</th><th>Available</th>" for _ in categories)
    data_cells = "".join(
        f"<td>{values['target']}</td><td>{values['achieved']}</td><td>{values['available']}</td>"
        for values in categories.values()
    )
    return (
        '<table border="1">'
        f"<caption><b>{html.escape(title)}</b></caption>"
        f"<tr>{header_cells}</tr>"
        f"<tr>{sub_header_cells}</tr>"
        f"<tr>{data_cells}</tr>"
        "</table>"
    )


def format_alert_message(district_name, financial_year, implement_name, reports_data):
    current_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%I:%M %p")
    lines = [
        f"<b>TIME:</b> {current_time}<br>",
        "<b>Target Report:</b><br><br>",
        f"<b>Financial Year:</b> {html.escape(financial_year)}<br>",
        f"<b>District:</b> {html.escape(district_name)}<br>",
        f"<b>Implement:</b> {html.escape(implement_name)}<br>",
    ]
    for report_title, categories in reports_data:
        lines.extend([
            "",
            generate_table(report_title, categories),
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

    for implement_name, implement_id in IMPLEMENTS.items():
        implement_changed = False
        reports_data = []
        fetched_state = {}

        for report_type, report_config in REPORTS.items():
            try:
                report_data = fetch_target_report(report_config["url"].format(implement_id=implement_id))
            except requests.RequestException as exc:
                print(f"Error fetching {report_config['title']} for {implement_name}: {exc}")
                continue

            categories = parse_categories(report_data)
            if not categories:
                print(f"No target data returned for {report_config['title']} ({implement_name}).")
                continue

            reports_data.append((report_config["title"], categories))
            fetched_state[report_type] = categories

            report_state = state.setdefault(report_type, {})
            if report_state.get(implement_name) != categories:
                implement_changed = True

        if not reports_data:
            continue

        if not implement_changed:
            print(f"No changes for {implement_name}.")
            continue

        print(f"Report change detected for {implement_name}! Sending alert...")

        message = format_alert_message(
            DISTRICT_NAME,
            FINANCIAL_YEAR,
            implement_name,
            reports_data,
        )
        send_telegram_message(bot_token, chat_id, message)
        alerts_sent += 1
        time.sleep(1)

        for report_type, categories in fetched_state.items():
            state.setdefault(report_type, {})[implement_name] = categories
        state_changed = True

    if state_changed:
        save_state(state)
        print("Updated fmdbt_state.json saved.")

    print(f"Finished FMDBT check. Alerts sent: {alerts_sent}.")


if __name__ == "__main__":
    main()
