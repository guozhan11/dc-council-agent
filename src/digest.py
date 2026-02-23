import sys
import os
import yaml
import requests
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv

from db import connect, init_db, get_items_since
from utils import score_item
from emailer_gmail import send_email_gmail_smtp
from summarizer_openai import summarize_updates

load_dotenv()


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_plain_text(subject: str, highlights: list, sections: dict, unsubscribe_url: str) -> str:
    lines = [subject, ""]
    if highlights:
        lines.append("Top highlights")
        for it in highlights:
            lines.append(f"- {it.get('title')} ({it.get('source')}): {it.get('url')}")
        lines.append("")

    for section_name, items in sections.items():
        if not items:
            continue
        lines.append(section_name)
        for it in items:
            lines.append(f"- {it.get('title')} ({it.get('source')}): {it.get('url')}")
        lines.append("")

    lines.append(f"Unsubscribe: {unsubscribe_url}")
    return "\n".join(lines)


def get_active_subscribers_from_apps_script() -> list[dict]:
    def _clean_secret(value: str) -> str:
        return str(value or "").strip().strip('"').strip("'")

    base = _clean_secret(os.environ.get("SUBSCRIBERS_API_URL", "")).rstrip("/")
    key = _clean_secret(os.environ.get("SUBSCRIBERS_API_KEY", ""))

    if not base or not key:
        raise RuntimeError("Missing SUBSCRIBERS_API_URL or SUBSCRIBERS_API_KEY environment variables.")

    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            "SUBSCRIBERS_API_URL must be a full http(s) URL (for example: "
            "https://script.google.com/macros/s/.../exec). Check the GitHub Secret value and remove wrapping quotes."
        )

    resp = requests.get(base, params={"path": "active_subscribers", "key": key}, timeout=20)
    resp.raise_for_status()

    # If Apps Script returns HTML (not JSON), this will fail:
    return resp.json().get("items", [])


def main() -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(repo_root, "config.yaml")
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    cfg = load_config(config_path)
    db_path = cfg["storage"]["db_path"]
    if not os.path.isabs(db_path):
        db_path = os.path.join(repo_root, db_path)

    conn = connect(db_path)
    init_db(conn)

    now = datetime.now(timezone.utc)
    window_start_dt = now - timedelta(days=7)
    window_start = window_start_dt.isoformat()
    window_end = now.isoformat()
    window_start_date = window_start_dt.date().isoformat()
    window_end_date = now.date().isoformat()

    items = get_items_since(conn, window_start)

    # ---- scoring/sorting FIRST (so AI sees the most important items) ----
    source_weight = cfg["ranking"]["source_weight"]
    keywords = cfg["highlights"]["keywords"]
    max_highlights = int(cfg["highlights"]["max_items"])

    for it in items:
        it["score"] = score_item(it, source_weight, keywords)

    items_sorted = sorted(
        items,
        key=lambda x: (x.get("score", 0), x.get("published_at") or x.get("created_at") or ""),
        reverse=True,
    )

    highlights = items_sorted[:max_highlights]

    sections = defaultdict(list)
    for it in items_sorted:
        src = it.get("source", "other")
        if src in ["granicus_rss", "council_rss"]:
            sections["Hearings & meetings (official)"].append(it)
        elif src == "youtube":
            sections["Videos & livestream replays"].append(it)
        else:
            sections["News mentions & other sources"].append(it)

    # ---- AI summary (use top K items only to control cost) ----
    top_for_ai = items_sorted[:40]  # adjust (20-60 is typical)

    # ---- Template setup (do this BEFORE render) ----
    # NOTE: set this to the actual folder name that contains weekly_email.html
    env = Environment(loader=FileSystemLoader(os.path.join(repo_root, "template")))
    template = env.get_template("weekly_email.html")

    email_cfg = cfg["email"]
    subscribers = get_active_subscribers_from_apps_script()

    test_to = os.environ.get("TEST_TO_EMAIL", "").strip()
    test_only = os.environ.get("TEST_ONLY_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    if test_to and test_only:
        print(f"TEST_ONLY_MODE enabled: sending only to {test_to}")
        subscribers = [{"email": test_to, "unsubscribe_token": "TESTTOKEN"}]
    elif test_to and not test_only:
        print("TEST_TO_EMAIL is set, but TEST_ONLY_MODE is not enabled; sending to all active subscribers.")
    if not subscribers:
        print("No active subscribers. Exiting.")
        return 0

    summaries_by_email = {}
    for sub in subscribers:
        interests_parts = []
        if sub.get("topics"):
            interests_parts.append(str(sub.get("topics")).strip())
        if sub.get("interests"):
            interests_parts.append(str(sub.get("interests")).strip())
        interests = "; ".join([p for p in interests_parts if p]) or None

        try:
            ai_summary = summarize_updates(
                top_for_ai,
                model="gpt-4.1-mini",
                max_bullets=3,
                interests=interests,
            )
        except Exception as e:
            print(f"AI summary error for {sub.get('email')}: {e}")
            print("Aborting send: AI summary failed to generate.")
            return 1

        if not interests:
            ai_summary["interest_notice"] = (
                "This email covers highlights across all areas. Update your preferences for customization anytime "
                "https://guozhan11.github.io/dc-council-agent/subscribe.html"
            )
            ai_summary["interest_notice_html"] = (
                "This email covers highlights across all areas. Update your preferences for customization anytime "
                "<a href=\"https://guozhan11.github.io/dc-council-agent/subscribe.html\">here</a>."
            )

        source_url_map = {
            s.get("n"): s.get("url")
            for s in ai_summary.get("sources", [])
            if s.get("n") and s.get("url")
        }

        fallback_subject = f"{email_cfg['subject_prefix']} ({window_start_dt.date()}–{now.date()})"
        subject = (ai_summary or {}).get("headline") or fallback_subject

        summaries_by_email[sub["email"]] = {
            "ai_summary": ai_summary,
            "source_url_map": source_url_map,
            "subject": subject,
        }

    provider = email_cfg.get("provider", "gmail_smtp")
    if provider != "gmail_smtp":
        raise ValueError('Set email.provider to "gmail_smtp" in config.yaml.')

    smtp_user = os.environ.get("GMAIL_SMTP_USERNAME", "")
    smtp_pass = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "")
    if not smtp_user or not smtp_pass:
        raise RuntimeError("Missing GMAIL_SMTP_USERNAME or GMAIL_SMTP_APP_PASSWORD environment variables.")

    # Unsubscribe base should be your Apps Script /exec URL
    # Example: https://script.google.com/macros/s/XXX/exec
    base_unsub = email_cfg["base_url_for_unsubscribe"].rstrip("/")

    for sub in subscribers:
        to_email = sub["email"]
        token = sub["unsubscribe_token"]

        # If your Apps Script expects /exec?path=unsubscribe&token=...
        unsubscribe_url = f"{base_unsub}?path=unsubscribe&token={token}"

        summary_bundle = summaries_by_email.get(to_email, {})
        html = template.render(
            subject=summary_bundle.get("subject"),
            window_start=window_start_date,
            window_end=window_end_date,
            highlights=highlights,
            sections=dict(sections),
            unsubscribe_url=unsubscribe_url,
            ai_summary=summary_bundle.get("ai_summary"),
            source_url_map=summary_bundle.get("source_url_map"),
        )
        text = build_plain_text(summary_bundle.get("subject"), highlights, dict(sections), unsubscribe_url)

        # If your send_email_gmail_smtp DOES accept from_name, keep it.
        # If not, remove from_name.
        send_email_gmail_smtp(
            smtp_username=smtp_user,
            smtp_app_password=smtp_pass,
            from_email=email_cfg["from_email"],
            to_email=to_email,
            subject=summary_bundle.get("subject"),
            html_content=html,
            text_content=text,
            from_name=email_cfg.get("from_name", ""),
        )

        print(f"Sent to {to_email}")

    print("Weekly digest sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())