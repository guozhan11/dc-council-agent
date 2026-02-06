# DC Council Agent

A lightweight pipeline that collects DC Council updates from multiple sources, stores them in SQLite, and sends a weekly email digest to subscribers. Subscribers are managed via a Google Apps Script + Google Sheet (subscribe/unsubscribe + “active subscribers” API). The email can optionally include an OpenAI-generated summary while still listing sources/links.

---

## What this repo does

1. **Collectors** gather items from different sources (RSS, YouTube, etc.).
2. **SQLite** stores normalized items (deduped).
3. **Digest sender** pulls the last 7 days of items, ranks them, optionally summarizes via OpenAI, renders an HTML email (Jinja2), and sends via Gmail SMTP.
4. **Subscriber service** (Google Apps Script) stores subscriber emails + unsubscribe tokens in a Google Sheet and exposes endpoints used by the Python sender.

---

## Folder structure

```text
.
├── .github/                   # GitHub workflows / configs (optional)
├── config.yaml                # Main project configuration
├── db.sqlite                  # Local SQLite database (generated)
├── requirements.txt           # Python dependencies
├── docs/                      # GitHub Pages static site (subscribe/unsubscribe pages)
├── src/                       # Main Python source code (digest + utilities)
├── template/                  # Email templates (Jinja2 HTML)
├── x-api/                     # Experiments / scripts using X API (optional)
└── x-scraper/                 # Scraper experiments (optional)