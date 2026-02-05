import os
from flask import Flask, request, abort

from db import connect, init_db

app = Flask(__name__)

# Change this if your db path is different
DB_PATH = os.environ.get("DB_PATH", "db.sqlite")


@app.get("/unsubscribe")
def unsubscribe():
    email = (request.args.get("email") or "").strip()
    token = (request.args.get("token") or "").strip()

    if not email or not token:
        abort(400, "Missing email or token")

    conn = connect(DB_PATH)
    init_db(conn)

    cur = conn.cursor()
    # Adjust column names if your schema differs
    cur.execute(
        """
        UPDATE subscribers
        SET is_active = 0, unsubscribed_at = CURRENT_TIMESTAMP
        WHERE email = ? AND unsubscribe_token = ? AND is_active = 1
        """,
        (email, token),
    )
    conn.commit()

    if cur.rowcount == 0:
        # Either already unsubscribed, wrong token, or email not found
        return (
            "<h3>Unsubscribe link is invalid or already used.</h3>"
            "<p>If you believe this is a mistake, reply to the email and we will fix it.</p>",
            400,
        )

    return (
        "<h2>You are unsubscribed.</h2>"
        "<p>You will no longer receive DC Council Weekly Digest emails.</p>",
        200,
    )


if __name__ == "__main__":
    # local dev
    app.run(host="127.0.0.1", port=8000, debug=True)
