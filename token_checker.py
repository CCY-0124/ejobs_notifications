import os, urllib.parse, json, asyncio, requests
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# load .env file
load_dotenv()

USER    = (os.getenv("BCIT_USER") or "").strip()
PASS    = (os.getenv("BCIT_PASS") or "").strip()
WEBHOOK = (os.getenv("TESTING_WEBHOOK_UR") or "").strip()
CHECK_URL  = os.getenv("CHECK_URL", "https://bcit-csm.symplicity.com/api/v2/jobs")
RAW_PARAMS = os.getenv("CHECK_PARAMS", "perPage=1&sort=!postdate&json_mode=read_only&enable_translation=false")
PARAMS     = dict(urllib.parse.parse_qsl(RAW_PARAMS, keep_blank_values=True))
TARGET_PAGE= os.getenv("TARGET_PAGE", "https://bcit-csm.symplicity.com/students/app/jobs/search?perPage=20&page=1&sort=!postdate")

STATE_FILE = "state.json"

def discord(msg: str):
    if not WEBHOOK:
        print("[warn] DISCORD_WEBHOOK_URL missing; would send:\n", msg)
        return
    try:
        r = requests.post(WEBHOOK, json={"content": msg}, timeout=20)
        print(f"[discord] status={r.status_code}")
        if r.status_code >= 300:
            print("[discord] body:", r.text[:300])
    except Exception as e:
        print("[discord] error:", e)

async def interactive_login_and_save_state(p):
    """Interactive login once (you will enter MFA), then save state.json."""
    browser = await p.chromium.launch(headless=False)  # headful to allow manual MFA entry
    ctx = await browser.new_context(user_agent="Mozilla/5.0")
    page = await ctx.new_page()

    # Navigate to a page that triggers SSO
    await page.goto(TARGET_PAGE, wait_until="domcontentloaded")

    # Try common selectors for username/password and submit
    possible_user = ['input[type="email"]', 'input[name="username"]', '#username', 'input[type="text"]']
    possible_pass = ['input[type="password"]', '#password']
    possible_submit = ['button[type="submit"]', 'input[type="submit"]', 'button[name="login"]', 'button:has-text("Sign in")']

    try:
        filled = False
        for u_sel in possible_user:
            if await page.is_visible(u_sel, timeout=1500):
                await page.fill(u_sel, USER); filled = True; break
        if filled:
            for p_sel in possible_pass:
                if await page.is_visible(p_sel, timeout=1500):
                    await page.fill(p_sel, PASS); break
            for s_sel in possible_submit:
                if await page.is_visible(s_sel, timeout=1500):
                    await page.click(s_sel); break
    except Exception:
        pass

    print("[ACTION] Please enter the MFA code in the opened browser window (timeout 3 minutes).")
    await page.wait_for_url("**/students/app/jobs/search**", timeout=180000)
    await ctx.storage_state(path=STATE_FILE)
    print(f"[INFO] Saved storage state to: {STATE_FILE}")
    await browser.close()

async def check_with_saved_state(p):
    """Call the API using the saved state (headless)."""
    browser = await p.chromium.launch(headless=True)
    ctx = await browser.new_context(storage_state=STATE_FILE, user_agent="Mozilla/5.0")

    # Warm the session by visiting the page once
    page = await ctx.new_page()
    await page.goto(TARGET_PAGE, wait_until="domcontentloaded")

    # Use the same authenticated context to call the API
    res = await ctx.request.get(
        CHECK_URL,
        params=PARAMS,
        headers={
            "Accept": "application/json, text/plain, */*",
            "x-requested-system-user": "students",
            "Referer": TARGET_PAGE,
            "User-Agent": "Mozilla/5.0",
        },
        timeout=20000,
    )
    ok = (res.status == 200) and (res.headers.get("content-type","").startswith("application/json"))
    body = await res.text()
    await browser.close()
    return ok, res.status, res.headers.get("content-type"), len(body), body[:300].replace("\n"," ")

async def main():
    if not USER or not PASS:
        print("ERROR: Missing BCIT_USER or BCIT_PASS in .env")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with async_playwright() as p:
        # If no saved state, perform interactive login once
        if not os.path.exists(STATE_FILE):
            await interactive_login_and_save_state(p)

        try:
            ok, status, ctype, nbytes, preview = await check_with_saved_state(p)
            if ok:
                discord(
                    "OK: BCIT token check (state)\n"
                    f"- Time: {now}\n- Status: {status}\n- Bytes: {nbytes}\n- Params: {RAW_PARAMS}"
                )
            else:
                discord(
                    "FAILED: BCIT token check (state)\n"
                    f"- Time: {now}\n- Status: {status}\n- JSON: {ctype}\n- Body: `{preview}`\n"
                    "- Will attempt re-login."
                )
                # Try a fresh interactive login, then re-check
                await interactive_login_and_save_state(p)
                ok2, status2, ctype2, nbytes2, preview2 = await check_with_saved_state(p)
                if ok2:
                    discord(
                        "OK: BCIT token check after re-login\n"
                        f"- Time: {now}\n- Status: {status2}\n- Bytes: {nbytes2}"
                    )
                else:
                    discord(
                        "FAILED: BCIT token check still failing after re-login\n"
                        f"- Time: {now}\n- Status: {status2}\n- JSON: {ctype2}\n- Body: `{preview2}`"
                    )

        except PWTimeout as e:
            discord(f"ERROR: BCIT token check timeout\n- Time: {now}\n- Error: `{e}`")
        except Exception as e:
            discord(f"ERROR: BCIT token check exception\n- Time: {now}\n- Error: `{e}`")

if __name__ == "__main__":
    asyncio.run(main())
