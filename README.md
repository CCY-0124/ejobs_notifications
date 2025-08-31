# BCIT eJOBS Notifier (Personal & Educational Use)

This repository contains a small, personal-use toolchain to fetch job postings from the BCIT Symplicity (eJOBS) portal, save them to CSV, and post **new** items to a Discord channel. It uses a saved **Playwright storage state** (`state.json`) for authentication. _This project is for personal and educational use only._

> Core scripts: `main.py` (fetch + Discord post) and `token_checker.py` (refresh/check session).

---

## New Features (2025)

- **Dual Discord Webhooks**: Separate testing and official channels for better error handling
- **Automated Session Monitoring**: Daily morning session checks with automatic notifications
- **Scheduled Job Scraping**: Every 2 hours automatic job scraping with fallback error handling
- **Smart Error Handling**: Errors and fallbacks go to testing channel, successful posts go to official channel

---

## How it works (high level)

- **Auth**: You sign in once with MFA in a real browser window. The script saves Playwright storage (cookies + localStorage) to `state.json`. Subsequent runs reuse that file; logging out in your normal browser does not remove `state.json`.
- **Fetch**: `main.py` opens a headless context with `state.json`, warms the target page, then calls the same JSON API the site uses to list jobs. It paginates and saves everything into `bcit_jobs.csv`.
- **Discord posting (de-duplicated)**: The script posts only jobs **on or after a cutoff date** (default: today in `America/Vancouver`) and only once per `job_id`. Older jobs are "seeded" as seen on the first run so you do not get backlog spam.
- **Optional co‑op filter**: In September you can set `BCIT_JOB_TYPE=21` in `.env` to fetch only co‑op roles; leave it blank now to fetch all jobs.
- **Automated monitoring**: Daily session checks and hourly job scraping with intelligent error handling.

Implementation details are visible in your code: `main.py` and `token_checker.py`.  filecite turn3file0  filecite turn4file0

---

## Requirements

- Python 3.11+ (tested on Windows)
- Node is **not** required, but Playwright needs its browser binaries
- Packages:
  ```bash
  pip install playwright python-dotenv requests beautifulsoup4 pandas schedule
  python -m playwright install
  ```

---

## Setup

1. **Clone the repo** and open a terminal in the project folder.
2. **Install dependencies** (see above).
3. **Create `.env`** (see configuration section below) and **add `.env` to `.gitignore`**.
4. **Generate `state.json`** using the token checker's interactive login (one time or when session expires):
   ```bash
   python token_checker.py
   ```
   - A browser will open; enter credentials and MFA if prompted.
   - On success, `state.json` will be saved in the project root.

> After `state.json` exists, you do not need to enter MFA each run; the scripts reuse the stored session until the server expires it.  filecite turn4file0

---

## Environment Configuration (.env)

Create a `.env` file in your project root with the following configuration:

```env
# =============================================================================
# DISCORD WEBHOOKS
# =============================================================================
# Testing webhook for notifications, errors, and status updates
TESTING_WEBHOOK_URL=https://discord.com/api/webhooks/1405038920659636274/pcote3UKuBs2VdYvvCcW8aRXTxX0am_y19f76UFjcJJV7TQD4WSQe8Jhd4eu2t4ABoor

# Official webhook for job postings (successful results only)
OFFICIAL_WEBHOOK_URL=https://discord.com/api/webhooks/1410429285872963714/ou8djaRgQ8A5w6H-rL81l2kClYPT6MHk2EdmgfATx1cEEh_7cNVzdnt5FxzT8ETPdzMv

# Legacy webhook (kept for backward compatibility, can be empty)
DISCORD_WEBHOOK_URL=

# =============================================================================
# BCIT LOGIN CREDENTIALS
# =============================================================================
# Only needed when refreshing state interactively (token_checker.py)
BCIT_USER=your_email@bcit.ca
BCIT_PASS=your_password

# =============================================================================
# SESSION MANAGEMENT
# =============================================================================
# File to store Playwright session state
STATE_FILE=state.json

# =============================================================================
# API AND TARGET CONFIGURATION
# =============================================================================
# Main page to scrape
TARGET_PAGE=https://bcit-csm.symplicity.com/students/app/jobs/search?perPage=20&page=1&sort=!postdate

# API endpoint for job data
CHECK_URL=https://bcit-csm.symplicity.com/api/v2/jobs

# Job sorting (default: newest first)
BCIT_SORT=!postdate

# Number of jobs per page
BCIT_PER_PAGE=20

# Job type filter (leave empty to fetch ALL jobs)
# In September, set to 21 for co-op only
BCIT_JOB_TYPE=

# =============================================================================
# POSTING CONFIGURATION
# =============================================================================
# Start date for job posting (YYYY-MM-DD format)
# Leave empty to use today's date
POST_SINCE=

# Timezone for date calculations
LOCAL_TZ=America/Vancouver

# =============================================================================
# OUTPUT FILES
# =============================================================================
# CSV file for job data
OUTPUT_CSV=bcit_jobs.csv

# JSON file to track seen job IDs
STATE_IDS=seen_job_ids.json

# =============================================================================
# REQUEST TUNING
# =============================================================================
# Delay between API requests (seconds)
REQ_SLEEP=0.4

# Request timeout (milliseconds)
REQ_TIMEOUT_MS=20000

# =============================================================================
# SCHEDULER CONFIGURATION
# =============================================================================
# Morning session check time (24-hour format)
MORNING_CHECK_TIME=08:00

# Job scraping interval (hours)
SCRAPING_INTERVAL_HOURS=2
```

---

## Usage

### 1) Manual job scraping and posting
```bash
python main.py
```
- Fetches all jobs (or co‑op only if you set `BCIT_JOB_TYPE=21` later).
- Saves `bcit_jobs.csv`.
- Posts only **new** jobs with `postdate >= POST_SINCE` (default: today) to Discord.
- The Discord message includes a **direct link** to the job details (no long description).  filecite turn3file0

### 2) Choose a different starting day
```bash
python main.py --since=2025-09-01
```

### 3) Start automated scheduler
```bash
python main.py --scheduler
```
- **Morning session check**: Every day at 8:00 AM, checks if session is alive
- **Hourly job scraping**: Every 2 hours, scrapes new jobs and posts to official Discord
- **Error handling**: All errors and notifications go to testing Discord channel
- **Fallback**: If official Discord fails, messages are sent to testing Discord

### 4) Check or refresh the session
```bash
python token_checker.py
```
- Calls the API using `state.json`.
- If it fails, it opens a browser for interactive login and saves a fresh `state.json`.  filecite turn4file0

---

## Automated Features

### Morning Session Check (8:00 AM daily)
- Automatically checks if your BCIT session is still valid
- Sends success/failure notifications to testing Discord
- If session expired, provides instructions to fix it

### Hourly Job Scraping (every 2 hours)
- Automatically scrapes new job postings
- Posts successful results to official Discord
- Sends status updates to testing Discord
- Includes error handling and fallback mechanisms

### Smart Error Handling
- **Testing Discord**: Receives all notifications, errors, and status updates
- **Official Discord**: Receives only successful job postings
- **Fallback**: If official Discord fails, messages are automatically sent to testing Discord

---

## Scheduling

### Windows Task Scheduler
1. Open **Task Scheduler** → **Create Basic Task**.
2. Trigger: **Daily** at your preferred time.
3. Action: **Start a program** → Program/script:
   ```
   C:\Path\To\python.exe
   ```
   Arguments:
   ```
   C:\Path\To\Project\main.py --scheduler
   ```
   Start in:
   ```
   C:\Path\To\Project
   ```

### GitHub Actions (optional; if you keep secrets safe)
```yaml
name: run-daily
on:
  schedule:
    - cron: "5 16 * * *"   # 16:05 UTC = 09:05 America/Vancouver (adjust as needed)
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install playwright python-dotenv requests beautifulsoup4 pandas schedule
      - run: python -m playwright install --with-deps
      - env:
          TESTING_WEBHOOK_URL: ${{ secrets.TESTING_WEBHOOK_URL }}
          OFFICIAL_WEBHOOK_URL: ${{ secrets.OFFICIAL_WEBHOOK_URL }}
          STATE_B64: ${{ secrets.STATE_B64 }}
        run: |
          echo "$STATE_B64" | base64 -d > state.json
          python main.py
```

> For Actions you would store a base64 of `state.json` in a repo secret (`STATE_B64`), and webhook URLs in `TESTING_WEBHOOK_URL` and `OFFICIAL_WEBHOOK_URL`. Keep in mind that sessions expire server-side; you may need to refresh `state.json` periodically from a trusted machine.

---

## Troubleshooting

- **Files are not being ignored**: If `bcit_jobs.csv`, `seen_job_ids.json`, `state.json`, `.env` are already tracked, remove them from the index once:
  ```bash
  git rm --cached bcit_jobs.csv seen_job_ids.json state.json .env
  git commit -m "Stop tracking generated files"
  ```
  Also ensure your `.gitignore` path patterns match where the files live.

- **Session expired**: If API returns 401/403 or non‑JSON, run:
  ```bash
  python token_checker.py
  ```
  Complete browser login and MFA; a fresh `state.json` is saved.  filecite turn4file0

- **Discord throttling or errors**: Check the webhook URLs, network, and that your content is under Discord limits. The current message uses concise text plus a link to the job details.  filecite turn3file0

- **Playwright not installed**: Ensure you ran `python -m playwright install` at least once on the machine where you run the scripts.

- **Corrupt `seen_job_ids.json`**: If the JSON was edited and now fails to parse, delete it to let the script recreate it, or implement an atomic writer/robust loader variant.

- **Scheduler not working**: Ensure you have the `schedule` package installed (`pip install schedule`) and are using the `--scheduler` flag.

- **Environment variables not working**: Make sure your `.env` file is in the project root and contains the correct variable names. Check for typos and ensure no extra spaces around the `=` sign.

---

## Project structure

```
.
├─ main.py              # Fetch pages, save CSV, post new jobs to Discord, automated scheduler
├─ token_checker.py     # Check API with saved state; re-login and save state.json if needed
├─ .env                 # Configuration file (create from template, do not commit)
├─ state.json           # Playwright storage (generated; do not commit)
├─ bcit_jobs.csv        # Output CSV (generated; do not commit)
├─ seen_job_ids.json    # De‑dup memory (generated; do not commit)
└─ .gitignore           # Make sure the generated files above are ignored
```

---

## Legal and acceptable-use note

This project is intended **only for personal and educational use**. Respect the website's Terms of Service and robots policy. Keep request rates low (`REQ_SLEEP`) and avoid heavy scraping. Do not redistribute data or credentials. You are responsible for how you use these scripts.

---
