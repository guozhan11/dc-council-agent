import sys
import yaml
import requests

from db import connect, init_db, insert_item
from utils import make_content_hash, to_iso_datetime


YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_youtube_search(
    api_key: str,
    channel_id: str,
    event_type: str,
    max_results: int,
) -> list[dict]:
    """
    event_type: "live" | "upcoming"
    Returns a list of search items.
    """
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "eventType": event_type,
        "type": "video",
        "order": "date",
        "maxResults": max_results,
        "key": api_key,
    }

    resp = requests.get(
        YOUTUBE_SEARCH_URL,
        params=params,
        timeout=30,
        headers={"User-Agent": "dc-digest-bot/0.1"},
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def youtube_item_to_db_item(source: str, it: dict) -> dict:
    vid = it.get("id", {}).get("videoId", "")
    snippet = it.get("snippet", {}) or {}

    title = (snippet.get("title") or "").strip() or "(no title)"
    published_at_raw = snippet.get("publishedAt") or ""
    published_at = to_iso_datetime(published_at_raw) if published_at_raw else None

    # Video URL
    url = f"https://www.youtube.com/watch?v={vid}" if vid else ""
    if not url:
        raise ValueError("Missing videoId from YouTube search item.")

    summary = (snippet.get("description") or "").strip()
    source_item_id = vid

    content_hash = make_content_hash(title, url)

    return {
        "source": source,
        "source_item_id": source_item_id,
        "title": title,
        "url": url,
        "published_at": published_at,
        "summary": summary,
        "content_hash": content_hash,
    }


def main() -> int:
    import os

    config_path = "../config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    cfg = load_config(config_path)
    db_path = cfg["storage"]["db_path"]

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing YOUTUBE_API_KEY environment variable.")

    yt_cfg = cfg.get("youtube_api", {})
    channel_id = yt_cfg.get("channel_id", "").strip()
    if not channel_id:
        raise RuntimeError("Missing youtube_api.channel_id in config.yaml.")

    event_types = yt_cfg.get("event_types", ["live", "upcoming"])
    max_results = int(yt_cfg.get("max_results_per_event_type", 25))

    conn = connect(db_path)
    init_db(conn)

    total_new = 0

    for event_type in event_types:
        if event_type not in ["live", "upcoming"]:
            print(f"Skipping unsupported event_type: {event_type}")
            continue

        source = "youtube_live" if event_type == "live" else "youtube_upcoming"
        print(f"Fetching YouTube {event_type} streams for channel {channel_id}...")

        items = fetch_youtube_search(
            api_key=api_key,
            channel_id=channel_id,
            event_type=event_type,
            max_results=max_results,
        )

        for it in items:
            try:
                db_item = youtube_item_to_db_item(source, it)
            except Exception as e:
                print(f"Skipping item due to parse error: {e}")
                continue

            inserted = insert_item(conn, db_item)
            if inserted:
                total_new += 1

    print(f"Done. New YouTube live/upcoming items saved: {total_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
