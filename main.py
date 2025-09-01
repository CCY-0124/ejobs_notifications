import os, asyncio, time, json, sys, argparse
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from zoneinfo import ZoneInfo
import schedule

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

load_dotenv()

# ---------- Config (from .env) ----------
STATE_FILE   = os.getenv("STATE_FILE", "state.json")
TARGET_PAGE  = os.getenv("TARGET_PAGE", "https://bcit-csm.symplicity.com/students/app/jobs/search?perPage=20&page=1&sort=!postdate")
API_URL      = os.getenv("CHECK_URL", "https://bcit-csm.symplicity.com/api/v2/jobs")
RAW_SORT     = os.getenv("BCIT_SORT", "!postdate")
PER_PAGE     = int(os.getenv("BCIT_PER_PAGE", "20"))
JOB_TYPE     = (os.getenv("BCIT_JOB_TYPE") or "").strip()   # leave empty to fetch ALL jobs
WEBHOOK      = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
OUTPUT_CSV   = os.getenv("OUTPUT_CSV", "bcit_jobs.csv")
STATE_IDS    = os.getenv("STATE_IDS", "seen_job_ids.json")
SLEEP        = float(os.getenv("REQ_SLEEP", "0.4"))
TIMEOUT_MS   = int(os.getenv("REQ_TIMEOUT_MS", "20000"))
TZ_NAME      = os.getenv("LOCAL_TZ", "America/Vancouver")
ENV_SINCE    = (os.getenv("POST_SINCE") or "").strip()      # optional YYYY-MM-DD in .env

# ---------- Discord URLs from .env ----------
TESTING_WEBHOOK = (os.getenv("TESTING_WEBHOOK_URL") or "").strip()
OFFICIAL_WEBHOOK = (os.getenv("OFFICIAL_WEBHOOK_URL") or "").strip()
MAX_MSG = int(os.getenv("DISCORD_MAX_CHARS", "1900"))

# ---------- Date helpers ----------
def local_today(tz_name: str = TZ_NAME) -> date:
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).date()

def parse_since(cli_since: Optional[str]) -> date:
    """Pick since date from CLI > .env > today (America/Vancouver)."""
    raw = cli_since or ENV_SINCE
    if raw:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    return local_today()

def parse_postdate(s: Optional[str]) -> Optional[date]:
    """Parse strings like 'Aug 12, 2025' to date."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None  # unknown format -> treat as old

# ---------- Discord helpers ----------
def discord_post_testing(msg: str) -> None:
    """Send message to testing Discord webhook."""
    if not TESTING_WEBHOOK:
        print("[WARN] TESTING_WEBHOOK_URL not set in .env")
        return
        
    try:
        requests.post(TESTING_WEBHOOK, json={"content": msg}, timeout=15)
        print(f"[TESTING] Discord message sent: {msg[:100]}...")
    except Exception as e:
        print(f"[ERROR] Failed to send to testing Discord: {e}", file=sys.stderr)

def discord_post_official(msg: str) -> None:
    """Send message to official Discord webhook with response code checking and fallback."""
    if not OFFICIAL_WEBHOOK:
        print("[WARN] OFFICIAL_WEBHOOK_URL not set in .env")
        return
    try:
        r = requests.post(OFFICIAL_WEBHOOK, json={"content": msg}, timeout=15)
        if r.status_code >= 300:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
        print(f"[OFFICIAL] status={r.status_code} len={len(msg)}")
    except Exception as e:
        print(f"[ERROR] Official webhook failed: {e}", file=sys.stderr)
        discord_post_testing(f"Official webhook failed: {e}\nFirst 300 chars:\n{msg[:300]}")

def split_messages(lines, limit=1900):
    """Split message lines into chunks that fit within Discord's character limit."""
    chunks, cur, cur_len = [], [], 0
    for line in lines:
        add = len(line) + 1
        if cur and cur_len + add > limit:
            chunks.append("\n".join(cur)); cur = [line]; cur_len = add
        else:
            cur.append(line); cur_len += add
    if cur: chunks.append("\n".join(cur))
    return chunks

def discord_post_batch(jobs):
    """Post multiple jobs in safe-sized chunks with correct page info and character limit."""
    if not jobs: return
    
    # Start with header
    chunks = []
    current_chunk = [f"**New BCIT Jobs Found, total new jobs: {len(jobs)}**"]
    current_length = len(current_chunk[0])
    
    for i, job in enumerate(jobs, 1):
        job_id = job.get("job_id")
        title = job.get("job_title", "(no title)")
        company = job.get("name", "")
        location = job.get("job_location", "")
        postdate = job.get("postdate", "")
        deadline = job.get("deadline", "")
        comp_from = job.get("compensation_from")
        comp_to = job.get("compensation_to")
        comp_freq = job.get("compensation_frequency") or ""
        
        # Get page info for accurate links
        page_no = job.get("_page_no")
        per_page = job.get("_per_page", PER_PAGE)
        
        # Build job lines
        job_lines = [
            f"{i}. {title} — {company}",
            f"Posted: {postdate}  Deadline: {deadline}"
        ]
        
        if comp_from or comp_to:
            job_lines.append(f"Compensation: {comp_from or '?'}–{comp_to or '?'} {comp_freq}")
        
        if job_id:
            # Use correct page info for accurate links
            link = build_job_link(job_id, page_no, TARGET_PAGE, per_page, RAW_SORT, JOB_TYPE or None)
            job_lines.append(f"Link: {link}")
        
        job_lines.append("")  # Empty line for spacing
        
        # Calculate total length of this job
        job_content = "\n".join(job_lines)
        job_length = len(job_content)
        
        # Check if adding this job would exceed the limit
        if current_length + job_length > MAX_MSG:
            # Current chunk is full, save it and start a new one
            chunks.append("\n".join(current_chunk))
            current_chunk = [f" "]
            current_length = len(current_chunk[0])
        
        # Add job to current chunk
        current_chunk.extend(job_lines)
        current_length += job_length
    
    # Add the last chunk if it has content
    if len(current_chunk) > 1:  # More than just the header
        chunks.append("\n".join(current_chunk))
    
    # Post all chunks
    for chunk in chunks:
        discord_post_official(chunk)

def discord_post(job: Dict[str, Any], page_no: int | None = None, per_page_hint: int | None = None) -> None:
    """Post single job to official Discord (kept for backward compatibility)."""
    if not OFFICIAL_WEBHOOK:
        return
    per_page = per_page_hint or PER_PAGE

    job_id = job.get("job_id")
    title = job.get("job_title", "(no title)")
    company = job.get("name", "")
    location = job.get("job_location", "")
    postdate = job.get("postdate", "")
    deadline = job.get("deadline", "")
    comp_from = job.get("compensation_from")
    comp_to   = job.get("compensation_to")
    comp_freq = job.get("compensation_frequency") or ""

    lines = [
        f"**{title}** — {company}",
        location,
        f"Posted: {postdate}  Deadline: {deadline}",
    ]
    if comp_from or comp_to:
        lines.append(f"Comp: {comp_from or '?'}–{comp_to or '?'} {comp_freq}")

    if job_id:
        link = build_job_link(job_id, page_no, TARGET_PAGE, per_page, RAW_SORT, JOB_TYPE or None)
        lines.append(f"Link: {link}")

    try:
        requests.post(OFFICIAL_WEBHOOK, json={"content": "\n".join([x for x in lines if x.strip()])}, timeout=15)
    except Exception as e:
        print(f"[warn] Discord post failed: {e}", file=sys.stderr)

# ---------- Session check helpers ----------
async def check_session_alive() -> bool:
    """Check if the saved session is still valid."""
    if not os.path.exists(STATE_FILE):
        return False
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(storage_state=STATE_FILE, user_agent="Mozilla/5.0")
            
            # Warm session by visiting the page
            page = await ctx.new_page()
            await page.goto(TARGET_PAGE, wait_until="domcontentloaded")
            
            # Test API call
            res = await ctx.request.get(
                API_URL,
                params={"perPage": 1, "sort": RAW_SORT, "json_mode": "read_only"},
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "x-requested-system-user": "students",
                    "Referer": TARGET_PAGE,
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=TIMEOUT_MS,
            )
            
            await browser.close()
            return (res.status == 200 and 
                   res.headers.get("content-type", "").startswith("application/json"))
    except Exception as e:
        print(f"[ERROR] Session check failed: {e}")
        return False

async def morning_session_check():
    """Morning session check - send status to testing Discord."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if await check_session_alive():
        discord_post_testing(f"Good morning! BCIT session check successful\n- Time: {now}\n- Status: Session working normally")
    else:
        discord_post_testing(
            f"Good morning! BCIT session expired\n"
            f"- Time: {now}\n"
            f"- Status: Session expired\n"
            f"- Please run `python token_checker.py` to fix the session"
        )

async def hourly_job_scrape():
    """Scrape jobs every two hours and send to official Discord."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Check session first
        if not await check_session_alive():
            discord_post_testing(f"Scheduled scraping failed: Session expired\n- Time: {now}\n- Please fix session first")
            return
        
        # Perform job scraping
        since_day = local_today()
        await pull_jobs(since_day)
        
        discord_post_testing(f"Scheduled scraping completed\n- Time: {now}\n- Scraping date: {since_day}")
        
    except Exception as e:
        error_msg = f"Scheduled scraping error\n- Time: {now}\n- Error: {str(e)}"
        discord_post_testing(error_msg)

# ---------- Misc helpers ----------
def strip_html(html: str, limit: int = 350) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return (text[: limit - 1] + "…") if len(text) > limit else text

def load_seen() -> set:
    try:
        with open(STATE_IDS, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_seen(seen: set) -> None:
    with open(STATE_IDS, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)

def build_job_link(job_id: str,
                   page_no: int | None,
                   base_url: str,
                   per_page: int,
                   sort: str,
                   job_type: str | None = None) -> str:
    """
    Build a stable link that opens the correct job in the SPA:
    - preserve existing query params from base_url (e.g., keywords)
    - set perPage, page, sort to match our fetch
    - include job_type if set
    - include currentJobId for the details drawer
    """
    u = urlparse(base_url)
    q = dict(parse_qsl(u.query, keep_blank_values=True))

    # Preserve existing filters (e.g., keywords) already in TARGET_PAGE
    q["perPage"] = str(per_page)
    if page_no is not None:
        q["page"] = str(page_no)
    q["sort"] = sort
    if job_type:
        q["job_type"] = job_type
    q["currentJobId"] = job_id

    return urlunparse(u._replace(query=urlencode(q)))

async def fetch_page(ctx, page_no: int, per_page: int) -> Dict[str, Any]:
    params = {
        "perPage": per_page,
        "page": page_no,
        "sort": RAW_SORT,
        "json_mode": "read_only",
        "enable_translation": "false",
    }
    # Include job_type only when set in .env (empty = fetch ALL)
    if JOB_TYPE:
        params["job_type"] = JOB_TYPE

    res = await ctx.request.get(
        API_URL,
        params=params,
        headers={
            "Accept": "application/json, text/plain, */*",
            "x-requested-system-user": "students",
            "Referer": TARGET_PAGE,
            "User-Agent": "Mozilla/5.0",
        },
        timeout=TIMEOUT_MS,
    )
    if res.status == 200 and (res.headers.get("content-type","").startswith("application/json")):
        return await res.json()
    else:
        body = (await res.text())[:300].replace("\n"," ")
        raise RuntimeError(f"HTTP {res.status} {res.headers.get('content-type')} :: {body}")

# ---------- Main pull ----------
async def pull_jobs(since_day: date):
    if not os.path.exists(STATE_FILE):
        raise RuntimeError(f"state.json not found at '{STATE_FILE}'. Run your login flow to generate it.")

    all_rows: List[Dict[str, Any]] = []
    seen = load_seen()
    seed_mode = not os.path.exists(STATE_IDS)  # first run -> seed older jobs as seen
    new_count = 0
    new_jobs = []  # Collect new jobs to post together

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=STATE_FILE, user_agent="Mozilla/5.0")

        # Warm session (optional)
        page = await ctx.new_page()
        await page.goto(TARGET_PAGE, wait_until="domcontentloaded")

        page_no = 1
        total = None
        per_page = PER_PAGE

        while True:
            data = await fetch_page(ctx, page_no, per_page)
            total = data.get("total", total)
            models = data.get("models", [])
            if data.get("perPage"):
                per_page = data["perPage"]

            if not models:
                break

            for job in models:
                # Flatten row for CSV
                row = {
                    "job_id": job.get("job_id"),
                    "job_title": job.get("job_title"),
                    "company": job.get("name"),
                    "postdate": job.get("postdate"),
                    "deadline": job.get("deadline"),
                    "location": job.get("job_location"),
                    "type": ", ".join(job.get("job_type", []) or []),
                    "onsite_remote": (job.get("symp_remote_onsite") or {}).get("label"),
                    "comp_from": job.get("compensation_from"),
                    "comp_to": job.get("compensation_to"),
                    "comp_freq": job.get("compensation_frequency"),
                    "desc_preview": strip_html(job.get("job_desc")),
                    "visual_id": job.get("visual_id"),
                }
                all_rows.append(row)

                # Posting logic with since filter
                jid = job.get("job_id")
                jd  = parse_postdate(job.get("postdate"))
                is_new_enough = (jd is not None and jd >= since_day)

                if seed_mode and jid and not is_new_enough:
                    # First run: mark older jobs as seen so we do not post the backlog later
                    if jid not in seen:
                        seen.add(jid)
                    continue

                if is_new_enough and jid and jid not in seen:
                    # Collect job with page info for accurate links
                    job_with_page = {
                        **job,
                        "_page_no": page_no,
                        "_per_page": per_page
                    }
                    new_jobs.append(job_with_page)
                    seen.add(jid)
                    new_count += 1

            if total and per_page and page_no * per_page >= total:
                break
            page_no += 1
            await asyncio.sleep(SLEEP)

        await ctx.storage_state(path=STATE_FILE)
        print(f"[INFO] Updated storage state written to: {os.path.abspath(STATE_FILE)}")
        await browser.close()

    # Post all new jobs in safe-sized chunks with correct page info
    if new_jobs:
        discord_post_batch(new_jobs)

    # Save CSV
    if all_rows:
        df = pd.DataFrame(all_rows)
        cols = [
            "job_id","job_title","company","postdate","deadline","location",
            "type","onsite_remote","comp_from","comp_to","comp_freq","desc_preview","visual_id"
        ]
        for c in df.columns:
            if c not in cols:
                cols.append(c)
        df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig", columns=cols)
        print(f"Saved {len(df)} rows to {OUTPUT_CSV}")

    save_seen(seen)
    print(f"[SINCE] {since_day}  |  New jobs posted to Discord: {new_count}")

# ---------- Scheduler functions ----------
def run_morning_check():
    """Run the async morning session check once (inside its own loop)."""
    import asyncio
    asyncio.run(morning_session_check())  # async def morning_session_check()

def run_hourly_scrape():
    """Run the async hourly scrape once (inside its own loop)."""
    import asyncio
    asyncio.run(hourly_job_scrape())      # async def hourly_job_scrape()

def start_scheduler():
    """Start cron-like tasks using the schedule library (sync loop)."""
    import schedule, time

    # Register jobs
    schedule.every().day.at("08:00").do(run_morning_check)
    schedule.every(2).hours.do(run_hourly_scrape)

    # Fire once immediately so you don't wait 2 hours for the first run
    run_hourly_scrape()

    print("Automated tasks started:")
    print("   - Daily session check at 8:00 AM")
    print("   - Job scraping every 2 hours")
    print("   - Press Ctrl+C to stop")

    # Sync loop: no global asyncio loop is running here
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BCIT jobs fetcher")
    parser.add_argument("--since", help="YYYY-MM-DD; default = today in America/Vancouver (or POST_SINCE in .env)")
    parser.add_argument("--scheduler", action="store_true", help="Start the automated scheduler")
    args = parser.parse_args()

    if args.scheduler:
        # Pure sync mode; callbacks will run their own event loops
        start_scheduler()
    else:
        # One-shot run
        since_day = parse_since(args.since)
        print(f"[INFO] Using since date: {since_day} (TZ={TZ_NAME})")
        import asyncio
        asyncio.run(pull_jobs(since_day))

