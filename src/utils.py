import hashlib
import re
import html
from datetime import datetime, timezone
from dateutil import parser as dateparser
from typing import Any, Dict, List


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text


def make_content_hash(title: str, url: str) -> str:
    base = normalize_text(title) + "||" + normalize_text(url)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def to_iso_datetime(value: Any) -> str:
    """
    Accepts many date formats and returns ISO 8601 string in UTC.
    If parsing fails, returns current UTC time.
    """
    try:
        dt = dateparser.parse(str(value))
        if dt is None:
            raise ValueError("Could not parse date")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def contains_keywords(title: str, keywords: List[str]) -> bool:
    t = title.lower()
    return any(k.lower() in t for k in keywords)


def score_item(item: Dict[str, Any], source_weight: Dict[str, int], keywords: List[str]) -> int:
    s = source_weight.get(item.get("source", ""), 0)
    title = item.get("title", "")
    if contains_keywords(title, keywords):
        s += 2
    # Small bonus if it looks like an official hearing/committee item
    if "committee" in title.lower() or "hearing" in title.lower():
        s += 1
    return s

def strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    text = html.unescape(html_text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)  # remove tags
    text = re.sub(r"\n\s+\n", "\n\n", text)
    return text.strip()