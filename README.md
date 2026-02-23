# DC Council Agent

A lightweight pipeline that collects DC Council updates from multiple sources, stores them in SQLite, and sends a weekly email digest to subscribers. The newsletter includes a three-bullet AI summary with clickable sources and supports per-subscriber customization based on selected topics and free-text interests.

Subscribe [here](https://guozhan11.github.io/dc-council-agent/)!

---

## What this repo does

1. **Collectors** gather items from different sources (RSS, YouTube, etc.).
2. **SQLite** stores normalized items (deduped).
3. **Digest sender** pulls the last 7 days of items, ranks them, summarizes via OpenAI into a concise three-bullet brief, renders a clean HTML newsletter, and sends via Gmail SMTP.
4. **Subscriber service** (Google Apps Script) stores subscriber emails, unsubscribe tokens, and interest preferences in a Google Sheet and exposes endpoints used by the Python sender.

---

## Sources

The newsletter is generated from a mix of official and local news sources. Current feeds include:

- DC Council official Granicus hearings feed
- DC Council YouTube channel feed
- Google Alerts for major news mentions
- States Newsroom (DC Bureau)
- Washington Post (Politics, Local)
- Washington Times (Headlines)
- 51st.news (Latest)
- Popville
- Greater Greater Washington

---

## Required environment variables / GitHub Secrets

The weekly digest job needs these values (locally via `.env`, or in GitHub via **Settings → Secrets and variables → Actions**):

- `SUBSCRIBERS_API_URL`: Your Google Apps Script `/exec` deployment URL (used for `?path=active_subscribers`)
- `SUBSCRIBERS_API_KEY`: Shared key that protects the `active_subscribers` endpoint
- `OPENAI_API_KEY`: Used to generate the 3-bullet AI summary
- `GMAIL_SMTP_USERNAME`: Gmail address used to send the digest
- `GMAIL_SMTP_APP_PASSWORD`: Gmail App Password (requires 2FA enabled)

Optional:

- `TEST_TO_EMAIL`: Test recipient address
- `TEST_ONLY_MODE`: Set to `true`/`1` to send only to `TEST_TO_EMAIL`

---

## Folder structure

```text
.
├── .github/                   # GitHub workflows / configs
├── .env                       # Local environment variables (not committed)
├── .venv/                     # Local virtual environment
├── config.yaml                # Main project configuration
├── db.sqlite                  # Local SQLite database
├── requirements.txt           # Python dependencies
├── docs/                      # GitHub Pages static site (subscribe/unsubscribe pages)
├── src/                       # Main Python source code (digest + utilities)
├── template/                  # Email templates
├── x-api/                     # Experiments / scripts using X API (optional)
└── x-scraper/                 # Scraper experiments (optional)