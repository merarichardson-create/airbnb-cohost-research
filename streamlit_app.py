import asyncio
import nest_asyncio
import re
import pandas as pd
import streamlit as st
from playwright.async_api import async_playwright
from geopy.geocoders import Nominatim

nest_asyncio.apply()

st.set_page_config(page_title="Airbnb Cohost Research", layout="wide")

st.title("🏠 Airbnb Cohost Research")
st.write("Scan Airbnb host profiles to find owner-operated listings")

geolocator = Nominatim(user_agent="bpo_fresh_recon_2026")

async def scan_leads(urls):
    results = []
    keywords = ["my home", "our home", "owner", "we own", "my villa", "my business", "local", "own and operate"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(user_agent="Mozilla/5.0")
        page = await context.new_page()
        for url in urls:
            if not url.strip(): 
                continue
            st.write(f"👤 Checking: {url}")
            try:
                await page.goto(url.strip(), wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5)
                content = await page.content()
                if not any(w in content.lower() for w in keywords):
                    st.write(f"  ⏩ Skipping: No owner bio found.")
                    continue
                room_ids = list(set(re.findall(r'/rooms/(\d+)', content)))
                for rid in room_ids[:3]:
                    room_url = f"https://www.airbnb.com/rooms/{rid}"
                    await page.goto(room_url, wait_until="domcontentloaded")
                    await asyncio.sleep(3)
                    r_content = await page.content()
                    lat = re.search(r'"lat":([-+]?\d*\.\d+|\d+)', r_content)
                    lng = re.search(r'"lng":([-+]?\d*\.\d+|\d+)', r_content)
                    address, maps_url = "Vicinity Only", "N/A"
                    if lat and lng:
                        lt, lg = lat.group(1), lng.group(1)
                        maps_url = f"https://www.google.com/maps?q={lt},{lg}"
                        try:
                            loc = geolocator.reverse(f"{lt}, {lg}", exactly_one=True, addressdetails=True)
                            if loc:
                                raw = loc.raw.get('address', {})
                                h_num, road = raw.get('house_number', ''), raw.get('road', '')
                                address = f"{h_num} {road}".strip() if h_num and road else loc.address
                        except:
                            pass
                    results.append({"Host": url.split('/')[-1], "Address": address, "Maps": maps_url, "Link": room_url})
            except Exception as e: 
                st.write(f"  ⚠️ Error: {e}")
        await browser.close()
    return pd.DataFrame(results)

# Main UI
st.markdown("---")

col1, col2 = st.columns([3, 1])
with col1:
    text_input = st.text_area(
        "Enter Airbnb host profile URLs (one per line):",
        placeholder="https://www.airbnb.com/users/show/...\nhttps://www.airbnb.com/users/show/...",
        height=150
    )

with col2:
    scan_button = st.button("🔍 Scan Hosts", use_container_width=True)

if scan_button and text_input.strip():
    urls = [url.strip() for url in text_input.split('\n') if url.strip()]
    
    with st.spinner("Scanning hosts..."):
        try:
            df = asyncio.run(scan_leads(urls))
            
            if not df.empty:
                st.success(f"✅ Found {len(df)} listings!")
                st.dataframe(df, use_container_width=True)
                
                # Download option
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download Results (CSV)",
                    data=csv,
                    file_name="airbnb_results.csv",
                    mime="text/csv"
                )
            else:
                st.info("No owner-operated listings found in the scanned profiles.")
        except Exception as e:
            st.error(f"Error during scan: {e}")
elif scan_button:
    st.warning("Please enter at least one URL.")
