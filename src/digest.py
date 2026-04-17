import sys
import os
import json
import html
import re
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
from summarizer_openai import summarize_interest_phrase, summarize_updates, review_summary_quality

load_dotenv()


def build_test_subscriber(subscribers: list[dict], test_to: str) -> dict:
    normalized_test_to = test_to.strip().lower()
    for subscriber in subscribers:
        email = str(subscriber.get("email") or "").strip().lower()
        if email == normalized_test_to:
            matched = dict(subscriber)
            matched.setdefault("unsubscribe_token", "TESTTOKEN")
            return matched

    test_subscriber = {
        "email": test_to,
        "unsubscribe_token": "TESTTOKEN",
    }

    test_topics = str(os.environ.get("TEST_SUBSCRIBER_TOPICS", "") or "").strip()
    test_interests = str(os.environ.get("TEST_SUBSCRIBER_INTERESTS", "") or "").strip()
    if test_topics:
        test_subscriber["topics"] = test_topics
    if test_interests:
        test_subscriber["interests"] = test_interests
    return test_subscriber


def parse_email_list(value: str) -> set[str]:
    emails = set()
    for token in re.split(r"[;,\s]+", str(value or "").strip()):
        normalized = token.strip().lower()
        if normalized and "@" in normalized:
            emails.add(normalized)
    return emails


INTEREST_STOPWORDS = {
    "about",
    "against",
    "around",
    "because",
    "between",
    "council",
    "district",
    "focus",
    "general",
    "interest",
    "interests",
    "issues",
    "policy",
    "program",
    "programs",
    "public",
    "their",
    "these",
    "those",
    "topic",
    "topics",
    "update",
    "updates",
    "washington",
    "week",
    "with",
}


def extract_interest_terms(interests: str) -> set[str]:
    terms = set()
    for token in re.findall(r"[a-z0-9]+", str(interests or "").lower()):
        if len(token) < 4:
            continue
        if token in INTEREST_STOPWORDS:
            continue
        terms.add(token)
    return terms


def filter_items_for_interests(items: list[dict], interests: str) -> list[dict]:
    terms = extract_interest_terms(interests)
    if not terms:
        return []

    matched = []
    for it in items:
        haystack = " ".join(
            [
                str(it.get("title") or ""),
                str(it.get("summary") or ""),
                str(it.get("text") or ""),
                str(it.get("source") or ""),
            ]
        ).lower()
        if any(term in haystack for term in terms):
            matched.append(it)
    return matched


def send_delivery_alert(
    *,
    smtp_user: str,
    smtp_pass: str,
    from_email: str,
    from_name: str,
    alert_to_email: str,
    subject_prefix: str,
    failed_recipients: list[dict],
    sent_count: int,
) -> None:
    if not alert_to_email:
        return
    if not failed_recipients:
        return

    utc_now = datetime.now(timezone.utc).isoformat()
    subject = f"[Alert] {subject_prefix} delivery failures ({len(failed_recipients)})"
    lines = [
        "Digest delivery alert",
        f"Timestamp (UTC): {utc_now}",
        f"Sent successfully: {sent_count}",
        f"Failed deliveries: {len(failed_recipients)}",
        "",
        "Failed recipients:",
    ]
    for item in failed_recipients:
        lines.append(f"- {item.get('email')}: {item.get('error')}")

    text_content = "\n".join(lines)
    html_lines = ["<p><strong>Digest delivery alert</strong></p>"]
    html_lines.append(f"<p>Timestamp (UTC): {html.escape(utc_now)}</p>")
    html_lines.append(f"<p>Sent successfully: {sent_count}<br/>Failed deliveries: {len(failed_recipients)}</p>")
    html_lines.append("<p><strong>Failed recipients:</strong></p><ul>")
    for item in failed_recipients:
        html_lines.append(
            f"<li>{html.escape(str(item.get('email') or ''))}: {html.escape(str(item.get('error') or 'unknown error'))}</li>"
        )
    html_lines.append("</ul>")
    html_content = "".join(html_lines)

    send_email_gmail_smtp(
        smtp_username=smtp_user,
        smtp_app_password=smtp_pass,
        from_email=from_email,
        to_email=alert_to_email,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        from_name=from_name,
    )


def summarize_interest_text(interests: str) -> str:
    text = re.sub(r"\s+", " ", str(interests or "").strip())
    if not text:
        return ""

    text = text.strip(" .;,:!?")
    lead_in_patterns = [
        r"^(?:i|we)\s+(?:care about|am interested in|are interested in|want to follow|follow|want updates about|want updates on|would like updates about|would like updates on)\s+",
        r"^(?:interested in|updates about|updates on|about)\s+",
    ]
    for pattern in lead_in_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    text = re.sub(r"\b(?:also|too)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .;,:!?")
    return text or str(interests or "").strip()


def build_preferences_notice(
    subscriber: dict,
    summarized_interests: str | None = None,
) -> tuple[str, str] | tuple[None, None]:
    topics = str(subscriber.get("topics") or "").strip()
    interests = str(subscriber.get("interests") or "").strip()
    summarized_interests = (summarized_interests or "").strip() or summarize_interest_text(interests)

    if not topics and not interests:
        return None, None

    update_url = "https://guozhan11.github.io/dc-council-agent/subscribe.html"

    if summarized_interests and topics:
        plain = (
            f"This AI-curated digest is personalized to your interests in {summarized_interests} and your selected topic(s): {topics} — every story is sourced and verifiable. "
            f"Update your preferences anytime: {update_url}"
        )
        rich = (
            f"This AI-curated digest is personalized to your interests in {html.escape(summarized_interests)} and your selected topic(s): {html.escape(topics)} — every story is sourced and verifiable. "
            f"<a href=\"{update_url}\">Update your preferences</a> anytime!"
        )
    elif summarized_interests:
        plain = (
            f"This AI-curated digest is personalized to your interests in {summarized_interests} — every story is sourced and verifiable. "
            f"Update your preferences anytime: {update_url}"
        )
        rich = (
            f"This AI-curated digest is personalized to your interests in {html.escape(summarized_interests)} — every story is sourced and verifiable. "
            f"<a href=\"{update_url}\">Update your preferences</a> anytime!"
        )
    else:
        plain = (
            f"This AI-curated digest is personalized to your selected topic(s): {topics} — every story is sourced and verifiable. "
            f"Update your preferences anytime: {update_url}"
        )
        rich = (
            f"This AI-curated digest is personalized to your selected topic(s): {html.escape(topics)} — every story is sourced and verifiable. "
            f"<a href=\"{update_url}\">Update your preferences</a> anytime!"
        )

    return plain, rich


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


def build_fallback_ai_summary(items_sorted: list[dict], max_bullets: int = 3) -> dict:
    fallback_items = items_sorted[:max_bullets]
    bullets = []
    sources = []
    for idx, it in enumerate(fallback_items, start=1):
        title = html.unescape((it.get("title") or "Update").strip())
        title = re.sub(r"<[^>]+>", "", title)
        title = re.sub(r"\*\*(.*?)\*\*", r"\1", title)
        title = re.sub(r"__(.*?)__", r"\1", title)
        title = re.sub(r"\s+", " ", title).strip()
        source_name = (it.get("source") or "source").strip()
        bullets.append(
            {
                "text": f"{title} — Key update from {source_name} this week.",
                "lead": title,
                "detail": f"Key update from {source_name} this week.",
                "sources": [idx],
            }
        )
        sources.append(
            {
                "n": idx,
                "title": title,
                "url": it.get("url") or "",
                "source": source_name,
            }
        )

    return {
        "headline": "DC Council Weekly Updates",
        "interest_notice": "AI summary fallback was used due to a temporary processing issue.",
        "bullets": bullets,
        "sources": sources,
    }


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
    def _is_true(value: str) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _summary_format_ok(summary: dict) -> tuple[bool, str]:
        if not isinstance(summary, dict):
            return False, "summary is not an object"
        headline = str(summary.get("headline") or "").strip()
        bullets = summary.get("bullets") or []
        sources = summary.get("sources") or []
        if not headline:
            return False, "missing headline"
        if not bullets:
            return False, "no bullets"
        if not sources:
            return False, "no sources"
        for idx, bullet in enumerate(bullets, start=1):
            text = str((bullet or {}).get("text") or "").strip()
            srcs = (bullet or {}).get("sources") or []
            if not text:
                return False, f"bullet {idx} is empty"
            if not srcs:
                return False, f"bullet {idx} has no citations"
        return True, "ok"

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
        if src in ["granicus_rss", "granicus_captions", "council_rss"]:
            sections["Hearings & meetings (official)"].append(it)
        elif src == "youtube":
            sections["Videos & livestream replays"].append(it)
        else:
            sections["News mentions & other sources"].append(it)

    # ---- AI summary (use top K items only to control cost) ----
    top_for_ai = items_sorted

    # ---- Template setup (do this BEFORE render) ----
    # NOTE: set this to the actual folder name that contains weekly_email.html
    env = Environment(loader=FileSystemLoader(os.path.join(repo_root, "template")))
    template = env.get_template("weekly_email.html")

    email_cfg = cfg["email"]
    subscribers = get_active_subscribers_from_apps_script()

    test_to = os.environ.get("TEST_TO_EMAIL", "").strip()
    test_only = _is_true(os.environ.get("TEST_ONLY_MODE", ""))
    preview_only = _is_true(os.environ.get("PREVIEW_ONLY_MODE", ""))
    makeup_targets = parse_email_list(os.environ.get("MAKEUP_TARGET_EMAILS", ""))
    alert_to_email = str(os.environ.get("DIGEST_ALERT_TO_EMAIL", "") or "").strip()
    auto_quality_check = _is_true(os.environ.get("AUTO_QUALITY_CHECK", "true"))
    quality_model = (os.environ.get("QUALITY_CHECK_MODEL", "") or "gpt-4.1-mini").strip()
    try:
        quality_min_score = int((os.environ.get("QUALITY_CHECK_MIN_SCORE", "70") or "70").strip())
    except ValueError:
        quality_min_score = 70
    preview_dir = os.environ.get("PREVIEW_OUTPUT_DIR", "").strip() or os.path.join(repo_root, "tmp", "email_previews")
    try:
        preview_limit = max(1, int(os.environ.get("PREVIEW_RECIPIENT_LIMIT", "3").strip() or "3"))
    except ValueError:
        preview_limit = 3
    if test_to and test_only:
        print(f"TEST_ONLY_MODE enabled: sending only to {test_to}")
        test_subscriber = build_test_subscriber(subscribers, test_to)
        if test_subscriber.get("topics") or test_subscriber.get("interests"):
            print("Using subscriber preferences for test send.")
        else:
            print("No saved preferences found for test email; using TEST_SUBSCRIBER_TOPICS/TEST_SUBSCRIBER_INTERESTS if set.")
        subscribers = [test_subscriber]
    elif test_to and not test_only:
        print("TEST_TO_EMAIL is set, but TEST_ONLY_MODE is not enabled; sending to all active subscribers.")

    if makeup_targets:
        active_map = {str(s.get("email") or "").strip().lower(): s for s in subscribers}
        missing = sorted([email for email in makeup_targets if email not in active_map])
        subscribers = [active_map[email] for email in sorted(makeup_targets) if email in active_map]
        print(f"MAKEUP_TARGET_EMAILS enabled: attempting delivery only to {len(subscribers)} subscriber(s).")
        if missing:
            print("The following make-up targets are not active subscribers and will be skipped: " + ", ".join(missing))

    if not subscribers:
        print("No active subscribers. Exiting.")
        return 0

    summaries_by_email = {}
    interest_phrase_cache: dict[str, str] = {}
    ai_failures = 0
    ai_fallbacks = 0
    ai_fallback_emails: list[str] = []
    format_blocks: list[tuple[str, str]] = []
    quality_warnings: list[tuple[str, str]] = []
    for sub in subscribers:
        interests_parts = []
        if sub.get("topics"):
            interests_parts.append(str(sub.get("topics")).strip())
        if sub.get("interests"):
            interests_parts.append(str(sub.get("interests")).strip())
        interests = "; ".join([p for p in interests_parts if p]) or None
        items_for_ai = top_for_ai
        no_interest_match = False
        if interests:
            interest_matched_items = filter_items_for_interests(top_for_ai, interests)
            if interest_matched_items:
                items_for_ai = interest_matched_items
            else:
                no_interest_match = True

        try:
            ai_summary = summarize_updates(
                items_for_ai,
                model="gpt-4.1-mini",
                max_bullets=3,
                interests=interests,
            )
            # Guard: AI succeeded but returned unusable references.
            # Patch in rule-based bullets/sources so emails always carry
            # valid citations.
            has_bullets = bool(ai_summary.get("bullets"))
            has_sources = bool(ai_summary.get("sources"))
            all_bullets_cited = all(bool(b.get("sources")) for b in ai_summary.get("bullets", []))
            if not has_bullets or not has_sources or not all_bullets_cited:
                print(
                    f"Warning: AI returned missing citations for {sub.get('email')}; "
                    "patching with fallback bullets/sources."
                )
                fallback = build_fallback_ai_summary(items_for_ai or top_for_ai, max_bullets=3)
                ai_summary["bullets"] = fallback["bullets"]
                ai_summary["sources"] = fallback["sources"]
                ai_fallbacks += 1
                ai_fallback_emails.append(sub.get("email") or "(unknown)")
        except Exception as e:
            ai_failures += 1
            ai_fallbacks += 1
            ai_fallback_emails.append(sub.get("email") or "(unknown)")
            print(f"AI summary error for {sub.get('email')}: {e}")
            print("Using fallback summary for this subscriber.")
            ai_summary = build_fallback_ai_summary(items_for_ai or top_for_ai, max_bullets=3)

        summarized_interest = ""
        raw_interests = str(sub.get("interests") or "").strip()
        if raw_interests:
            cached_interest_phrase = interest_phrase_cache.get(raw_interests)
            if cached_interest_phrase is None:
                try:
                    cached_interest_phrase = summarize_interest_phrase(raw_interests)
                except Exception as e:
                    print(f"Interest summary fallback for {sub.get('email')}: {e}")
                    cached_interest_phrase = summarize_interest_text(raw_interests)
                interest_phrase_cache[raw_interests] = cached_interest_phrase
            summarized_interest = cached_interest_phrase

        preferences_notice, preferences_notice_html = build_preferences_notice(
            sub,
            summarized_interests=summarized_interest,
        )
        if preferences_notice:
            ai_summary["preferences_notice"] = preferences_notice
            ai_summary["preferences_notice_html"] = preferences_notice_html

        if not interests:
            ai_summary["interest_notice"] = (
                "This email covers highlights across all areas. Update your preferences for customization anytime "
                "https://guozhan11.github.io/dc-council-agent/subscribe.html"
            )
            ai_summary["interest_notice_html"] = (
                "This email covers highlights across all areas. Update your preferences for customization anytime "
                "<a href=\"https://guozhan11.github.io/dc-council-agent/subscribe.html\">here</a>."
            )
        elif no_interest_match:
            # Deterministic message when no weekly items match subscriber interests.
            ai_summary["interest_notice"] = (
                f"No updates this week for your interests: {interests}. Showing general updates instead."
            )
            ai_summary["interest_notice_html"] = html.escape(ai_summary["interest_notice"])

        quality_review = {
            "approved": True,
            "score": 100,
            "issues": [],
            "reason": "quality check disabled",
        }
        if auto_quality_check:
            try:
                quality_review = review_summary_quality(
                    ai_summary,
                    interests=interests,
                    model=quality_model,
                )
            except Exception as e:
                quality_review = {
                    "approved": False,
                    "score": 0,
                    "issues": ["quality-check-error"],
                    "reason": str(e),
                }

            review_approved = bool(quality_review.get("approved"))
            review_score = int(quality_review.get("score", 0))
            if not review_approved or review_score < quality_min_score:
                reason = str(quality_review.get("reason") or "failed quality gate")
                print(
                    f"Quality warning for {sub.get('email')}: "
                    f"approved={review_approved}, score={review_score}, reason={reason}"
                )
                quality_warnings.append((sub.get("email") or "(unknown)", reason))

        format_ok, format_reason = _summary_format_ok(ai_summary)
        if not format_ok:
            format_blocks.append((sub.get("email") or "(unknown)", format_reason))

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
            "quality_review": quality_review,
        }

    provider = email_cfg.get("provider", "gmail_smtp")
    if provider != "gmail_smtp":
        raise ValueError('Set email.provider to "gmail_smtp" in config.yaml.')

    # Safety rule: if any subscriber required AI fallback, abort the run and
    # do not send any emails.
    if ai_fallbacks > 0:
        print(
            "Aborting send: AI fallback was triggered for "
            f"{ai_fallbacks} subscriber(s): {', '.join(ai_fallback_emails)}"
        )
        return 1

    if format_blocks:
        blocked_emails = ", ".join(email for email, _ in format_blocks)
        print(
            "Aborting send: summary format checks failed for "
            f"{len(format_blocks)} subscriber(s): {blocked_emails}"
        )
        for email, reason in format_blocks:
            print(f"- Format block {email}: {reason}")
        return 1

    if quality_warnings:
        print(f"OpenAI quality warnings: {len(quality_warnings)} subscriber(s). Continuing send because format is valid.")
        for email, reason in quality_warnings:
            print(f"- Quality warning {email}: {reason}")

    # Preflight quality report before any send attempt.
    preflight_rows = []
    for sub in subscribers:
        email = sub.get("email")
        summary_bundle = summaries_by_email.get(email, {})
        ai_summary = summary_bundle.get("ai_summary") or {}
        bullets = ai_summary.get("bullets") or []
        sources = ai_summary.get("sources") or []
        cited_bullets = sum(1 for b in bullets if b.get("sources"))
        preflight_rows.append(
            {
                "email": email,
                "subject": summary_bundle.get("subject") or "",
                "bullet_count": len(bullets),
                "cited_bullet_count": cited_bullets,
                "source_count": len(sources),
                "quality_score": int((summary_bundle.get("quality_review") or {}).get("score", 0)),
            }
        )

    print(f"Preflight summary: subscribers={len(preflight_rows)}")
    for row in preflight_rows:
        print(
            f"- {row['email']}: bullets={row['bullet_count']}, "
            f"cited_bullets={row['cited_bullet_count']}, sources={row['source_count']}, "
            f"quality_score={row['quality_score']}"
        )

    empty_or_thin = [
        row["email"]
        for row in preflight_rows
        if row["bullet_count"] < 1 or row["source_count"] < 1 or row["cited_bullet_count"] < 1
    ]
    if empty_or_thin:
        print(
            "Aborting send: preflight found empty/unusable summaries for "
            f"{len(empty_or_thin)} subscriber(s): {', '.join(empty_or_thin)}"
        )
        return 1

    # Unsubscribe base should be your Apps Script /exec URL
    # Example: https://script.google.com/macros/s/XXX/exec
    base_unsub = email_cfg["base_url_for_unsubscribe"].rstrip("/")

    if preview_only:
        print("PREVIEW_ONLY_MODE enabled: generating previews and skipping all sends.")
        os.makedirs(preview_dir, exist_ok=True)

        for idx, sub in enumerate(subscribers[:preview_limit], start=1):
            to_email = sub["email"]
            token = sub["unsubscribe_token"]
            unsubscribe_url = f"{base_unsub}?path=unsubscribe&token={token}"
            summary_bundle = summaries_by_email.get(to_email, {})

            rendered_html = template.render(
                subject=summary_bundle.get("subject"),
                window_start=window_start_date,
                window_end=window_end_date,
                highlights=highlights,
                sections=dict(sections),
                unsubscribe_url=unsubscribe_url,
                ai_summary=summary_bundle.get("ai_summary"),
                source_url_map=summary_bundle.get("source_url_map"),
            )
            rendered_text = build_plain_text(summary_bundle.get("subject"), highlights, dict(sections), unsubscribe_url)

            safe_email = re.sub(r"[^A-Za-z0-9_.-]+", "_", to_email)
            html_path = os.path.join(preview_dir, f"{idx:02d}_{safe_email}.html")
            txt_path = os.path.join(preview_dir, f"{idx:02d}_{safe_email}.txt")

            with open(html_path, "w", encoding="utf-8") as f:
                f.write(rendered_html)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(rendered_text)

            print(f"Preview written: {html_path}")
            print(f"Preview written: {txt_path}")

        print(
            "Preview run finished. "
            f"Generated {min(len(subscribers), preview_limit)} preview(s) in {preview_dir}."
        )
        return 0

    smtp_user = os.environ.get("GMAIL_SMTP_USERNAME", "")
    smtp_pass = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "")
    if not smtp_user or not smtp_pass:
        raise RuntimeError("Missing GMAIL_SMTP_USERNAME or GMAIL_SMTP_APP_PASSWORD environment variables.")

    sent_count = 0
    send_failures = 0
    failed_recipients: list[dict] = []
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
        try:
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
            sent_count += 1
            print(f"Sent to {to_email}")
        except Exception as e:
            send_failures += 1
            failed_recipients.append({"email": to_email, "error": str(e)})
            print(f"Send failed for {to_email}: {e}")

    report_dir = os.path.join(repo_root, "tmp")
    os.makedirs(report_dir, exist_ok=True)
    failed_report_path = os.path.join(report_dir, "failed_recipients.json")
    with open(failed_report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "sent_count": sent_count,
                "send_failures": send_failures,
                "failed_recipients": failed_recipients,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Failure report written: {failed_report_path}")

    if send_failures > 0 and alert_to_email:
        try:
            send_delivery_alert(
                smtp_user=smtp_user,
                smtp_pass=smtp_pass,
                from_email=email_cfg["from_email"],
                from_name=email_cfg.get("from_name", ""),
                alert_to_email=alert_to_email,
                subject_prefix=email_cfg.get("subject_prefix", "DC Council Digest"),
                failed_recipients=failed_recipients,
                sent_count=sent_count,
            )
            print(f"Delivery alert sent to {alert_to_email}")
        except Exception as e:
            print(f"Delivery alert failed: {e}")

    print(
        f"Weekly digest finished. sent={sent_count}, send_failures={send_failures}, ai_fallbacks={ai_fallbacks}"
    )
    if sent_count == 0:
        print("No messages were sent successfully.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())