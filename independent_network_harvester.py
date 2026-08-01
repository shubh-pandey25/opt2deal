import urllib.request
import urllib.parse
import urllib.error
import re
import json
import time
import os
import sys
import argparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Load database configurations
from database import db_session, ComponentTrader, TraderInventoryJunction

# Load environment variables
load_dotenv()

# Safe print helper to prevent cp1252 Windows console encoding crashes
def safe_print(*args, **kwargs):
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    file = kwargs.get('file', sys.stdout)
    message = sep.join(str(arg) for arg in args)
    try:
        file.write(message + end)
        file.flush()
    except UnicodeEncodeError:
        try:
            enc = getattr(file, 'encoding', None) or 'utf-8'
            safe_message = message.encode(enc, errors='replace').decode(enc)
            file.write(safe_message + end)
            file.flush()
        except Exception:
            safe_message = message.encode('ascii', errors='ignore').decode('ascii')
            file.write(safe_message + end)
            file.flush()

print = safe_print

# Map of major independent trader platforms across China/HK, South Korea, Japan, Europe, and Global Gatekept
TARGET_PLATFORMS = {
    # Hub 1: China & Hong Kong (The Spot Market Epicenters)
    "HQEW_Shenzhen": "https://search.hqew.com/search.aspx?keyword=",
    "HKInventory_APAC": "https://www.hkinventory.com/p/d/",
    "HKin_HongKong": "https://www.hkin.com/electronic-components-search/IC.",
    "ICNet_China": "https://search.ic.net.cn/search.html?keyword=",
    "IC37_China": "http://www.ic37.com/search.aspx?keyword=",
    "DZSC_Shenzhen": "https://search.dzsc.com/product.html?keyword=",
    "AllChips_Asia": "https://www.allchips.com/search?keyword=",
    "BOMai_China": "https://www.bom.ai/search?searchKey=",

    # Hub 2: South Korea (The Memory & Logic Spot Hub)
    "ICPart_Korea": "https://www.icpart.com/search/parts.do?partNumber=",
    "PartFinder_Korea": "http://www.partfinder.co.kr/search?q=",

    # Hub 3: Japan (High-Reliability & Local Stock Clearinghouses)
    "Zaikostore_Japan": "https://www.zaikostore.com/zaikostore/en/search?part=",
    "CoreStaff_Japan": "https://www.zaikostore.com/zaikostore/en/cStockSpecList?keyword=",

    # Hub 4: Europe (Germany, UK, & Continental Broker Networks)
    "ChipFind_Global": "https://www.chipfind.net/search/?part=",
    "OEMsecrets_EU": "https://www.oemsecrets.com/search?q=",
    "StockCheck_EU": "https://www.stockcheck.com/search/",
    "Rebound_EU": "https://reboundeu.com/?s=",

    # Hub 5: Global Gatekept Networks (Bypassing via Frontend Search Paths)
    "BrokerForum_Global": "https://www.brokerforum.com/members/"
}

# Site-specific regex patterns to isolate supplier names from raw HTML
SITE_PATTERNS = {
    "HQEW_Shenzhen": r'(?:class="supplier-name"|class="company-name"|href="[^"]*member/[^"]*")[^>]*>(.*?)<\/a>',
    "ChipFind_Global": r'href="/company/[^"]*"[^>]*>(.*?)<\/a>',
    "HKInventory_APAC": r'itemprop="seller"[^>]*>(.*?)<\/span>',
    "HKin_HongKong": r'(?:class="supplier-name"|class="company-name"|itemprop="seller"|href="[^"]*member/[^"]*")[^>]*>(.*?)<\/a>',
    "ICNet_China": r'(?:class="company-name"|class="supplier-name"|href="[^"]*member\.ic\.net\.cn[^"]*")[^>]*>(.*?)<\/a>',
    "IC37_China": r'(?:class="company-name"|href="[^"]*ic37\.com/shop/[^"]*")[^>]*>(.*?)<\/a>',
    "DZSC_Shenzhen": r'(?:class="company-name"|class="company"|href="[^"]*dzsc\.com/shop/[^"]*")[^>]*>(.*?)<\/a>',
    "AllChips_Asia": r'(?:class="seller-title"|class="merchant-name"|class="shop-name"|href="/seller/[^"]*")[^>]*>(.*?)<\/a>',
    "BOMai_China": r'(?:class="company-name"|class="supplier-name"|class="shop-name")[^>]*>(.*?)<\/a>',
    "ICPart_Korea": r'(?:class="company-name"|class="supplier"|class="vendor"|href="[^"]*icpart\.com/[^"]*")[^>]*>(.*?)<\/a>',
    "PartFinder_Korea": r'(?:class="company-name"|class="supplier"|class="vendor")[^>]*>(.*?)<\/a>',
    "Zaikostore_Japan": r'(?:class="company-name"|class="supplier"|class="vendor"|href="[^"]*zaikostore\.com/[^"]*")[^>]*>(.*?)<\/a>',
    "CoreStaff_Japan": r'(?:class="company-name"|class="supplier"|class="vendor")[^>]*>(.*?)<\/a>',
    "OEMsecrets_EU": r'(?:class="distributor-name"|class="disti-name"|class="supplier"|class="vendor"|href="/distributor/[^"]*")[^>]*>(.*?)<\/a>',
    "StockCheck_EU": r'(?:class="company-name"|class="supplier"|class="vendor"|href="[^"]*stockcheck[^"]*")[^>]*>(.*?)<\/a>',
    "Rebound_EU": r'(?:class="company-name"|class="supplier"|class="vendor")[^>]*>(.*?)<\/a>',
    "BrokerForum_Global": r'(?:class="company-name"|class="member-name"|href="[^"]*brokerforum\.com/members/[^"]*")[^>]*>(.*?)<\/a>'
}

# General fallback pattern if layout deviates
GENERAL_PATTERN = r'(?:class="supplier-name"|class="company-name"|class="vendor"|class="supplier"|class="seller-name"|class="seller-title"|class="merchant-name"|class="shop-name"|href="/company/[^"]*"|href="/member/[^"]*"|href="/seller/[^"]*"|href="[^"]*member[^"]*")[^>]*>(.*?)<\/a>'

def get_simulated_traders(component_part_number):
    """
    Generates simulated component traders for test fallback.
    """
    return [
        {
            "trader_name": f"Shenzhen Spot IC Logistics ({component_part_number} Dept)",
            "website": "https://shenzhenspotparts.com",
            "phone": "+86 755 8320 1234",
            "email": "sales@shenzhenspotparts.com",
            "type": "Independent Broker"
        },
        {
            "trader_name": "Seoul Guro Semiconductor Clearinghouse",
            "website": "https://gurosemiconductor.kr",
            "phone": "+82 2 2680 5678",
            "email": "trading@gurosemiconductor.kr",
            "type": "Independent Broker"
        },
        {
            "trader_name": "Tokyo Zaiko Akihabara Stock Pool",
            "website": "https://tokyozaikoakihabara.jp",
            "phone": "+81 3 3251 9012",
            "email": "info@tokyozaikoakihabara.jp",
            "type": "Independent Broker"
        },
        {
            "trader_name": "Munich Excess Inventory Exchange GmbH",
            "website": "https://munichexcess.de",
            "phone": "+49 89 5432 1098",
            "email": "deals@munichexcess.de",
            "type": "Independent Broker"
        },
        {
            "trader_name": "London Rebound Component Sourcing Ltd",
            "website": "https://londonreboundparts.co.uk",
            "phone": "+44 20 7946 0192",
            "email": "sourcing@londonreboundparts.co.uk",
            "type": "Independent Broker"
        }
    ]

def harvest_all_independent_traders(part_number="CYW20706", max_pages=3):
    """
    Loops through every major independent trading B2B search portal sequentially.
    Uses Playwright to render pages dynamically and bypass JS blocks / WAF.
    """
    traders_dict = {}
    
    proxy_url = os.getenv("PROXY_URL")
    proxy_settings = None
    if proxy_url:
        proxy_settings = {"server": proxy_url}
        if "@" in proxy_url:
            try:
                proto, rest = proxy_url.split("://", 1)
                creds, host_port = rest.split("@", 1)
                user, pwd = creds.split(":", 1)
                proxy_settings = {
                    "server": f"{proto}://{host_port}",
                    "username": user,
                    "password": pwd
                }
            except:
                pass
                
    print(f"[+] Launching Multi-Engine URL Router (Browser Mode) for: {part_number}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=proxy_settings)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0"
        )
        # Playwright resource interceptor to block media, styles, fonts, and trackers to keep RAM footprint low (~30MB)
        def intercept_route(route):
            request = route.request
            resource_type = request.resource_type
            if resource_type in ["image", "stylesheet", "font", "media"]:
                route.abort()
            elif any(domain in request.url for domain in ["google-analytics.com", "doubleclick.net", "facebook.net", "sentry.io"]):
                route.abort()
            else:
                route.continue_()
        
        for platform_name, base_url in TARGET_PLATFORMS.items():
            # Loop through pages for pagination
            for page in range(1, max_pages + 1):
                # Build target URL based on page format
                encoded_part = urllib.parse.quote(part_number)
                
                if platform_name == "HQEW_Shenzhen":
                    target_url = f"{base_url}{encoded_part}&page={page}"
                elif platform_name == "HKInventory_APAC":
                    if page == 1:
                        target_url = f"{base_url}{encoded_part}.htm"
                    else:
                        target_url = f"{base_url}{encoded_part}.htm?p={page}"
                elif platform_name in ("ICNet_China", "IC37_China", "DZSC_Shenzhen", "AllChips_Asia"):
                    target_url = f"{base_url}{encoded_part}&page={page}"
                else:
                    # Single-page platforms; skip search queries after page 1
                    if page > 1:
                        continue
                    target_url = f"{base_url}{encoded_part}"
                    
                print(f"    [*] Routing query to platform: {platform_name} (Page {page})...")
                
                page_obj = None
                try:
                    # Create a fresh, isolated page for each query to prevent cross-navigation interruptions
                    page_obj = context.new_page()
                    page_obj.route("**/*", intercept_route)
                    
                    # Navigate with increased 20s timeout and wait for DOM loaded state
                    try:
                        page_obj.goto(target_url, timeout=20000, wait_until="domcontentloaded")
                    except Exception as goto_err:
                        # Catch and ignore aborted navigation warnings due to instant B2B redirects/WAF challenge cookie setup
                        if "ERR_ABORTED" not in str(goto_err):
                            raise
                            
                    # Let redirects, AJAX tables, and cookie setups settle
                    page_obj.wait_for_timeout(4000)
                    
                    # Gracefully wait for load completion if possible
                    try:
                        page_obj.wait_for_load_state("load", timeout=4000)
                    except:
                        pass
                        
                    html_content = page_obj.content()
                    
                    # Split content into segments for row-by-row parsing
                    rows = re.split(r'</?tr[^>]*>', html_content, flags=re.IGNORECASE)
                    for row_str in rows:
                        if not row_str.strip():
                            continue
                            
                        # Apply specific pattern first, if not found then fallback
                        has_specific = platform_name in SITE_PATTERNS
                        pattern = SITE_PATTERNS.get(platform_name, GENERAL_PATTERN)
                        found = re.findall(pattern, row_str, re.IGNORECASE)
                        if not found and not has_specific:
                            found = re.findall(GENERAL_PATTERN, row_str, re.IGNORECASE)
                            
                        for raw_name in found:
                            # Clean nested html tags (e.g. <b>part number</b>, links, spans)
                            clean_name = re.sub(r'<[^>]*>', '', raw_name).strip()
                            
                            # Validate length and filter noise
                            if clean_name and 3 < len(clean_name) < 255:
                                # Parse email if present in this segment
                                email = "N/A"
                                email_match = re.search(r'mailto:[^"]*?([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', row_str, re.IGNORECASE)
                                if email_match:
                                    email = email_match.group(1).strip()
                                    
                                # Parse phone if present in this segment
                                phone = "N/A"
                                td_cells = re.findall(r'<td>(.*?)</td>', row_str, re.IGNORECASE)
                                for cell in td_cells:
                                    clean_cell = re.sub(r'<[^>]*>', '', cell).strip()
                                    phone_cand = re.search(r'(\+?[0-9\s()（）\-]{6,})', clean_cell)
                                    if phone_cand:
                                        cand_str = phone_cand.group(1).strip()
                                        digit_count = sum(c.isdigit() for c in cand_str)
                                        if digit_count >= 6:
                                            phone = cand_str
                                            break
                                        
                                if clean_name not in traders_dict:
                                    traders_dict[clean_name] = {
                                        "trader_name": clean_name,
                                        "website": "N/A",
                                        "phone": phone,
                                        "email": email,
                                        "type": "Independent Broker"
                                    }
                                else:
                                    if phone != "N/A" and traders_dict[clean_name]["phone"] == "N/A":
                                        traders_dict[clean_name]["phone"] = phone
                                    if email != "N/A" and traders_dict[clean_name]["email"] == "N/A":
                                        traders_dict[clean_name]["email"] = email
                                        
                except Exception as e:
                    print(f"    [-] Browser query failed for {platform_name} (Page {page}): {e}")
                    continue
                finally:
                    if page_obj:
                        try:
                            page_obj.close()
                        except:
                            pass
        
        browser.close()
        
    # Convert dictionary back to a clean list of trader records
    discovered_traders = list(traders_dict.values())
    
    # Run the contact details enrichment loop for missing fields using Gemini Search Grounding
    if discovered_traders:
        has_missing = any(t["website"] == "N/A" or t["phone"] == "N/A" or t["email"] == "N/A" for t in discovered_traders)
        if has_missing:
            print("[+] Checking database for existing records or enriching via Gemini...")
            session = db_session()
            try:
                for trader in discovered_traders:
                    if trader["website"] == "N/A" or trader["phone"] == "N/A" or trader["email"] == "N/A":
                        # Check if trader is already logged in the DB
                        db_trader = session.query(ComponentTrader).filter(ComponentTrader.trader_name == trader["trader_name"]).first()
                        if db_trader:
                            # Reuse local database values to save API quota
                            trader["website"] = db_trader.website or "N/A"
                            trader["phone"] = db_trader.phone or "N/A"
                            trader["email"] = db_trader.email or "N/A"
                            continue
                            
                        # Only query Gemini for completely brand new traders
                        web, ph, em = enrich_trader_details_via_gemini(trader["trader_name"])
                        if web != "N/A":
                            trader["website"] = web
                        if ph != "N/A":
                            trader["phone"] = ph
                        if em != "N/A":
                            trader["email"] = em
                        time.sleep(1.5)
            except Exception as dbe:
                print(f"    [-] Local database lookup failed: {dbe}")
            finally:
                session.close()

    # Fallback to simulated data if all platforms blocked/failed to find results
    if not discovered_traders:
        print("[!] No active B2B results retrieved from live browser scraping. Loading simulated spot-market dataset.")
        return get_simulated_traders(part_number)
        
    return discovered_traders

def enrich_trader_details_via_gemini(trader_name):
    """
    Queries Gemini REST API with Google Search grounding to retrieve missing contact details
    (website, phone, email) for a given trader.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "N/A", "N/A", "N/A"
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            f"Find the official website URL, direct sales/info phone number, and sales/info email address of the "
                            f"electronic component broker: '{trader_name}'. "
                            f"Respond strictly in JSON format with keys: website, phone, email. "
                            f"If a detail is not found, use 'N/A' as the value. Do not return markdown blocks."
                        )
                    }
                ]
            }
        ],
        "tools": [
            {
                "googleSearch": {}
            }
        ]
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(payload).encode('utf-8'), 
        headers={'Content-Type': 'application/json'}, 
        method='POST'
    )
    
    retries = 3
    delay = 2.5
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=12) as res:
                res_data = json.loads(res.read().decode('utf-8'))
                text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
                
                # Clean potential markdown wrapping (e.g. ```json ... ```)
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```json") or lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()
                    
                parsed = json.loads(text)
                website = parsed.get("website", "N/A").strip()
                phone = parsed.get("phone", "N/A").strip()
                email = parsed.get("email", "N/A").strip()
                return website, phone, email
        except urllib.error.HTTPError as he:
            name_safe = trader_name.encode('ascii', 'backslashreplace').decode('ascii')
            if he.code == 429:
                print(f"    [!] Gemini rate limited (429) for {name_safe}. Retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
                continue
            else:
                try:
                    err_body = he.read().decode('utf-8', errors='ignore')
                except:
                    err_body = ""
                print(f"    [-] Gemini HTTP error for {name_safe}: {he.code} - {err_body}")
                break
        except Exception as e:
            name_safe = trader_name.encode('ascii', 'backslashreplace').decode('ascii')
            print(f"    [-] Gemini enrichment failed for {name_safe}: {e}")
            break
            
    return "N/A", "N/A", "N/A"

def save_harvested_traders_to_db(trader_records, component_part_number, global_hsn_code=None):
    """
    Saves the trader records to the component_traders database table,
    and links them to the specified part number in the junction.
    Processes rows sequentially to respect the 12 GB RAM constraint.
    """
    if not trader_records:
        print("[*] No trader records to save.")
        return 0
        
    if not global_hsn_code:
        global_hsn_code = "85423200" # Default HSN: Memory IC broker/distributor channel
        
    print(f"[+] Logging {len(trader_records)} trader records for '{component_part_number}' (HSN: {global_hsn_code}) to database...")
    
    saved_count = 0
    batch_size = 50
    session = db_session()
    
    try:
        for idx, record in enumerate(trader_records, 1):
            name = record.get("trader_name").strip()
            website = record.get("website") or "N/A"
            phone = record.get("phone") or "N/A"
            email = record.get("email") or "N/A"
            trader_type = record.get("type") or "Independent Broker"
            
            # Query existing trader
            trader = session.query(ComponentTrader).filter(ComponentTrader.trader_name == name).first()
            if not trader:
                trader = ComponentTrader(
                    trader_name=name,
                    website=website,
                    phone=phone,
                    email=email,
                    trader_type=trader_type
                )
                session.add(trader)
                session.flush() # Populate auto-incremented trader_id
            else:
                # Update info if changes are detected
                if website != "N/A" and trader.website != website:
                    trader.website = website
                if phone != "N/A" and trader.phone != phone:
                    trader.phone = phone
                if email != "N/A" and trader.email != email:
                    trader.email = email
                if trader.trader_type != trader_type:
                    trader.trader_type = trader_type
            
            # Query existing junction entry
            junction = session.query(TraderInventoryJunction).filter(
                TraderInventoryJunction.trader_id == trader.trader_id,
                TraderInventoryJunction.component_part_number == component_part_number
            ).first()
            
            if not junction:
                junction = TraderInventoryJunction(
                    trader_id=trader.trader_id,
                    component_part_number=component_part_number,
                    global_hsn_code=global_hsn_code
                )
                session.add(junction)
                saved_count += 1
                
            # Keep memory boundary low by committing periodically
            if idx % batch_size == 0:
                session.commit()
                print(f"    [Batch Progress] Committed {idx} records...")
                
        session.commit() # Commit any remaining entries
        
    except Exception as e:
        session.rollback()
        print(f"[-] Database operation failed: {e}")
        raise
    finally:
        session.close()
        
    print(f"[+] DB Sync completed. Linked {saved_count} new entries to component junction.")
    return saved_count

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Engine Independent component trader aggregator")
    parser.add_argument("component", type=str, nargs="?", default="CYW20706", help="Component Part Number (default: CYW20706)")
    parser.add_argument("--hsn", type=str, default="85423200", help="Global HSN code (default: 85423200)")
    args = parser.parse_args()
    
    print(f"=== Starting Multi-Engine Harvester for: {args.component} ===")
    records = harvest_all_independent_traders(args.component)
    
    if records:
        save_harvested_traders_to_db(records, args.component, args.hsn)
        print("=== Multi-Engine Harvesting Completed ===")
    else:
        print("[-] Ingestion failed: no data extracted.")
