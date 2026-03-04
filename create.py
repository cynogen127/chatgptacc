"""
ChatGPT Account Creator - Python 3 
Original: Node.js/Playwright by wahdalo
Converted to: Python 3 using playwright-python, bs4, faker
"""

import asyncio
import json
import os
import random
import re
import string
import tempfile
import time
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from faker import Faker
from playwright.async_api import async_playwright, BrowserContext, Page

fake = Faker()

CONFIG_FILE   = "config.json"
ACCOUNTS_FILE = "accounts.txt"

DEFAULT_CONFIG = {
    "headless": False,
    "slow_mo": 500,
    "timeout": 30000,
    "password": None,
}

def load_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    if Path(CONFIG_FILE).exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            config.update(data)
            if config.get("password") and len(config["password"]) < 12:
                log("Warning: password is less than 12 characters.", level="WARNING")
        except Exception as e:
            log(f"Error loading config: {e}, using defaults", level="WARNING")
    else:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        log(f"Created default config file: {CONFIG_FILE}")
    return config

def log(message: str, level: str = "INFO", progress: str = None):
    ts    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    label = progress if progress else level
    print(f"[{ts}] [{label}] {message}", flush=True)

def randstr(length: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

def rnd(a: float, b: float) -> float:
    return random.uniform(a, b)

def generate_random_birthday() -> dict:
    today = datetime.today()
    year  = random.randint(today.year - 65, today.year - 18)
    month = random.randint(1, 12)
    if month in (1, 3, 5, 7, 8, 10, 12):
        max_day = 31
    elif month in (4, 6, 9, 11):
        max_day = 30
    else:
        max_day = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    return {"year": year, "month": month, "day": random.randint(1, max_day)}

async def browser_get_email(context: BrowserContext) -> tuple:

    email_page = await context.new_page()
    await email_page.goto("https://generator.email/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    html = await email_page.content()
    soup = BeautifulSoup(html, "html.parser")

    domains = []

    for p in soup.select(".e7m.tt-suggestions div > p"):
        t = p.get_text(strip=True)
        if t:
            domains.append(t)

    if not domains:
        for el in soup.select("[data-value]"):
            v = el.get("data-value", "").strip()
            if v and "." in v and "@" not in v:
                domains.append(v)

    if not domains:
        for opt in soup.select("select option"):
            t = opt.get_text(strip=True)
            if t and "." in t and "@" not in t:
                domains.append(t)

    if not domains:
        for inp in soup.find_all("input"):
            val = inp.get("value", "") or inp.get("placeholder", "")
            if "@" in val:
                domains.append(val.split("@")[1].strip())

    if not domains:
        found = re.findall(r'@([\w.-]+\.[a-z]{2,})', html)
        domains = list(dict.fromkeys(found))

    if not domains:
        await email_page.close()
        raise RuntimeError("Could not extract any domain from generator.email")

    domain     = random.choice(list(dict.fromkeys(domains)))
    first_name = re.sub(r"[\"']", "", fake.first_name())
    last_name  = re.sub(r"[\"']", "", fake.last_name())
    email      = f"{first_name}{last_name}{randstr(5)}@{domain}".lower()

    log(f"Generated email: {email}")

    username = email.split("@")[0]
    inbox_url = f"https://generator.email/{username}@{domain}/"
    try:
        await email_page.goto(inbox_url, wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass  

    return email, first_name, last_name, email_page


async def browser_get_otp(email_page: Page, email: str,
                          max_retries: int = 12, delay: int = 5) -> str | None:
    username, domain = email.split("@", 1)
    inbox_url = f"https://generator.email/{username}@{domain}/"

    for attempt in range(max_retries):
        try:
            await email_page.goto(inbox_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1)

            html = await email_page.content()
            soup = BeautifulSoup(html, "html.parser")
            otp_el = soup.select_one(
                "#email-table > div.e7m.list-group-item.list-group-item-info "
                "> div.e7m.subj_div_45g45gg"
            )
            if not otp_el:
                otp_el = soup.select_one(".subj_div_45g45gg")

            if not otp_el:
                for tag in soup.find_all(string=re.compile(r'\b\d{6}\b')):
                    otp_el = tag.parent
                    break

            if otp_el:
                raw   = otp_el.get_text(strip=True)
                match = re.search(r'\b(\d{6})\b', raw)
                if match:
                    log(f"Retrieved OTP: {match.group(1)}")
                    return match.group(1)

            full_text = soup.get_text()
            for pattern in [
                r'(?:verification|verify|code|otp)[^\d]{0,40}(\d{6})',
                r'(\d{6})(?:[^\d]{0,40}(?:verification|verify|code|otp))',
                r'\b(\d{6})\b',
            ]:
                m = re.search(pattern, full_text, re.IGNORECASE)
                if m:
                    log(f"Retrieved OTP (fallback scan): {m.group(1)}")
                    return m.group(1)

            log(f"OTP not found yet, retrying in {delay}s... ({attempt+1}/{max_retries})")
            await asyncio.sleep(delay)

        except Exception as e:
            log(f"OTP poll error (attempt {attempt+1}): {e}", level="WARNING")
            await asyncio.sleep(delay)

    log("Failed to get OTP after all retries", level="ERROR")
    return None


def save_account(email: str, password: str):
    try:
        with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{email}|{password}\n")
        log(f"Saved: {email}")
    except Exception as e:
        log(f"Save error: {e}", level="ERROR")

STEALTH_SCRIPT = """
(function() {
    Object.defineProperty(navigator, 'webdriver',  { get: () => undefined, configurable: true });
    Object.defineProperty(navigator, 'languages',  { get: () => ['en-US', 'en'], configurable: true });
    Object.defineProperty(navigator, 'plugins',    {
        get: () => ({ length: 0, item: () => null, namedItem: () => null, refresh: () => {} }),
        configurable: true
    });
    const orig = window.navigator.permissions && window.navigator.permissions.query;
    if (orig) {
        window.navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : orig(p);
    }
    ['__marionette','__fxdriver','_driver','_selenium',
     '__driver_evaluate','__webdriver_evaluate'].forEach(k => {
        try { delete navigator[k]; } catch(e) {}
    });
})();
"""

FF_VER     = "131.0"
USER_AGENT = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{FF_VER}) Gecko/20100101 Firefox/{FF_VER}"

async def create_account(account_number: int, total: int,
                         config: dict, created: list) -> bool:
    prog     = f"{account_number}/{total}"
    password = config.get("password")
    if not password:
        log("No password in config.json!", level="ERROR", progress=prog)
        return False
    if len(password) < 12:
        log(f"Password only {len(password)} chars (need >=12).", level="WARNING", progress=prog)

    temp_dir = tempfile.mkdtemp(prefix=f"chatgpt_{account_number}_{int(time.time())}_")

    try:
        async with async_playwright() as pw:
            context = await pw.firefox.launch_persistent_context(
                temp_dir,
                headless=config.get("headless", False),
                viewport={"width": 1366, "height": 768},
                user_agent=USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
                device_scale_factor=0.9,
                has_touch=False,
                is_mobile=False,
                ignore_https_errors=True,
                bypass_csp=True,
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
                firefox_user_prefs={
                    "dom.webdriver.enabled": False,
                    "useAutomationExtension": False,
                    "marionette.enabled": False,
                },
                slow_mo=config.get("slow_mo", 0),
            )

            try:
                email, first_name, last_name, email_page = await browser_get_email(context)
            except Exception as e:
                log(f"Email generation failed: {e}", level="ERROR", progress=prog)
                await context.close()
                return False

            name     = f"{first_name} {last_name}"
            birthday = generate_random_birthday()

            page = await context.new_page()
            await page.add_init_script(STEALTH_SCRIPT)

            try:
                await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
            except Exception as e:
                log(f"Navigate error: {e}", level="ERROR", progress=prog)
                return False

            log("Clicking 'Sign up'", progress=prog)
            try:
                xpath    = '/html/body/div[2]/div[1]/div/div[2]/div/header/div[3]/div[2]/div/div/div/button[2]/div'
                btn      = page.locator(f"xpath={xpath}")
                await btn.wait_for(state="visible", timeout=30000)
                await asyncio.sleep(3)
                await btn.click(timeout=10000)
                await asyncio.sleep(rnd(1, 2))
            except Exception as e:
                log(f"Sign-up click error: {e}", level="ERROR", progress=prog)
                return False

            try:
                ei = page.get_by_role("textbox", name="Email address")
                await ei.wait_for(state="visible", timeout=15000)
                await ei.fill(email)
                await ei.blur()
                await asyncio.sleep(rnd(2, 3))
            except Exception as e:
                log(f"Email fill error: {e}", level="ERROR", progress=prog)
                return False

            try:
                c1 = page.get_by_role("button", name="Continue", exact=True)
                await c1.wait_for(state="visible", timeout=10000)
                await asyncio.sleep(rnd(0.5, 1))
                try:
                    await asyncio.gather(
                        page.wait_for_load_state("domcontentloaded"),
                        c1.click(timeout=10000),
                    )
                except Exception:
                    await c1.click(timeout=10000)
                await asyncio.sleep(1)
                if "error" in page.url.lower():
                    log("Error URL after email submit", level="ERROR", progress=prog)
                    return False
            except Exception as e:
                log(f"Continue (email) error: {e}", level="ERROR", progress=prog)
                return False

            try:
                pi = page.get_by_role("textbox", name="Password")
                await pi.wait_for(state="visible", timeout=15000)
                await pi.fill(password)
                await asyncio.sleep(rnd(1, 2))
            except Exception as e:
                log(f"Password fill error: {e}", level="ERROR", progress=prog)
                return False

            try:
                c2  = page.get_by_role("button", name="Continue")
                await c2.wait_for(state="visible", timeout=15000)
                box = await c2.bounding_box()
                if box:
                    await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    await asyncio.sleep(rnd(0.3, 0.7))
                try:
                    await asyncio.gather(
                        page.wait_for_load_state("domcontentloaded"),
                        c2.click(timeout=10000),
                    )
                except Exception:
                    await c2.click(timeout=10000)
                await asyncio.sleep(rnd(2, 3))
            except Exception as e:
                log(f"Continue (password) error: {e}", level="ERROR", progress=prog)
                return False

            log("Waiting for verification email...", progress=prog)
            await asyncio.sleep(8)
            code = await browser_get_otp(email_page, email)
            if not code:
                log(f"No OTP received for {email}", level="ERROR", progress=prog)
                await context.close()
                return False

            try:
                ci = page.get_by_role("textbox", name="Code")
                await ci.wait_for(state="visible", timeout=15000)
                await ci.fill(code)
                await asyncio.sleep(0.5)
            except Exception as e:
                log(f"Code input error: {e}", level="ERROR", progress=prog)
                return False

            try:
                c3 = page.get_by_role("button", name="Continue")
                await c3.click(timeout=10000)
                await asyncio.sleep(3)
            except Exception as e:
                log(f"Continue (code) error: {e}", level="ERROR", progress=prog)
                return False
            try:
                ni = page.get_by_role("textbox", name="Full name")
                await ni.wait_for(state="visible", timeout=10000)
                await ni.fill(name)
                await asyncio.sleep(0.5)
            except Exception as e:
                log(f"Name fill error: {e}", level="ERROR", progress=prog)
                return False
            m, d, y = birthday["month"], birthday["day"], birthday["year"]
            log(f"Setting birthday: {m}/{d}/{y}", progress=prog)
            try:
                await asyncio.sleep(1)
                bday_str = str(m).zfill(2) + str(d).zfill(2) + str(y)
                bday_form = page.locator(
                    "xpath=/html/body/div[1]/div/fieldset/form/div[1]/div/div[2]/div/div/div/div"
                )
                if await bday_form.is_visible(timeout=5000):
                    await bday_form.click()
                    await asyncio.sleep(0.5)
                month_spin = page.locator('[role="spinbutton"][aria-label*="month"]').first
                if await month_spin.is_visible(timeout=5000):
                    await month_spin.click()
                    await asyncio.sleep(0.3)
                    await page.keyboard.type(bday_str, delay=150)
                    await asyncio.sleep(1.5)
                else:
                    raise RuntimeError("Birthday spinbutton not found")
            except Exception as e:
                log(f"Birthday error: {e}", level="ERROR", progress=prog)
                return False
            try:
                final_btn = None
                for label in ["Continue", "Finish", "Done", "Get started", "Agree"]:
                    candidate = page.get_by_role("button", name=label)
                    try:
                        await candidate.wait_for(state="visible", timeout=4000)
                        final_btn = candidate
                        log(f"Found final button: '{label}'", progress=prog)
                        break
                    except Exception:
                        continue
                if final_btn is None:
                    log("Trying any visible button as final step...", level="WARNING", progress=prog)
                    all_buttons = page.get_by_role("button")
                    count = await all_buttons.count()
                    for i in range(count):
                        btn = all_buttons.nth(i)
                        if await btn.is_visible() and await btn.is_enabled():
                            final_btn = btn
                            txt = await btn.inner_text()
                            log(f"Using fallback button: '{txt.strip()}'", progress=prog)
                            break

                if final_btn is None:
                    log("No final button found — assuming signup already completed", level="WARNING", progress=prog)
                else:
                    try:
                        await asyncio.gather(
                            page.wait_for_load_state("domcontentloaded"),
                            final_btn.click(timeout=10000),
                        )
                    except Exception:
                        await final_btn.click(timeout=10000)
                    await asyncio.sleep(3)

            except Exception as e:
                log(f"Final button error: {e}", level="ERROR", progress=prog)
                return False

            if "chatgpt.com" in page.url:
                log("Account created successfully!", progress=prog)
            else:
                log(f"Unexpected URL ({page.url}) — saving anyway", level="WARNING", progress=prog)

            save_account(email, password)
            created.append({"email": email, "password": password})
            await context.close()
            return True

    except Exception as e:
        log(f"Unhandled error: {e}", level="ERROR", progress=prog)
        return False
    finally:
        import shutil
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

def print_summary(ok: int, fail: int, created: list):
    print("\n" + "=" * 60)
    print("ACCOUNT CREATION SUMMARY")
    print("=" * 60)
    print(f"Successful : {ok}")
    print(f"Failed     : {fail}")
    print(f"Total saved: {len(created)}")
    print(f"File       : {ACCOUNTS_FILE}")
    if created:
        print("\nCreated accounts:")
        for i, a in enumerate(created, 1):
            print(f"  {i}. {a['email']}")
    print("=" * 60)


async def run(num: int, config: dict):
    created = []
    ok = fail = 0
    for n in range(1, num + 1):
        try:
            success = await create_account(n, num, config, created)
            if success:
                ok += 1
                log("Done\n", progress=f"{n}/{num}")
            else:
                fail += 1
                log("Failed\n", progress=f"{n}/{num}")
            if n < num:
                await asyncio.sleep(rnd(2, 4))
        except Exception as e:
            log(f"Error: {e}", level="ERROR")
            fail += 1
    print_summary(ok, fail, created)


async def main():
    print("ChatGPT Account Creator (Python 3)")
    print("=" * 60)
    config = load_config()
    print("Config loaded.")
    if not config.get("password"):
        print("ERROR: No 'password' set in config.json. Please add one and re-run.")
        return
    print()
    try:
        n = int(input("How many accounts do you want to create? ").strip())
        if n <= 0:
            print("Please enter a positive number.")
            return
    except (ValueError, EOFError):
        print("Invalid input.")
        return
    print(f"\nCreating {n} account(s)...\n")
    await run(n, config)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Progress saved to accounts.txt")
