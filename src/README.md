# Source Code Overview

This folder contains the core data collection, summarization, and email delivery logic for the DC Council digest.

## Files

- collect.py: RSS/Atom collector. Fetches feeds from config, filters by keywords for non-official sources, and inserts new items into the database.
- collect_youtube_live.py: YouTube API collector for live/upcoming streams and events.
- db.py: SQLite helpers for schema initialization and item/subscriber queries.
- digest.py: Weekly digest generator. Builds per-subscriber summaries, renders the HTML template, and sends emails.
- emailer_gmail.py: Gmail SMTP email sender.
- emailer_sendgrid.py: SendGrid email sender (optional).
- manage_subscribers.py: CLI tool to add/unsubscribe subscribers in the local SQLite database.
- server.py: Lightweight server endpoints (local/dev tooling).
- summarizer_openai.py: OpenAI summarizer for weekly bullet summaries with citations.
- utils.py: Shared utility helpers (hashing, date parsing, scoring, HTML cleanup).

## Notes

- The main daily ingestion entry point is collect.py.
- The weekly newsletter entry point is digest.py.
