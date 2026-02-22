import sys
import os
import secrets
import yaml

from db import connect, init_db


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def add_subscriber(db_path: str, email: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    token = secrets.token_urlsafe(24)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO subscribers (email, status, unsubscribe_token)
        VALUES (?, 'active', ?)
        """,
        (email, token),
    )
    conn.commit()
    print(f"Added subscriber: {email}")


def unsubscribe(db_path: str, email: str) -> None:
    conn = connect(db_path)
    init_db(conn)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE subscribers
        SET status = 'unsubscribed'
        WHERE email = ?
        """,
        (email,),
    )
    conn.commit()
    print(f"Unsubscribed: {email}")


def main() -> int:
    if len(sys.argv) < 3:
        print('Usage: python src/manage_subscribers.py add someone@example.com')
        print('       python src/manage_subscribers.py unsubscribe someone@example.com')
        return 1

    cmd = sys.argv[1].strip().lower()
    email = sys.argv[2].strip()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cfg = load_config(os.path.join(repo_root, "config.yaml"))
    db_path = cfg["storage"]["db_path"]
    if not os.path.isabs(db_path):
        db_path = os.path.join(repo_root, db_path)

    if cmd == "add":
        add_subscriber(db_path, email)
    elif cmd == "unsubscribe":
        unsubscribe(db_path, email)
    else:
        print('Unknown command. Use "add" or "unsubscribe".')
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
