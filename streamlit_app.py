import asyncio
import nest_asyncio
import re
import pandas as pd
import streamlit as st
from playwright.async_api import async_playwright
from geopy.geocoders import Nominatim
from urllib.parse import quote
import json

nest_asyncio.apply()

st.set_page_config(page_title="Airbnb Cohost Research", layout="wide")

st.title("🏠 Airbnb Cohost Research")
st.write("Scan Airbnb host profiles to find owner-operated properties and their business information")

geolocator = Nominatim(user_agent="bpo_fresh_recon_2026")

OWNER_KEYWORDS = [
    "my home", "our home", "owner", "we own", "my villa", "my business", 
    "local", "own and operate", "owner-operated", "locally owned", "family-owned",
    "manager", "managing", "property management", "pm company", "real estate",
    "llc", "ltd", "corp", "inc.", "business", "entrepreneur", "operator"
]

COMPANY_KEYWORDS = [
    "property management", "pm company", "management group", "hospitality",
    "vacation rental", "short-term rental", "airbnb management", "llc", "corp",
    "real estate", "realty", "properties", "investment group", "portfolio"
]

async def extract_host_info(url, page):
    """Extract host name, bio and other profile info"""
    try:
        await page.goto(url.strip(), wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        content = await page.content()
        
        # Extract host name
        host_name_match = re.search(r'"displayName":"([^"]+)"', content)
        host_name = host_name_match.group(1) if host_name_match else url.split('/')[-1]
        
        # Extract bio
        bio_match = re.search(r'"bio":"([^"]*)"', content)
        bio = bio_match.group(1) if bio_match else ""
        
        # Extract location info
        city_match = re.search(r'"city":"([^"]+)"', content)
        city = city_match.group(1) if city_match else ""
        
        return {
            "host_name": host_name,
            "bio": bio,
            "city": city,
            "profile_url": url
        }
    except Exception as e:
        st.write(f"  ⚠️ Error extracting host info: {e}")
        return None

def detect_business_type(bio, host_name):
    """Detect if host operates a business"""
    combined_text = f"{bio} {host_name}".lower()
    
    is_property_manager = any(kw in combined_text for kw in COMPANY_KEYWORDS)
    is_owner_operator = any(kw in combined_text for kw in OWNER_KEYWORDS)
    
    return is_property_manager, is_owner_operator

async def search_business_info(host_name, city, page):
    """Use Google search to find business information"""
    results = {
        "business_url": "",
        "linkedin": "",
        "facebook": "",
        "llc_info": "",
        "email": "",
        "phone": ""
    }
    
    try:
        # Search for business website and LLC info
        search_query = f"{host_name} {city} property management LLC business"
        google_url = f"https://www.google.com/search?q={quote(search_query)}"
        
        await page.goto(google_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        search_content = await page.content()
        
        # Extract URLs from search results
        urls = re.findall(r'href="(https?://[^"]+)"', search_content)
        
        for url in urls[:10]:  # Check first 10 results
            if "linkedin.com" in url.lower() and not results["linkedin"]:
                results["linkedin"] = url
            elif "facebook.com" in url.lower() and not results["facebook"]:
                results["facebook"] = url
            elif any(x in url.lower() for x in ["about.com", "yelp.com", "yellowpages", ".com"]) and not results["business_url"]:
                if "google.com" not in url and "facebook.com" not in url and "linkedin.com" not in url:
                    results["business_url"] = url
        
        # Search specifically for LLC info
        llc_query = f"{host_name} LLC {city}"
        llc_url = f"https://www.google.com/search?q={quote(llc_query)}"
        await page.goto(llc_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        llc_content = await page.content()
        
        if "llc" in llc_content.lower():
            results["llc_info"] = "Found LLC records"
        
        # Try to find contact info by searching website directly
        if results["business_url"]:
            try:
                await page.goto(results["business_url"], wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)
                ws_content = await page.content()
                
                # Extract email
                email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', ws_content)
                if email_match:
                    results["email"] = email_match.group(0)
                
                # Extract phone
                phone_match = re.search(r'\+?1?\s*[\(\-\s]?\d{3}[\)\-\s]?\d{3}[\-\s]?\d{4}', ws_content)
                if phone_match:
                    results["phone"] = phone_match.group(0)
            except:
                pass
        
    except Exception as e:
        st.write(f"  ⚠️ Error searching business info: {e}")
    
    return results

async def scan_leads(urls):
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(user_agent="Mozilla/5.0")
        page = await context.new_page()
        
        for url in urls:
            if not url.strip(): 
                continue
            
            st.write(f"👤 Analyzing: {url}")
            
            try:
                # Extract host profile info
                host_info = await extract_host_info(url, page)
                if not host_info:
                    continue
                
                # Detect business type
                is_pm, is_owner = detect_business_type(host_info["bio"], host_info["host_name"])
                
                if not (is_pm or is_owner):
                    st.write(f"  ⏩ Skipping: No business indicators found.")
                    continue
                
                st.write(f"  ✓ Host: {host_info['host_name']}")
                st.write(f"  📍 Location: {host_info['city']}")
                
                # Search for business information
                with st.spinner(f"  🔍 Searching for business info..."):
                    biz_info = await search_business_info(host_info["host_name"], host_info["city"], page)
                
                # Extract room/property info
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(3)
                content = await page.content()
                room_ids = list(set(re.findall(r'/rooms/(\d+)', content)))
                
                for rid in room_ids[:2]:  # Get first 2 properties
                    try:
                        room_url = f"https://www.airbnb.com/rooms/{rid}"
                        await page.goto(room_url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(2)
                        r_content = await page.content()
                        
                        # Extract coordinates
                        lat = re.search(r'"lat":([-+]?\d*\.\d+|\d+)', r_content)
                        lng = re.search(r'"lng":([-+]?\d*\.\d+|\d+)', r_content)
                        address = "N/A"
                        
                        if lat and lng:
                            lt, lg = lat.group(1), lng.group(1)
                            try:
                                loc = geolocator.reverse(f"{lt}, {lg}", exactly_one=True, addressdetails=True)
                                if loc:
                                    raw = loc.raw.get('address', {})
                                    h_num, road = raw.get('house_number', ''), raw.get('road', '')
                                    address = f"{h_num} {road}".strip() if h_num and road else loc.address
                            except:
                                pass
                        
                        results.append({
                            "Host Name": host_info["host_name"],
                            "City": host_info["city"],
                            "Profile URL": url,
                            "Property Address": address,
                            "Property Link": room_url,
                            "Business Type": "Property Manager" if is_pm else "Owner-Operator",
                            "Business Website": biz_info.get("business_url", ""),
                            "LinkedIn": biz_info.get("linkedin", ""),
                            "Facebook": biz_info.get("facebook", ""),
                            "LLC Info": biz_info.get("llc_info", ""),
                            "Email": biz_info.get("email", ""),
                            "Phone": biz_info.get("phone", ""),
                            "Bio": host_info["bio"][:100] + "..." if len(host_info["bio"]) > 100 else host_info["bio"]
                        })
                        st.write(f"    📌 Added property: {address}")
                    except Exception as e:
                        st.write(f"    ⚠️ Error processing property {rid}: {e}")
                
            except Exception as e: 
                st.write(f"  ⚠️ Error: {e}")
        
        await browser.close()
    
    return pd.DataFrame(results)

# Main UI
st.markdown("---")

col1, col2 = st.columns([3, 1])
with col1:
    text_input = st.text_area(
        "Enter Cohost/Airbnb host profile URLs (one per line):",
        placeholder="https://www.airbnb.com/users/show/...\nhttps://www.airbnb.com/users/show/...",
        height=150
    )

with col2:
    scan_button = st.button("🔍 Scan & Research", use_container_width=True)

if scan_button and text_input.strip():
    urls = [url.strip() for url in text_input.split('\n') if url.strip()]
    
    progress_placeholder = st.empty()
    
    with st.spinner("Scanning hosts and researching business information..."):
        try:
            df = asyncio.run(scan_leads(urls))
            
            if not df.empty:
                st.success(f"✅ Found {len(df)} leads!")
                st.dataframe(df, use_container_width=True)
                
                # Download option
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download Contact Spreadsheet (CSV)",
                    data=csv,
                    file_name="airbnb_business_leads.csv",
                    mime="text/csv"
                )
            else:
                st.info("No qualified leads found.")
        except Exception as e:
            st.error(f"Error during scan: {e}")
elif scan_button:
    st.warning("Please enter at least one URL.")
