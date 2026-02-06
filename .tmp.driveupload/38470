# DC Council Agent

A small end-to-end system that collects public **DC Council** updates from multiple sources, stores them in a database, and sends a **weekly email digest** to subscribers. The digest can optionally include an **AI-written summary** (with links to original sources).

---

## Why this exists

DC Council information is spread across different channels (official pages, video archives, YouTube streams, media mentions, etc.). This project consolidates those signals into one weekly update that is easy to skim.

---

## What it does

- **Collects updates** from multiple sources (primarily RSS/Atom feeds and other public endpoints)
- **Stores items** in a local SQLite database and deduplicates them
- **Ranks and groups** items (e.g., official hearings vs. other mentions)
- **Generates a weekly digest email** using an HTML template
- **Manages subscribers** via a lightweight Google Apps Script + Google Sheet workflow
- **Optional:** Uses the OpenAI API to produce a readable summary while keeping citations/links

---

## Repo structure (high level)

```text
.
├── src/            # Python code (collect, store, digest, email, OpenAI summarizer)
├── template/       # Jinja2 email templates (weekly_email.html)
├── docs/           # GitHub Pages site (subscribe/unsubscribe pages)
├── config.yaml     # Main configuration (feeds, ranking, email)
├── db.sqlite       # SQLite database (generated locally)
└── requirements.txt
