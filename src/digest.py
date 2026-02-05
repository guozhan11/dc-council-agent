import sys
import yaml
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from jinja2 import Environment, FileSystemLoader

from db import connect, init_db, get_items_since, get_active_subscribers
from utils import score_item

import os
from emailer_gmail import send_email_gmail_smtp


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_plain_text(subject: str, highlights: list, sections: dict, unsubscribe_url: str) -> str:
    lines = [subject, ""]
    if highlights:
        lines.append("Top highlights")
        for it in highlights:
            lines.append(f"- {it['title']} ({it['source']}): {it['url']}")
        lines.append("")

    for section_name, items in sections.items():
        if not items:
            continue
        lines.append(section_name)
        for it in items:
            lines.append(f"- {it['title']} ({it['source']}): {it['url']}")
        lines.append("")

    lines.append(f"Unsubscribe: {unsubscribe_url}")
    return "\n".join(lines)


def main() -> int:
    config_path = "../config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    cfg = load_config(config_path)
    db_path = cfg["storage"]["db_path"]

    conn = connect(db_path)
    init_db(conn)

    now = datetime.now(timezone.utc)
    window_start_dt = now - timedelta(days=7)
    window_start = window_start_dt.isoformat()
    window_end = now.isoformat()

    items = get_items_since(conn, window_start)

    source_weight = cfg["ranking"]["source_weight"]
    keywords = cfg["highlights"]["keywords"]
    max_highlights = int(cfg["highlights"]["max_items"])

    # Score + sort
    for it in items:
        it["score"] = score_item(it, source_weight, keywords)

    items_sorted = sorted(items, key=lambda x: (x["score"], x.get("published_at") or x.get("created_at")), reverse=True)

    highlights = items_sorted[:max_highlights]

    # Sections
    sections = defaultdict(list)
    for it in items_sorted:
        src = it.get("source", "other")
        if src in ["granicus_rss", "council_rss"]:
            sections["Hearings & meetings (official)"].append(it)
        elif src == "youtube":
            sections["Videos & livestream replays"].append(it)
        else:
            sections["News mentions & other sources"].append(it)

    # Render HTML
    env = Environment(loader=FileSystemLoader("../template"))
    template = env.get_template("weekly_email.html")

    email_cfg = cfg["email"]
    subject = f"{email_cfg['subject_prefix']} ({window_start_dt.date()}–{now.date()})"

    subscribers = get_active_subscribers(conn)
    if not subscribers:
        print("No active subscribers. Exiting.")
        return 0

    provider = email_cfg.get("provider", "gmail_smtp")
    if provider != "gmail_smtp":
        raise ValueError('Set email.provider to "gmail_smtp" in config.yaml.')

    smtp_user = os.environ.get("GMAIL_SMTP_USERNAME", "")
    smtp_pass = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "")
    if not smtp_user or not smtp_pass:
        raise RuntimeError("Missing GMAIL_SMTP_USERNAME or GMAIL_SMTP_APP_PASSWORD environment variables.")

    base_unsub = email_cfg["base_url_for_unsubscribe"].rstrip("/")

    for sub in subscribers:
        to_email = sub["email"]
        token = sub["unsubscribe_token"]
        unsubscribe_url = f"{base_unsub}?email={to_email}&token={token}"

        html = template.render(
            subject=subject,
            window_start=window_start,
            window_end=window_end,
            highlights=highlights,
            sections=dict(sections),
            unsubscribe_url=unsubscribe_url,
        )
        text = build_plain_text(subject, highlights, dict(sections), unsubscribe_url)

        send_email_gmail_smtp(
            smtp_username=smtp_user,
            smtp_app_password=smtp_pass,
            from_email=email_cfg["from_email"],
            from_name=email_cfg["from_name"],
            to_email=to_email,
            subject=subject,
            html_content=html,
            text_content=text,
        )
        print(f"Sent to {to_email}")

    print("Weekly digest sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
