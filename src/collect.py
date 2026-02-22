import sys
import re
import os
import yaml
import feedparser
import requests

from db import connect, init_db, insert_item
from utils import make_content_hash, to_iso_datetime, strip_html


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_google_redirect(url: str) -> str:
    """
    Google Alerts often uses redirect links like:
      https://www.google.com/url?rct=j&sa=t&url=REAL_URL&...

    Best case: REAL_URL is in the query string.
    Fallback: follow redirects with a HEAD/GET.
    """
    if not url:
        return url

    # Fast path: extract the real "url=" parameter if present
    if url.startswith("https://www.google.com/url") and "url=" in url:
        try:
            from urllib.parse import urlparse, parse_qs

            qs = parse_qs(urlparse(url).query)
            if "url" in qs and len(qs["url"]) > 0:
                return qs["url"][0]
        except Exception:
            pass

    # Fallback: follow redirects (some sources use shorteners / tracking)
    try:
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=10,
            headers={"User-Agent": "dc-digest-bot/0.1"},
            stream=True,
        )
        return resp.url
    except Exception:
        return url


def extract_granicus_download_url(summary_html: str) -> str:
    """
    In Granicus RSS, the summary HTML usually contains a link like:
      https://dc.granicus.com/DownloadFile.php?view_id=2&clip_id=XXXXX

    We extract it so your DB stores the direct downloadable file URL.
    """
    if not summary_html:
        return ""

    m = re.search(r'href="(https://dc\.granicus\.com/DownloadFile\.php[^"]+)"', summary_html)
    if m:
        return m.group(1).replace("&amp;", "&")
    return ""


def parse_feed(feed_name: str, source: str, url: str):
    parsed = feedparser.parse(url)
    if parsed.bozo:
        # bozo means parsing had issues; still might have entries
        pass

    for entry in parsed.entries:
        title = entry.get("title", "").strip() or "(no title)"

        raw_link = entry.get("link", "").strip()
        if not raw_link:
            continue

        published_raw = entry.get("published") or entry.get("updated") or ""
        published_at = to_iso_datetime(published_raw) if published_raw else None

        # 1) Keep the raw HTML summary for Granicus parsing
        summary_raw = entry.get("summary", "") or entry.get("description", "")

        # 2) Clean summary into plain text for readability
        summary = strip_html(summary_raw)

        source_item_id = entry.get("id") or entry.get("guid")

        # 3) Decide which URL to store
        link = raw_link

        # Granicus: store the direct DownloadFile link if present
        if source == "granicus_rss":
            dl = extract_granicus_download_url(summary_raw)
            if dl:
                link = dl

        # Google Alerts: resolve to real destination instead of google.com/url tracking
        if source == "google_alerts":
            link = resolve_google_redirect(link)

        # Use final link in the hash so duplicates collapse correctly
        content_hash = make_content_hash(title, link)

        yield {
            "source": source,
            "source_item_id": source_item_id,
            "title": title,
            "url": link,
            "published_at": published_at,
            "summary": summary,
            "content_hash": content_hash,
        }


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

    total_new = 0
    for f in cfg.get("feeds", []):
        name = f["name"]
        source = f["source"]
        url = f["url"]
        print(f"Fetching: {name} ({source})")

        for item in parse_feed(name, source, url):
            inserted = insert_item(conn, item)
            if inserted:
                total_new += 1

    print(f"Done. New items saved: {total_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())