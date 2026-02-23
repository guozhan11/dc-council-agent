import sqlite3
from typing import Any, Dict, List, Optional


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_item_id TEXT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            published_at TEXT,
            summary TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_items_url
        ON items(url)
        """
    )

    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_items_hash
        ON items(content_hash)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscribers (
            email TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            unsubscribe_token TEXT NOT NULL
        )
        """
    )

    conn.commit()


def insert_item(conn: sqlite3.Connection, item: Dict[str, Any]) -> bool:
    """
    Returns True if inserted, False if it already existed (deduped).
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO items (source, source_item_id, title, url, published_at, summary, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("source"),
                item.get("source_item_id"),
                item.get("title"),
                item.get("url"),
                item.get("published_at"),
                item.get("summary"),
                item.get("content_hash"),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_items_since(conn: sqlite3.Connection, iso_datetime: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source, source_item_id, title, url, published_at, summary, content_hash, created_at
        FROM items
        WHERE COALESCE(published_at, created_at) >= ?
        ORDER BY COALESCE(published_at, created_at) DESC
        """,
        (iso_datetime,),
    )
    rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_existing_hashes(conn: sqlite3.Connection, source: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT content_hash
        FROM items
        WHERE source = ?
        """,
        (source,),
    )
    rows = cur.fetchall()
    return {r[0] for r in rows if r and r[0]}


def get_active_subscribers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT email, unsubscribe_token
        FROM subscribers
        WHERE status = 'active'
        ORDER BY created_at ASC
        """
    )
    rows = cur.fetchall()
    return [dict(r) for r in rows]