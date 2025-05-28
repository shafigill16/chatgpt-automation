import logging
import os
import random
import time
import tkinter as tk

from camoufox.sync_api import Camoufox
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
load_dotenv()
# Config from environment
EMAIL = os.getenv("CHAT_EMAIL")
PASSWORD = os.getenv("CHAT_PASSWORD")
USER_DATA_DIR = os.getenv("CAMOUFOX_PROFILE_DIR", "user-data-dirlll")

print("EMAIL:", EMAIL)       # should print your email, not None
print("PASSWORD:", PASSWORD) # should print your full password
# Selectors & timeouts
SELECTORS = {
    "login_btn": ("button:has-text('Log in')", 20000),
    "email_input": ('input[type="email"]', 20000),
    "password_in": ('input[type="password"]', 20000),
    "msg_input": ('div.ProseMirror[contenteditable="true"]', 25000),
    "responses": ("div.markdown.prose", 25000),
    "chat_items": ("ol li[data-testid^='history-item']", 20000),
}


def retry(action, retries=3, base=0.5):
    for i in range(1, retries + 1):
        try:
            return action()
        except Exception:
            if i == retries:
                raise
            time.sleep(base * 2 ** (i - 1) + random.random() * 0.1)


def get_screen_size():
    root = tk.Tk()
    root.withdraw()
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.destroy()
    return w, h


def build_config():
    width, height = get_screen_size()
    return {
        'window.outerHeight': height,
        'window.outerWidth': width,
        'window.innerHeight': height,
        'window.innerWidth': width,
        'window.history.length': 4,
        'navigator.userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
        'navigator.appCodeName': 'Mozilla',
        'navigator.appName': 'Netscape',
        'navigator.appVersion': '5.0 (Windows)',
        'navigator.oscpu': 'Windows NT 10.0; Win64; x64',
        'navigator.language': 'en-US',
        'navigator.languages': ['en-US'],
        'navigator.platform': 'Win32',
        'navigator.hardwareConcurrency': 12,
        'navigator.product': 'Gecko',
        'navigator.productSub': '20030107',
        'navigator.maxTouchPoints': 10,

    }


def setup_browser():
    return Camoufox(
        headless=False,
        persistent_context=True,
        user_data_dir=USER_DATA_DIR,
        os='windows',
        config=build_config(),
        i_know_what_im_doing=True,
    )


def wait_sel(page, key):
    sel, to = SELECTORS[key]
    return page.wait_for_selector(sel, timeout=to)


def login_if_needed(page):
    LOGIN_URL = "https://auth.openai.com/log-in"

    # 1) Detect if we’re already signed in
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
        buttons = page.query_selector_all('button:has-text("Log in")')
        if not buttons:
            logging.info("No login button found—already signed in.")
            return
    except Exception:
        logging.warning("Error detecting login state—will navigate to login URL.")
        page.goto(LOGIN_URL, timeout=60000, wait_until="networkidle")
        # fall through to credentials step

    # 2) Try clicking every “Log in” button until one works
    logging.info(f"Found {len(buttons)} login button(s), attempting clicks…")
    clicked = False
    for idx, btn in enumerate(buttons, start=1):
        try:
            btn.click()
            logging.info(f"✅ Clicked login button #{idx}")
            clicked = True
            break
        except Exception as e:
            logging.warning(f"❌ Button #{idx} click failed: {e}")

    # 3) If none of the buttons clicked successfully, go direct
    if not clicked:
        logging.info("All button clicks failed—navigating directly to login URL.")
        page.goto(LOGIN_URL, timeout=60000, wait_until="networkidle")

    # 4) Perform the email/password flow
    logging.info("Performing email/password login flow.")
    retry(lambda: wait_sel(page, "email_input").fill(EMAIL))
    retry(lambda: page.click('button:has-text("Continue")'))
    retry(lambda: wait_sel(page, "password_in").fill(PASSWORD))
    retry(lambda: page.click('button:has-text("Continue")'))

    # 5) Wait for the post-login UI to settle
    page.wait_for_load_state("networkidle", timeout=20000)
    logging.info("Login flow complete.")


# def login_if_needed(page):
#     try:
#         time.sleep(100)
#         # give the page a moment to settle
#         page.wait_for_load_state("networkidle", timeout=20000)
#
#         # see if the “Log in” button is there
#         login_buttons = page.query_selector_all('button:has-text("Log in")')
#         if not login_buttons:
#             logging.info("No login button found—already signed in.")
#             return
#     except:
#         pass
#     #  login page : https://auth.openai.com/log-in
#     logging.info("Logging in…")
#     wait_sel(page, "login_btn").click()
#     retry(lambda: wait_sel(page, "email_input").fill(EMAIL))
#     retry(lambda: page.click('button:has-text("Continue")'))
#     retry(lambda: wait_sel(page, "password_in").fill(PASSWORD))
#     retry(lambda: page.click('button:has-text("Continue")'))


def select_chat(page, name):
    sel, timeout = SELECTORS["chat_items"]
    # wait for the chat list to be present
    page.wait_for_selector(sel, timeout=timeout)

    # try clicking the chat title directly
    title_selector = f"a div[title=\"{name}\"]"
    try:
        page.click(title_selector, timeout=5000)
        logging.info(f"Selected chat '{name}'")
        # now wait for the message input in that chat
        msg_sel, msg_to = SELECTORS["msg_input"]
        page.wait_for_selector(msg_sel, timeout=msg_to)
        return True
    except Exception:
        logging.warning(f"Chat '{name}' not found or click failed")
        return False


def send_message_and_wait(page, text):
    msg_sel, timeout = SELECTORS["msg_input"]

    # ensure the input is visible and ready
    page.wait_for_selector(msg_sel, timeout=timeout)

    # click → fill → enter, all via fresh queries
    page.click(msg_sel)
    page.fill(msg_sel, text)
    page.press(msg_sel, "Enter")
    logging.info("Prompt entered.")
    wait_for_response_completion(page)


def wait_for_response_completion(page, timeout=200000):
    """
    1) Wait for ChatGPT to start streaming (the “stop” button appears)
    2) Then wait for streaming to finish, which we detect when the
       send‐prompt button (data-testid="send-button") re‐appears.
    """
    logging.info("Waiting for response")
    # 1) wait for the Stop‐streaming button
    page.wait_for_selector('button[data-testid="stop-button"]', timeout=timeout)

    # 2) now wait for it to be replaced by the send‐prompt button
    page.wait_for_selector('button[data-testid="send-button"]', timeout=timeout)
    logging.info("Response wait over.")


def get_latest_response(page):
    wait_sel(page, "responses")
    blocks = page.query_selector_all(SELECTORS["responses"][0])
    if not blocks:
        raise RuntimeError("No responses found")
    return blocks[-1].inner_text()


def send_prompt_get_response(page, prompt):
    send_message_and_wait(page, prompt)
    logging.info("Waiting for reply…")
    page.wait_for_timeout(5000)
    print(get_latest_response(page))
    # time.sleep(20)


def main(prompt_message):
    with setup_browser() as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # Option B: go straight to networkidle
        # page.goto("https://chat.openai.com", timeout=60000, wait_until="networkidle")
        # Option A: wait only for DOMContentLoaded, then wait for networkidle
        page.goto("https://chat.openai.com", timeout=60000, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=60000)

        login_if_needed(page)

        send_prompt_get_response(page, prompt_message)

        # time.sleep(20)


if __name__ == "__main__":
    try:
        prompt = """[SYSTEM]
        You are an expert proposal writer (senior conversion copywriter) who crafts **short, high‑conversion Upwork proposals** for web‑scraping & data‑engineering projects.

        [CONTEXT]
        MY_BIO:
        """"""
        Most scrapers break when websites change or block bots — I build the ones that don’t. Clean pipelines, scalable infra, and anti-bot precision for data at scale.
        Delivering enterprise-grade scraping and data pipelines for the eCommerce, automotive, and AI sectors. Specialized in anti-bot solutions, multi-server infra, and OpenSearch indexing with concurrency, offset tracking, and zero data loss.
        I’ve built scraping platforms with containerized AWS ECS/Kubernetes-based auto-scaled fleet of cloud servers for the client, achieving 10M+ product listings per week with robust IP rotation, JSON validation and OpenSearch or S3 ingestion.
        With 8+ years of experience in Python and cloud-based data infrastructure, I’ve helped clients build web scraping engines—optimized for speed, scale, and reliability.
        What I Build for You
        * Scalable Scraping Engines — Playwright-based automation with proxy rotation, anti-CAPTCHA, and request interception
        * Offset-Resumable ETL Pipelines — Async retry logic, Redis progress tracking, fault-tolerant batching
        * Clean JSON Output Pipelines — Mongo → JSONL → OpenSearch/S3 integration
        * Product Aggregators — Automotive, eCom, marketplace data enrichment
        * Data Cleaning + Validation — Field sanitization before indexing, real-time log feedback, SQS-compatible outputs
        * Multi-server Deployments — Dockerized scrapers auto-synced across 50+ VMs using GitHub Actions + rsync
        * ASIN Pipelines — Keepa API batching, file tracking, AWS-ready JSONL drops​

        Recent Use Cases
        * Scraped 10M+ products weekly, 96% uptime, robust IP rotation, JSONL → OpenSearch.
        * Keepa ASIN Pipeline: Batching thousands of Amazon ASINs with rate-limit logic, generating JSONL for AWS S3 & analytics.
        * Scalable Orchestration of Containerized Scrapers: Used AWS ECS, Lambda, Elasticache, Step Functions, and CloudWatch to build a fully auto-scaling scraping system with complete observability.
        * Retail GenAI Data Feeds: Combined scraping + vector embeddings for a ChatGPT-like assistant on eCom product data.

        Tech Stack
        Languages: Python 3.11+ (Asyncio), Node.js (for select tasks)
        Scraping Tools: request, nodriver, curl cffi, Playwright, Puppeteer, BeautifulSoup, lxml, mitmproxy
        Anti-Bot: Proxy rotation, VPN in Docker, custom headers, user-agents, CAPTCHAs
        Databases: MongoDB, Redis, PostgreSQL, OpenSearch, S3, AWS Glue
        Pipelines: JSONL outputs, batch ETL, offset-based resume, concurrency semaphores
        Infra: Docker, GitHub Actions, AWS EC2, Hetzner, SQS, Portainer

        ✅ Why Clients Hire Me
        Scalable Orchestration of Containerized Scrapers: Built a fully automated, observable scraping system using AWS ECS, Lambda, Elasticache, Step Functions, and CloudWatch — enabling seamless autoscaling, fault tolerance, and real-time monitoring across millions of items scraped weekly.
        Resilient & Restartable: Async logic + offsets + logging for continuous operation
        Clean, Validated Outputs: JSON schema checks, brand/price sanity checks, zero duplicates
        Production Mindset: Automated deployments, real logs, concurrency control, and quick iteration
        Expert in AI-Ready Data: Perfect for GenAI training sets, advanced analytics, or real-time eCom dashboards
        """"""

        [JOB_POST]
        TITLE: Script Optimization for Price Scraping Automation

        DESCRIPTION:
        """"""
        We are seeking an experienced developer to optimize multiple scripts used for price scraping. The ideal candidate will enhance the scripts to ensure they run automatically and provide comprehensive reports on the scraping results. Familiarity with web scraping techniques and optimization best practices is essential. If you have a proven track record in this area and can deliver efficient, reliable scripts, we want to hear from you!
        """"""

        [TASK]
        Write a **250‑500‑word proposal** that:

        1. **Power‑hook (first 2 sentences, ≤ 40 words total).**  
           • Sentence 1 — Opens with a 1–2‑line, ultra‑specific hook (≤ 25 words) reflecting the client’s tone and the scale of the task.  
           • Sentence 2 — inject **2‑3 credibility stats** (e.g., “containerized AWS ECS/Kubernetes based auto scaled fleet, 10 M pages/week, 50+ sites scraped”).  

        2. Restates the problem in their own words and lays out a concise 3‑5‑step plan, and for webscraping tasks, unless the job specifically specifies browser-based scraping, suggests non-browser-based solutions (“Here’s the Plan”).  

        3. Backs up your fit with **one or two** bio‑based wins *directly relevant* to the job, scraping at scale (“Past Wins”).  

        4. Promises risk‑free, low‑friction delivery (“What You Get”).  
        5. **Next Steps.**  
           - Invite them to a quick call (“Let’s jump on a quick call— I’ll outline exactly how we run scrapers, prevent throttling, and structure the data.”)
           - Close with a friendly, low‑pressure sign‑off.

        **Rules**

        - Use natural sub‑headings like “Hi there!” or “Here’s the Plan.”  
        - Never include personal contact info or literal labels like “CTA.”  
        - Match the client’s tone (casual, formal, excited) and balance tech credibility with friendly reassurance.  
        - Output **plain text only**—no markdown, no code blocks, no extra commentary.
        """
        main(prompt)
    except Exception:
        logging.exception("Automation failed—see screenshot.")
