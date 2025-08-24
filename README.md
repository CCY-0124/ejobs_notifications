# BCIT eJOBS Notifier (Personal & Educational Use)

This repository contains a small, personal-use toolchain to fetch job postings from the BCIT Symplicity (eJOBS) portal, save them to CSV, and post **new** items to a Discord channel. It uses a saved **Playwright storage state** (`state.json`) for authentication. _This project is for personal and educational use only._

> Core scripts: `main.py` (fetch + Discord post) and `token_checker.py` (refresh/check session).

---

## How it works (high level)

- **Auth**: You sign in once with MFA in a real browser window. The script saves Playwright storage (cookies + localStorage) to `state.json`. Subsequent runs reuse that file; logging out in your normal browser does not remove `state.json`.
- **Fetch**: `main.py` opens a headless context with `state.json`, warms the target page, then calls the same JSON API the site uses to list jobs. It paginates and saves everything into `bcit_jobs.csv`.
- **Discord posting (de-duplicated)**: The script posts only jobs **on or after a cutoff date** (default: today in `America/Vancouver`) and only once per `job_id`. Older jobs are “seeded” as seen on the first run so you do not get backlog spam.
- **Optional co‑op filter**: In September you can set `BCIT_JOB_TYPE=21` in `.env` to fetch only co‑op roles; leave it blank now to fetch all jobs.

Implementation details are visible in your code: `main.py` and `token_checker.py`. fileciteturn3file0 fileciteturn4file0

---

## Requirements

- Python 3.11+ (tested on Windows)
- Node is **not** required, but Playwright needs its browser binaries
- Packages:
  ```bash
  pip install playwright python-dotenv requests beautifulsoup4 pandas
  python -m playwright install
  ```

---

## Setup

1. **Clone the repo** and open a terminal in the project folder.
2. **Install dependencies** (see above).
3. **Create `.env`** (see example below) and **add `.env` to `.gitignore`**.
4. **Generate `state.json`** using the token checker’s interactive login (one time or when session expires):
   ```bash
   python token_checker.py
   ```
   - A browser will open; enter credentials and MFA if prompted.
   - On success, `state.json` will be saved in the project root.

> After `state.json` exists, you do not need to enter MFA each run; the scripts reuse the stored session until the server expires it. fileciteturn4file0

---

## .env example

```env
# Session
STATE_FILE=state.json

# Discord (optional)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxx/yyy

# API + page
TARGET_PAGE=https://bcit-csm.symplicity.com/students/app/jobs/search?perPage=20&page=1&sort=!postdate
CHECK_URL=https://bcit-csm.symplicity.com/api/v2/jobs
BCIT_PER_PAGE=20
BCIT_SORT=!postdate

# Leave blank now to fetch ALL jobs
BCIT_JOB_TYPE=

# In September, enable co-op only by uncommenting the next line:
# BCIT_JOB_TYPE=21

# Posting window (default = today in America/Vancouver if blank)
POST_SINCE=
LOCAL_TZ=America/Vancouver

# Output
OUTPUT_CSV=bcit_jobs.csv
STATE_IDS=seen_job_ids.json

# Request tuning
REQ_SLEEP=0.4
REQ_TIMEOUT_MS=20000

# Only needed when refreshing state interactively (token_checker.py)
# BCIT_USER=you@bcit.ca
# BCIT_PASS=your_password
```

---

## Daily usage

### 1) Pull and post new jobs
```bash
python main.py
```
- Fetches all jobs (or co‑op only if you set `BCIT_JOB_TYPE=21` later).
- Saves `bcit_jobs.csv`.
- Posts only **new** jobs with `postdate >= POST_SINCE` (default: today) to Discord.
- The Discord message includes a **direct link** to the job details (no long description). fileciteturn3file0

### 2) Choose a different starting day
```bash
python main.py --since=2025-09-01
```

### 3) Check or refresh the session
```bash
python token_checker.py
```
- Calls the API using `state.json`.
- If it fails, it opens a browser for interactive login and saves a fresh `state.json`. fileciteturn4file0

---

## Scheduling

### Windows Task Scheduler (basic outline)
1. Open **Task Scheduler** → **Create Basic Task**.
2. Trigger: **Daily** at your preferred time.
3. Action: **Start a program** → Program/script:
   ```
   C:\Path\To\python.exe
   ```
   Arguments:
   ```
   C:\Path\To\Project\main.py
   ```
   Start in:
   ```
   C:\Path\To\Project
   ```
4. (Optional) Create a second task for `token_checker.py` earlier in the day to detect session expiry.

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
      - run: pip install playwright python-dotenv requests beautifulsoup4 pandas
      - run: python -m playwright install --with-deps
      - env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
          STATE_B64: ${{ secrets.STATE_B64 }}
        run: |
          echo "$STATE_B64" | base64 -d > state.json
          python main.py
```
> For Actions you would store a base64 of `state.json` in a repo secret (`STATE_B64`), and a webhook URL in `DISCORD_WEBHOOK_URL`. Keep in mind that sessions expire server-side; you may need to refresh `state.json` periodically from a trusted machine.

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
  Complete browser login and MFA; a fresh `state.json` is saved. fileciteturn4file0

- **Discord throttling or errors**: Check the webhook URL, network, and that your content is under Discord limits. The current message uses concise text plus a link to the job details. fileciteturn3file0

- **Playwright not installed**: Ensure you ran `python -m playwright install` at least once on the machine where you run the scripts.

- **Corrupt `seen_job_ids.json`**: If the JSON was edited and now fails to parse, delete it to let the script recreate it, or implement an atomic writer/robust loader variant.

---

## Project structure (suggested)

```
.
├─ main.py              # Fetch pages, save CSV, post new jobs to Discord
├─ token_checker.py     # Check API with saved state; re-login and save state.json if needed
├─ state.json           # Playwright storage (generated; do not commit)
├─ bcit_jobs.csv        # Output CSV (generated; do not commit)
├─ seen_job_ids.json    # De‑dup memory (generated; do not commit)
├─ .env                 # Local config and secrets (do not commit)
└─ .gitignore           # Make sure the generated files above are ignored
```

---

## Legal and acceptable-use note

This project is intended **only for personal and educational use**. Respect the website’s Terms of Service and robots policy. Keep request rates low (`REQ_SLEEP`) and avoid heavy scraping. Do not redistribute data or credentials. You are responsible for how you use these scripts.

---
