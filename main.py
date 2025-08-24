import os, asyncio, time, json, sys, argparse
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from zoneinfo import ZoneInfo

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

def build_job_link(job_id: str, base_url: str) -> str:
    """Append currentJobId to TARGET_PAGE robustly."""
    u = urlparse(base_url)
    q = dict(parse_qsl(u.query, keep_blank_values=True))
    q["currentJobId"] = job_id
    return urlunparse(u._replace(query=urlencode(q)))

def discord_post(job: Dict[str, Any]) -> None:
    if not WEBHOOK:
        return

    job_id = job.get("job_id")
    title = job.get("job_title", "(no title)")
    company = job.get("name", "")
    location = job.get("job_location", "")
    postdate = job.get("postdate", "")
    deadline = job.get("deadline", "")

    comp_from = job.get("compensation_from")
    comp_to = job.get("compensation_to")
    comp_freq = job.get("compensation_frequency") or ""

    lines = [
        f"**{title}** — {company}",
        location,
        f"Posted: {postdate}  Deadline: {deadline}",
    ]

    if comp_from or comp_to:
        lines.append(f"Comp: {comp_from or '?'}–{comp_to or '?'} {comp_freq}")

    # Add a direct link to the job details
    if job_id:
        link = build_job_link(job_id, TARGET_PAGE)
        lines.append(f"Link: {link}")

    try:
        requests.post(WEBHOOK, json={"content": "\n".join([x for x in lines if x.strip()])}, timeout=15)
    except Exception as e:
        print(f"[warn] Discord post failed: {e}", file=sys.stderr)

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
                    discord_post(job)  # post only NEW jobs since cutoff
                    seen.add(jid)
                    new_count += 1

            if total and per_page and page_no * per_page >= total:
                break
            page_no += 1
            await asyncio.sleep(SLEEP)

        await browser.close()

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

async def main():
    parser = argparse.ArgumentParser(description="Pull BCIT jobs using state.json; post only those since a date")
    parser.add_argument("--since", help="YYYY-MM-DD; default = today in America/Vancouver (or POST_SINCE in .env)")
    args = parser.parse_args()

    since_day = parse_since(args.since)
    print(f"[INFO] Using since date: {since_day} (TZ={TZ_NAME})")
    await pull_jobs(since_day)

if __name__ == "__main__":
    asyncio.run(main())
