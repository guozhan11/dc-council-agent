import sys
import re
import os
import yaml
import feedparser
import requests
import time
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup

from db import connect, init_db, insert_item, get_existing_hashes
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


def fetch_feed(url: str, source: str):
    headers = {"User-Agent": "dc-digest-bot/0.1"}
    if source == "washington_times":
        headers["User-Agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )

    resp = requests.get(
        url,
        timeout=20,
        headers=headers,
    )

    if source == "washington_times" and resp.status_code == 403:
        # Retry once with a different UA to avoid basic blocks.
        headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
        resp = requests.get(
            url,
            timeout=20,
            headers=headers,
        )

    resp.raise_for_status()
    return feedparser.parse(resp.text)


def parse_feed(feed_name: str, source: str, url: str):
    try:
        parsed = fetch_feed(url, source)
    except Exception as e:
        print(f"Failed to fetch {feed_name} ({source}): {e}")
        return
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


def _find_table_with_headers(soup: BeautifulSoup, required_headers: list[str]):
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if all(h in headers for h in required_headers):
            return table, headers
    return None, []


def _extract_caption_link(row, page_url: str, headers: list[str]):
    for a in row.find_all("a"):
        if a.get_text(" ", strip=True).lower() == "captions" and a.get("href"):
            return urljoin(page_url, a.get("href").strip())

    try:
        index = {h: i for i, h in enumerate(headers)}
        if "captions" not in index:
            return None
        cells = row.find_all("td")
        if not cells or len(cells) <= index["captions"]:
            return None
        captions_cell = cells[index["captions"]]
        caption_link = captions_cell.find("a") if captions_cell else None
        if caption_link and caption_link.get("href"):
            return urljoin(page_url, caption_link.get("href").strip())
    except Exception:
        return None

    return None


def _looks_like_caption_text(text: str) -> bool:
    if not text:
        return False
    head = text[:400]
    if "WEBVTT" in head:
        return True
    if re.search(r"\d{2}:\d{2}:\d{2}[\.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}", head):
        return True
    return False


def _clean_caption_text(raw_text: str) -> str:
    lines = []
    for line in raw_text.splitlines():
        t = line.strip()
        if not t:
            continue
        if t.upper() == "WEBVTT":
            continue
        if re.match(r"^\d+$", t):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}[\.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}", t):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\s+-->\s+\d{2}:\d{2}:\d{2}", t):
            continue
        lines.append(t)
    return " ".join(lines)


def _fetch_caption_text(caption_url: str) -> str:
    try:
        resp = requests.get(caption_url, timeout=20, headers={"User-Agent": "dc-digest-bot/0.1"})
        resp.raise_for_status()
    except Exception:
        return ""

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "video" in content_type or "audio" in content_type:
        return ""

    raw_text = resp.text or ""

    if "text/html" in content_type or "<html" in raw_text.lower():
        soup = BeautifulSoup(raw_text, "html.parser")
        caption_divs = soup.find_all("div", class_="caption")
        if not caption_divs:
            return ""
        lines = [d.get_text(" ", strip=True) for d in caption_divs if d.get_text(strip=True)]
        return " ".join(lines)

    if not _looks_like_caption_text(raw_text):
        return ""

    return _clean_caption_text(raw_text)


def parse_granicus_captions(page_url: str, existing_hashes: set[str] | None = None):
    try:
        resp = requests.get(page_url, timeout=20, headers={"User-Agent": "dc-digest-bot/0.1"})
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch Granicus captions page: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    table, headers = _find_table_with_headers(soup, ["name", "date", "captions"])
    if not table:
        print("Failed to find Granicus captions table.")
        return

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells or len(cells) < len(headers):
            continue

        index = {h: i for i, h in enumerate(headers)}
        name = cells[index["name"]].get_text(" ", strip=True) if "name" in index else ""
        date_text = cells[index["date"]].get_text(" ", strip=True) if "date" in index else ""
        published_at = to_iso_datetime(date_text) if date_text else None

        caption_url = _extract_caption_link(row, page_url, headers)
        if not caption_url:
            continue

        clip_id = None
        try:
            qs = parse_qs(urlparse(caption_url).query)
            clip_id = (qs.get("clip_id") or [None])[0]
        except Exception:
            pass

        title = f"{name} (Captions)"
        if date_text:
            title = f"{title} - {date_text}"

        content_hash = make_content_hash(title, caption_url)
        if existing_hashes is not None and content_hash in existing_hashes:
            continue

        caption_text = _fetch_caption_text(caption_url)
        if not caption_text:
            continue

        summary = caption_text[:8000]

        yield {
            "source": "granicus_captions",
            "source_item_id": clip_id,
            "title": title,
            "url": caption_url,
            "published_at": published_at,
            "summary": summary,
            "content_hash": content_hash,
        }


def matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = text.lower()
    return any(k.lower() in haystack for k in keywords)


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

    keywords = cfg.get("filters", {}).get("dc_council_keywords", [])
    official_sources = {"granicus_rss", "granicus_captions", "council_rss", "youtube"}

    total_new = 0
    for f in cfg.get("feeds", []):
        name = f["name"]
        source = f["source"]
        url = f["url"]
        start = time.monotonic()
        print(f"Fetching: {name} ({source})")

        existing_hashes = None
        if source == "granicus_captions":
            existing_hashes = get_existing_hashes(conn, source)
            items_iter = parse_granicus_captions(url, existing_hashes)
        else:
            items_iter = parse_feed(name, source, url)

        for item in items_iter:
            if source not in official_sources and keywords:
                combined = f"{item.get('title', '')} {item.get('summary', '')}"
                if not matches_keywords(combined, keywords):
                    continue
            inserted = insert_item(conn, item)
            if inserted:
                if existing_hashes is not None:
                    existing_hashes.add(item.get("content_hash", ""))
                total_new += 1

        elapsed = time.monotonic() - start
        print(f"Done: {name} in {elapsed:.1f}s")

    print(f"Done. New items saved: {total_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())