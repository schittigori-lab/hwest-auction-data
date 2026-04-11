# coding: utf-8
"""
Harvey West Auctions Scraper
=============================
Scrapes all upcoming auctions from app.hwestauctions.com
Skips "Today's Auction(s)" since it duplicates the dated sections.

OUTPUT: hwest_auctions.json (uploaded to GitHub daily)
RUN:    py hwest_scraper.py
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime

def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    install("requests")
    install("beautifulsoup4")
    import requests
    from bs4 import BeautifulSoup

try:
    from playwright.async_api import async_playwright
except ImportError:
    install("playwright")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    from playwright.async_api import async_playwright

try:
    import pandas as pd
except ImportError:
    install("pandas")
    install("openpyxl")
    import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:
    install("python-dotenv")
    from dotenv import load_dotenv


# ── Config ─────────────────────────────────────────────────────────────────────
load_dotenv()  # reads .env file from same folder

INDEX_URL   = "https://app.hwestauctions.com/index.php"
BASE_URL    = "https://app.hwestauctions.com"
OUTPUT_CSV  = "hwest_auctions.csv"
OUTPUT_XLSX = "hwest_auctions.xlsx"
OUTPUT_JSON = "hwest_auctions.json"

# ── GitHub Config (loaded from .env) ──────────────────────────────────────────
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "schittigori-lab")
GITHUB_REPO     = os.getenv("GITHUB_REPO", "hwest-auction-data")

# ── Email Config ───────────────────────────────────────────────────────────────
EMAIL_SENDER   = "Schittigori@gmail.com"
EMAIL_PASSWORD = "dkirjosfwacpcrzr"
EMAIL_RECEIVER = "Schittigori@gmail.com"


# ── Parse main page HTML ───────────────────────────────────────────────────────
def parse_main_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    auctions = []
    seen = set()

    # Each date section is a div.accordion-item
    for accordion_item in soup.select('div.accordion-item'):

        # Get the date from the button inside the h2 accordion header
        btn = accordion_item.select_one('button.accordion-button')
        if not btn:
            continue
        auction_date = btn.get_text(strip=True)

        # Replace Today label with real date so today's auctions are included
        if "Today" in auction_date:
            auction_date = "Auctions on " + date.today().strftime("%B %d, %Y")

        # Skip non-auction sections
        if "Auctions on" not in auction_date:
            continue

        # Each property is a div.card inside this accordion section
        for card in accordion_item.select('div.card'):
            # Property address from h5.card-title
            title = card.select_one('h5.card-title')
            if not title:
                continue
            address = title.get_text(strip=True)

            # Time and location from p.card-text
            p_tag = card.select_one('p.card-text')
            auction_time     = ''
            auction_location = ''
            if p_tag:
                p_text = p_tag.get_text(strip=True)
                time_m = re.search(r'at\s+(\d+:\d+\s*[AP]M)', p_text, re.IGNORECASE)
                loc_m  = re.search(r'at\s+\d+:\d+\s*[AP]M\s+at\s+(.+)', p_text, re.IGNORECASE)
                if time_m:
                    auction_time = time_m.group(1).strip()
                if loc_m:
                    auction_location = loc_m.group(1).strip()

            # Bid deposit from list-group-item
            bid_deposit = ''
            for li in card.select('li.list-group-item'):
                li_text = li.get_text(strip=True)
                if li_text.startswith('Bid Deposit:'):
                    bid_deposit = li_text.replace('Bid Deposit:', '').strip()

            # Detail URL from card-link
            detail_url = ''
            link = card.select_one('a.card-link')
            if link:
                href = link.get('href', '')
                if href:
                    detail_url = BASE_URL + '/' + href.lstrip('/')

            if detail_url and detail_url not in seen:
                seen.add(detail_url)
                auctions.append({
                    'auction_date':     auction_date,
                    'property_address': address,
                    'auction_time':     auction_time,
                    'auction_location': auction_location,
                    'bid_deposit':      bid_deposit,
                    'detail_url':       detail_url,
                })

    return auctions


# ── Scrape detail page ─────────────────────────────────────────────────────────
async def scrape_detail(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(600)
        text = await page.inner_text("body")

        # Principal balance
        principal = ''
        m = re.search(r'original principal amount of\s*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        if m:
            principal = '$' + m.group(1)

        # Phone number (last one is usually the firm's contact)
        phone = ''
        phones = re.findall(r'\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}', text)
        if phones:
            phone = phones[-1].strip()

        # Substitute Trustee name
        trustee = ''
        tm = re.search(
            r'([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:,?\s+et al\.?)?),?\s+Substitute Trustees?',
            text
        )
        if tm:
            trustee = tm.group(1).strip()

        return {'principal_balance': principal, 'substitute_trustee': trustee, 'trustee_phone': phone}
    except Exception:
        return {'principal_balance': '', 'substitute_trustee': '', 'trustee_phone': ''}


# ── Save results ───────────────────────────────────────────────────────────────
def save_results(auctions):
    if not auctions:
        print("\n No auction data to save.")
        return

    df = pd.DataFrame(auctions)
    if 'detail_url' in df.columns:
        df = df.drop(columns=['detail_url'])

    df = df.rename(columns={
        'auction_date':       'Auction Date',
        'property_address':   'Property Address',
        'auction_time':       'Time',
        'auction_location':   'Auction Location',
        'bid_deposit':        'Bid Deposit',
        'principal_balance':  'Principal Balance',
        'substitute_trustee': 'Substitute Trustee',
        'trustee_phone':      'Trustee Phone',
    })

    col_order = ['Auction Date', 'Property Address', 'Time', 'Auction Location',
                 'Bid Deposit', 'Principal Balance', 'Substitute Trustee', 'Trustee Phone']
    df = df[[c for c in col_order if c in df.columns]]

    # ── Save JSON for mobile app ──────────────────────────────────────────────
    json_records = []
    for auction in auctions:
        json_records.append({
            "auction_date":       auction.get("auction_date", ""),
            "property_address":   auction.get("property_address", ""),
            "auction_time":       auction.get("auction_time", ""),
            "auction_location":   auction.get("auction_location", ""),
            "bid_deposit":        auction.get("bid_deposit", ""),
            "principal_balance":  auction.get("principal_balance", ""),
            "substitute_trustee": auction.get("substitute_trustee", ""),
            "trustee_phone":      auction.get("trustee_phone", ""),
            "detail_url":         auction.get("detail_url", ""),
        })
    json_output = {
        "last_updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_auctions": len(json_records),
        "auctions": json_records,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as jf:
        json.dump(json_output, jf, indent=2, ensure_ascii=False)
    print(f"\n JSON saved:  {OUTPUT_JSON}  ({len(json_records)} auctions)")

    # df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    # print(f"\n CSV saved:   {OUTPUT_CSV}  ({len(df)} auctions)")

    # with pd.ExcelWriter(OUTPUT_XLSX, engine='openpyxl') as writer:
    #     df.to_excel(writer, index=False, sheet_name='HWest Auctions')
    #     ws = writer.sheets['HWest Auctions']
    #     from openpyxl.styles import Font, PatternFill
    #     for col in ws.columns:
    #         ws.column_dimensions[col[0].column_letter].width = min(
    #             max(len(str(c.value or '')) for c in col) + 4, 60)
    #     for cell in ws[1]:
    #         cell.font = Font(bold=True, color="FFFFFF")
    #         cell.fill = PatternFill("solid", fgColor="1F4E79")

    # print(f" Excel saved: {OUTPUT_XLSX}")
    # print(f"\n Preview (first 5 rows):\n")
    # print(df.head(5).to_string(index=False))


# ── Send email ─────────────────────────────────────────────────────────────────
def send_email(csv_path):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    if "your_gmail" in EMAIL_SENDER:
        print("\n Email skipped — update EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER in the script.")
        return

    print("\n Sending email...")
    msg = MIMEMultipart()
    msg['From']    = EMAIL_SENDER
    msg['To']      = EMAIL_RECEIVER
    msg['Subject'] = f"Harvey West Auctions - Daily Report {date.today().strftime('%m/%d/%Y')}"
    msg.attach(MIMEText(f"""Good morning,

Please find attached the Harvey West Auctions report for {date.today().strftime('%B %d, %Y')}.

Includes: Property Address, Auction Date/Time, Auction Location,
Bid Deposit, Principal Balance, Substitute Trustee and Phone.

Source: https://app.hwestauctions.com
-- Automated Daily Report
""", 'plain'))

    with open(csv_path, 'rb') as f:
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition',
            f'attachment; filename="hwest_auctions_{date.today().strftime("%Y%m%d")}.csv"')
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f" Email sent to {EMAIL_RECEIVER}!")
    except Exception as e:
        print(f" Email failed: {e}")



# ── Upload JSON to GitHub ──────────────────────────────────────────────────────
def upload_to_github(json_path):
    if not GITHUB_TOKEN:
        print("\n GitHub upload skipped — GITHUB_TOKEN not found in .env file.")
        return

    print("\n Uploading JSON to GitHub...")
    api_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{OUTPUT_JSON}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    with open(json_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    sha = None
    check = requests.get(api_url, headers=headers)
    if check.status_code == 200:
        sha = check.json().get("sha")

    payload = {
        "message": f"Daily auction update {date.today().strftime('%Y-%m-%d')}",
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload)
    if resp.status_code in (200, 201):
        live_url = f"https://{GITHUB_USERNAME}.github.io/{GITHUB_REPO}/{OUTPUT_JSON}"
        print(f" GitHub upload successful!")
        print(f" Live URL: {live_url}")
    else:
        print(f" GitHub upload failed: {resp.status_code} — {resp.text[:200]}")


# ── Main ───────────────────────────────────────────────────────────────────────
async def main():
    # Fetch full HTML directly
    print(" Fetching auction schedule...")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
    resp = requests.get(INDEX_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    print(f" Page downloaded successfully")

    # Parse all auctions
    auctions = parse_main_page(resp.text)
    print(f" Found {len(auctions)} auctions across all dates")

    if not auctions:
        print(" No auctions parsed! Saving debug file...")
        with open("hwest_debug.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        return

    # Fetch details for each auction
    print(f"\n Fetching details for {len(auctions)} auctions...\n")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=headers['User-Agent'])
        page    = await context.new_page()

        for i, auction in enumerate(auctions):
            url  = auction.get('detail_url', '')
            addr = auction.get('property_address', '')[:45]
            print(f"  [{i+1}/{len(auctions)}] {addr}...")
            if url:
                details = await scrape_detail(page, url)
                auction.update(details)
            else:
                auction.update({'principal_balance': '', 'substitute_trustee': '', 'trustee_phone': ''})
            await asyncio.sleep(0.3)

        await browser.close()

    save_results(auctions)
    # send_email(OUTPUT_CSV)
    upload_to_github(OUTPUT_JSON)


if __name__ == "__main__":
    print("=" * 60)
    print("  Harvey West Auctions Scraper")
    print("  Source: app.hwestauctions.com")
    print("=" * 60)
    asyncio.run(main())
    print("\n Done!")
    print(f"   {OUTPUT_JSON}")
