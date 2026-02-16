import asyncio
import re
from urllib.parse import quote

import pandas as pd
import nest_asyncio
from geopy.geocoders import Nominatim
from playwright.async_api import async_playwright

nest_asyncio.apply()

geolocator = Nominatim(user_agent="bpo_fresh_recon_2026")

OWNER_KEYWORDS = [
    "my home", "our home", "owner", "we own", "my villa", "my business",
    "local", "own and operate", "owner-operated", "locally owned", "family-owned",
    "manager", "managing", "property management", "pm company",
    "real estate", "llc", "ltd", "corp", "inc.", "business", "operator"
]

COMPANY_KEYWORDS = [
    "property management", "management group", "hospitality",
    "vacation rental", "short-term rental", "airbnb management", "llc",
    "real estate", "realty", "properties"
]


async def extract_host_info(page, url: str):
    try:
        await page.goto(url.strip(), wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)
        content = await page.content()

        host_name_match = re.search(r'"displayName":"([^"]+)"', content)
        host_name = host_name_match.group(1) if host_name_match else url.split('/')[-1]

        bio_match = re.search(r'"bio":"([^"]*)"', content)
        bio = bio_match.group(1) if bio_match else ""

        city_match = re.search(r'"city":"([^"]+)"', content)
        city = city_match.group(1) if city_match else ""

        return {"host_name": host_name, "bio": bio, "city": city, "profile_url": url}
    except Exception:
        return None


def detect_business_type(bio: str, host_name: str):
    combined = f"{bio} {host_name}".lower()
    is_pm = any(kw in combined for kw in COMPANY_KEYWORDS)
    is_owner = any(kw in combined for kw in OWNER_KEYWORDS)
    return is_pm, is_owner


async def search_business_info(host_name: str, city: str, page):
    results = {"business_url": "", "linkedin": "", "facebook": "", "llc_info": "", "email": "", "phone": ""}
    try:
        query = f"{host_name} {city} property management business"
        url = f"https://www.google.com/search?q={quote(query)}"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.5)
        content = await page.content()

        urls = re.findall(r'href="(https?://[^"]+)"', content)
        for u in urls[:12]:
            lu = u.lower()
            if "linkedin.com" in lu and not results["linkedin"]:
                results["linkedin"] = u
            elif "facebook.com" in lu and not results["facebook"]:
                results["facebook"] = u
            elif not any(x in lu for x in ("google.com", "facebook.com", "linkedin.com")) and not results["business_url"]:
                results["business_url"] = u

        # try llc search
        llc_q = f"{host_name} llc {city}"
        await page.goto(f"https://www.google.com/search?q={quote(llc_q)}", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1)
        llc_content = await page.content()
        if "llc" in llc_content.lower():
            results["llc_info"] = "LLC references found"

        # if we found a business site, try to extract contact details
        if results["business_url"]:
            try:
                await page.goto(results["business_url"], wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1)
                ws = await page.content()
                em = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', ws)
                if em:
                    results["email"] = em.group(0)
                ph = re.search(r'\+?1?\s*[\(\-\s]?\d{3}[\)\-\s]?\d{3}[\-\s]?\d{4}', ws)
                if ph:
                    results["phone"] = ph.group(0)
            except Exception:
                pass

    except Exception:
        pass
    return results


async def scan_leads(urls):
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(user_agent="Mozilla/5.0")
        page = await context.new_page()

        for url in urls:
            if not url or not url.strip():
                continue
            try:
                host_info = await extract_host_info(page, url)
                if not host_info:
                    continue

                is_pm, is_owner = detect_business_type(host_info.get("bio", ""), host_info.get("host_name", ""))
                if not (is_pm or is_owner):
                    continue

                biz = await search_business_info(host_info["host_name"], host_info["city"], page)

                # extract properties from profile
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2)
                content = await page.content()
                room_ids = list(set(re.findall(r'/rooms/(\d+)', content)))

                for rid in room_ids[:2]:
                    try:
                        room_url = f"https://www.airbnb.com/rooms/{rid}"
                        await page.goto(room_url, wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(1)
                        rcontent = await page.content()
                        lat = re.search(r'"lat":([-+]?\d*\.\d+|\d+)', rcontent)
                        lng = re.search(r'"lng":([-+]?\d*\.\d+|\d+)', rcontent)
                        address = ""
                        if lat and lng:
                            try:
                                loc = geolocator.reverse(f"{lat.group(1)}, {lng.group(1)}", exactly_one=True, addressdetails=True)
                                if loc:
                                    raw = loc.raw.get('address', {})
                                    h_num, road = raw.get('house_number', ''), raw.get('road', '')
                                    address = f"{h_num} {road}".strip() if h_num and road else loc.address
                            except Exception:
                                address = "Vicinity Only"

                        results.append({
                            "Host Name": host_info["host_name"],
                            "City": host_info["city"],
                            "Profile URL": url,
                            "Property Address": address,
                            "Property Link": room_url,
                            "Business Type": "Property Manager" if is_pm else "Owner-Operator",
                            "Business Website": biz.get("business_url", ""),
                            "LinkedIn": biz.get("linkedin", ""),
                            "Facebook": biz.get("facebook", ""),
                            "LLC Info": biz.get("llc_info", ""),
                            "Email": biz.get("email", ""),
                            "Phone": biz.get("phone", ""),
                            "Bio": host_info.get("bio", "")[:200]
                        })
                    except Exception:
                        continue

        await browser.close()

    return pd.DataFrame(results)
