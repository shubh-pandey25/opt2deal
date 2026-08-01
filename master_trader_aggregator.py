import os
import sys
import re
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import argparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Database configuration import
from database import db_session, ComponentTrader, TraderInventoryJunction, Company

# Load environment variables
load_dotenv()

# Safe console printing interceptor to prevent CP1252 Windows encoding crashes
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

# ---------------------------------------------------------
# Configuration: Target Channels and Regex Maps
# ---------------------------------------------------------

TARGET_CHANNELS = {
    # 🇨🇳 🇭🇰 Hub 1: China & Hong Kong (B2B stock boards)
    "HQEW_Shenzhen": "https://search.hqew.com/search.aspx?keyword=",
    "HKInventory_APAC": "https://www.hkin.com/electronic-components-search/IC.",
    "HKin_HongKong": "https://search.hkin.com/search.html?keyword=",
    "ICNet_China": "https://search.ic.net.cn/search.html?keyword=",
    "IC37_China": "http://www.ic37.com/search.aspx?keyword=",
    "DZSC_Shenzhen": "https://search.dzsc.com/product.html?keyword=",
    "AllChips_Asia": "https://www.allchips.com/search?keyword=",
    "BOMai_China": "https://www.bom.ai/search?searchKey=",
    "Allied_Electronics_Asia": "https://www.alliedic.com/search?keyword=",
    "IC2020_China": "http://search.ic2020.com.cn/search.html?q=",

    # 🇰🇷 Hub 2: South Korea (B2B stock boards)
    "ICPart_Korea": "https://www.icpart.com/search/parts.do?partNumber=",
    "PartFinder_Korea": "http://www.partfinder.co.kr/search?q=",
    "IC114_Korea": "https://www.ic114.com/web/search.aspx?q=",

    # 🇯🇵 Hub 3: Japan (B2B stock boards)
    "Zaikostore_Japan": "https://www.zaikostore.com/zaikostore/en/search?part=",
    "CoreStaff_Japan": "https://www.zaikostore.com/zaikostore/en/cStockSpecList?keyword=",
    "Chip1Stop_Japan": "https://www.chip1stop.com/USA/en/search?partId=",

    # 🇪🇺 Hub 4: Europe (B2B stock boards)
    "ChipFind_Global": "https://www.chipfind.net/search/?part=",
    "OEMsecrets_EU": "https://www.oemsecrets.com/search?q=",
    "StockCheck_EU": "https://www.stockcheck.com/search/",
    "Rebound_EU": "https://reboundeu.com/?s=",
    "Componentes_Electronicos_EU": "https://www.componentes-electronicos.com/search?part=",
    "Semiconductor_Sourcing_UK": "https://www.semiconductor-sourcing.com/search/",

    # 🌐 Hub 5: Global Networks
    "BrokerForum_Global": "https://www.brokerforum.com/members/",
    "NetComponents_Global": "https://www.netcomponents.com/results.htm?r=1&part=",
    "ICSource_Global": "https://www.icsource.com/public/search.aspx?part=",
    "EEAllParts_APAC": "https://www.eeallparts.com/",

    # 📋 Customs & Export Manifest Channels
    "Cybex_Customs": "https://www.cybex.in/search-global-trade-data.aspx?mpn=",
    "Volza_Customs": "https://www.volza.com/p/{encoded_part}/export/",
    "Zauba_Customs": "https://www.zauba.com/export-{encoded_part}-hs-code.html",
    "ImportGenius_Customs": "https://www.importgenius.com/search/{encoded_part}",
    "Panjiva_Customs": "https://panjiva.com/search?q={encoded_part}",
    "Seair_Exim_Customs": "https://www.seair.co.in/customs-data/search?q={encoded_part}",
    "Infodrive_India_Customs": "https://www.infodriveindia.com/customs-data/search?q={encoded_part}",
    "Eximpulse_Customs": "https://www.eximpulse.com/",
    "TradeGenius_Customs": "https://www.tradegenius.in/search?keyword={encoded_part}",
    "Export_Genius_Customs": "https://www.exportgenius.com/global-trade-data?q={encoded_part}",
    "Tendata_Customs": "https://www.tendata.com/search/shippers?q={encoded_part}"
}

# Regex pattern mapping for B2B search rows extraction
SITE_PATTERNS = {
    "HQEW_Shenzhen": r'(?:company|supplier|trader)[:\s]*"([^"]+)"|class="company-name"[^>]*>([^<]+)</a>',
    "HKInventory_APAC": r'class="gname"[^>]*>([^<]+)</a>|class="company-link"[^>]*>([^<]+)</a>',
    "HKin_HongKong": r'class="comp-name"[^>]*>([^<]+)</a>|class="company-title"[^>]*>([^<]+)</a>',
    "ICNet_China": r'class="company-name"[^>]*>([^<]+)</a>|class="qy-name"[^>]*>([^<]+)</a>',
    "IC37_China": r'class="companyName"[^>]*>([^<]+)</a>|class="shopName"[^>]*>([^<]+)</a>',
    "DZSC_Shenzhen": r'class="company_name"[^>]*>([^<]+)</a>|class="gys_name"[^>]*>([^<]+)</a>',
    "AllChips_Asia": r'class="supplier-name"[^>]*>([^<]+)</a>|class="merchant-name"[^>]*>([^<]+)</a>',
    "BOMai_China": r'class="company-title"[^>]*>([^<]+)</a>',
    "ICPart_Korea": r'class="comp-title"[^>]*>([^<]+)</a>|class="corp-name"[^>]*>([^<]+)</a>',
    "PartFinder_Korea": r'class="trader-name"[^>]*>([^<]+)</a>',
    "IC114_Korea": r'class="company-link"[^>]*>([^<]+)</a>|class="manufacturer-name"[^>]*>([^<]+)</a>',
    "Zaikostore_Japan": r'class="store-name"[^>]*>([^<]+)</a>|class="supplier-title"[^>]*>([^<]+)</a>',
    "CoreStaff_Japan": r'class="staff-name"[^>]*>([^<]+)</a>',
    "Chip1Stop_Japan": r'class="supplier-title"[^>]*>([^<]+)</a>|class="distributor-name"[^>]*>([^<]+)</a>',
    "ChipFind_Global": r'class="member-name"[^>]*>([^<]+)</a>|class="supplier-link"[^>]*>([^<]+)</a>',
    "OEMsecrets_EU": r'class="distributor-name"[^>]*>([^<]+)</a>|class="vendor-name"[^>]*>([^<]+)</a>',
    "StockCheck_EU": r'class="dist-name"[^>]*>([^<]+)</a>',
    "Rebound_EU": r'class="rebound-supplier"[^>]*>([^<]+)</a>',
    "BrokerForum_Global": r'class="member-title"[^>]*>([^<]+)</a>|class="broker-name"[^>]*>([^<]+)</a>',
    "NetComponents_Global": r'class="dist-link"[^>]*>([^<]+)</a>|class="supplier-name"[^>]*>([^<]+)</a>',
    "ICSource_Global": r'class="trader-name"[^>]*>([^<]+)</a>',
    "Allied_Electronics_Asia": r'class="company-link"[^>]*>([^<]+)</a>|class="supplier-title"[^>]*>([^<]+)</a>',
    "IC2020_China": r'class="gysName"[^>]*>([^<]+)</a>|class="companyName"[^>]*>([^<]+)</a>',
    "Componentes_Electronicos_EU": r'class="dist-name"[^>]*>([^<]+)</a>|class="trader-link"[^>]*>([^<]+)</a>',
    "Semiconductor_Sourcing_UK": r'class="supplier-name"[^>]*>([^<]+)</a>',
    "EEAllParts_APAC": r'class="company-title"[^>]*>([^<]+)</a>'
}

GENERAL_PATTERN = r'class="[^"]*(?:company|supplier|trader|vendor|distributor)[^"]*"[^>]*>([^<]+)</a>|class="companyName"[^>]*>([^<]+)</a>'

# ---------------------------------------------------------
# Sourcing & Parsing Logic Helpers
# ---------------------------------------------------------

def clean_html_tags(text: str) -> str:
    """Removes HTML elements to leave clean textual content."""
    if not text:
        return ""
    text = re.sub(r'<script.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<.*?>', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def parse_customs_manifest_via_llm(page_text, part_number):
    """
    Invokes Groq API (Llama-3.3-70b) to parse unstructured manifest table text.
    """
    try:
        from config import get_groq_client, GROQ_MODEL
        client = get_groq_client()
    except Exception as e:
        print(f"    [-] Failed to initialize Groq client for manifest parsing: {e}")
        return []

    prompt = (
        f"Analyze the following global trade customs manifest preview text for component '{part_number}'. "
        f"Extract all shipment records and return them in JSON format. "
        f"Each record should contain these fields: exporter_name, importer_name, date, origin_country, destination_country. "
        f"If a field is missing, use 'N/A' as the value. "
        f"Filter out generic headings, labels, or noise, and output only active, valid records. "
        f"Do not return any explanations or markdown formatting, respond strictly in JSON matching this schema: "
        f"{{\n"
        f"  \"shipments\": [\n"
        f"    {{\n"
        f"      \"exporter_name\": \"string\",\n"
        f"      \"importer_name\": \"string\",\n"
        f"      \"date\": \"string\",\n"
        f"      \"origin_country\": \"string\",\n"
        f"      \"destination_country\": \"string\"\n"
        f"    }}\n"
        f"  ]\n"
        f"}}\n\n"
        f"MANIFEST TEXT:\n{page_text}"
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        data = json.loads(response.choices[0].message.content)
        return data.get("shipments", [])
    except Exception as e:
        print(f"    [-] Groq manifest parsing failed: {e}")
        return []


# ---------------------------------------------------------
# Core Master Aggregation Loop
# ---------------------------------------------------------

def aggregate_all_traders(part_number="CYW20706", max_pages=3):
    """
    Unified harvester querying B2B stock boards and customs manifest preview channels
    sequentially in a single processing loop.
    """
    traders_dict = {}
    importers_list = []
    
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

    print(f"[+] Launching Unified Master Trader Aggregator for component: {part_number}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=proxy_settings)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0",
            ignore_https_errors=True
        )
        
        # Interceptor to block CSS/Images/Fonts/Trackers to keep footprint at ~30MB
        def intercept_route(route):
            request = route.request
            resource_type = request.resource_type
            if resource_type in ["image", "stylesheet", "font", "media"]:
                route.abort()
            elif any(domain in request.url for domain in ["google-analytics.com", "doubleclick.net", "facebook.net", "sentry.io"]):
                route.abort()
            else:
                route.continue_()

        # Iterate through target platforms
        for channel_name, base_url in TARGET_CHANNELS.items():
            is_customs = any(k in channel_name for k in ["Export", "Customs", "Zauba", "Cybex", "Volza"])
            classification = "Customs Manifest Exporter" if is_customs else "Independent Broker"
            
            # Customs sources only have a single index query; stock boards support pagination
            run_pages = 1 if is_customs else max_pages
            
            for page in range(1, run_pages + 1):
                encoded_part = urllib.parse.quote(part_number)
                
                # Format URL based on platform specifications
                if is_customs:
                    if "{encoded_part}" in base_url:
                        target_url = base_url.format(encoded_part=encoded_part)
                    else:
                        target_url = f"{base_url}{encoded_part}"
                else:
                    if channel_name == "HQEW_Shenzhen":
                        target_url = f"{base_url}{encoded_part}&page={page}"
                    elif channel_name == "HKInventory_APAC":
                        if page == 1:
                            target_url = f"{base_url}{encoded_part}.htm"
                        else:
                            target_url = f"{base_url}{encoded_part}.htm?p={page}"
                    elif channel_name in ("ICNet_China", "IC37_China", "DZSC_Shenzhen", "AllChips_Asia"):
                        target_url = f"{base_url}{encoded_part}&page={page}"
                    else:
                        # Single-page B2B stock boards
                        if page > 1:
                            continue
                        target_url = f"{base_url}{encoded_part}"

                print(f"    [*] Sourcing from: {channel_name} (Page {page}) - Classification: {classification}...")
                
                page_obj = None
                try:
                    # Fresh isolated page instance
                    page_obj = context.new_page()
                    page_obj.route("**/*", intercept_route)
                    
                    try:
                        page_obj.goto(target_url, timeout=20000, wait_until="domcontentloaded")
                    except Exception as goto_err:
                        raise goto_err

                    # Wait loop for Cloudflare redirect challenges (up to 15s)
                    for _ in range(15):
                        try:
                            content = page_obj.content()
                            if "security verification" not in content and "Just a moment" not in content:
                                break
                        except Exception:
                            pass # Let active redirects/navigations settle
                        page_obj.wait_for_timeout(1000)
                        
                    page_obj.wait_for_timeout(3000)
                    try:
                        page_obj.wait_for_load_state("load", timeout=4000)
                    except:
                        pass
                        
                    try:
                        html_content = page_obj.content()
                    except Exception:
                        # Wait another 4 seconds if page is still navigating in the background
                        page_obj.wait_for_timeout(4000)
                        html_content = page_obj.content()
                        
                    plain_text = clean_html_tags(html_content)
                    
                    if is_customs:
                        # --- Customs Manifest Parsing Subroutine ---
                        shipments = parse_customs_manifest_via_llm(plain_text[:12000], part_number)
                        
                        # Fallback parsing strategy
                        if not shipments:
                            pairs = re.findall(
                                r'(?:exporter|seller|shipper)[:\s]+([^:\n]+?)(?:importer|buyer|consignee)[:\s]+([^:\n]+)',
                                plain_text, 
                                re.IGNORECASE
                            )
                            for exporter, importer in pairs:
                                shipments.append({
                                    "exporter_name": exporter.strip(),
                                    "importer_name": importer.strip(),
                                    "date": "N/A",
                                    "origin_country": "N/A"
                                })
                                
                        for s in shipments:
                            exporter = s.get("exporter_name", "N/A").strip()
                            importer = s.get("importer_name", "N/A").strip()
                            origin = s.get("origin_country", "N/A").strip()
                            
                            if exporter and exporter != "N/A" and len(exporter) > 3 and not re.search(r'(exporter|shipper|name|signee)', exporter, re.IGNORECASE):
                                if exporter not in traders_dict:
                                    traders_dict[exporter] = {
                                        "trader_name": exporter,
                                        "website": "N/A",
                                        "phone": "N/A",
                                        "email": "N/A",
                                        "type": classification,
                                        "origin": origin
                                    }
                            if importer and importer != "N/A" and len(importer) > 3 and not re.search(r'(importer|consignee|buyer|name)', importer, re.IGNORECASE):
                                if importer not in importers_list:
                                    importers_list.append(importer)
                    else:
                        # --- Stock Board Parsing Subroutine ---
                        rows = re.split(r'</?tr[^>]*>', html_content, flags=re.IGNORECASE)
                        for row_str in rows:
                            if not row_str.strip():
                                continue
                            has_specific = channel_name in SITE_PATTERNS
                            pattern = SITE_PATTERNS.get(channel_name, GENERAL_PATTERN)
                            found = re.findall(pattern, row_str, re.IGNORECASE)
                            if not found and not has_specific:
                                found = re.findall(GENERAL_PATTERN, row_str, re.IGNORECASE)
                                
                            for raw_name in found:
                                # Clean potential tuple output if regex uses groupings
                                if isinstance(raw_name, tuple):
                                    raw_name = next((x for x in raw_name if x), "")
                                    
                                clean_name = re.sub(r'<[^>]*>', '', raw_name).strip()
                                if clean_name and 3 < len(clean_name) < 255:
                                    email = "N/A"
                                    email_match = re.search(r'mailto:[^"]*?([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', row_str, re.IGNORECASE)
                                    if email_match:
                                        email = email_match.group(1).strip()
                                        
                                    phone = "N/A"
                                    td_cells = re.findall(r'<td>(.*?)</td>', row_str, re.IGNORECASE)
                                    for cell in td_cells:
                                        clean_cell = re.sub(r'<[^>]*>', '', cell).strip()
                                        phone_cand = re.search(r'(\+?[0-9\s()（）\-]{6,})', clean_cell)
                                        if phone_cand:
                                            cand_str = phone_cand.group(1).strip()
                                            if sum(c.isdigit() for c in cand_str) >= 6:
                                                phone = cand_str
                                                break
                                                
                                    if clean_name not in traders_dict:
                                        traders_dict[clean_name] = {
                                            "trader_name": clean_name,
                                            "website": "N/A",
                                            "phone": phone,
                                            "email": email,
                                            "type": classification
                                        }
                                    else:
                                        if phone != "N/A" and traders_dict[clean_name]["phone"] == "N/A":
                                            traders_dict[clean_name]["phone"] = phone
                                        if email != "N/A" and traders_dict[clean_name]["email"] == "N/A":
                                            traders_dict[clean_name]["email"] = email
                                            
                except Exception as e:
                    print(f"    [-] Channel query failed for {channel_name} (Page {page}): {e}")
                finally:
                    if page_obj:
                        try:
                            page_obj.close()
                        except:
                            pass
                            
        browser.close()
        
    discovered_traders = list(traders_dict.values())
    


    # Filter out empty trader name rows
    discovered_traders = [t for t in discovered_traders if t.get("trader_name")]
    
    # ---------------------------------------------------------
    # Contact enrichment via Gemini
    # ---------------------------------------------------------
    new_traders = []
    session = db_session()
    try:
        for t in discovered_traders:
            name = t["trader_name"].strip()
            existing = session.query(ComponentTrader).filter(ComponentTrader.trader_name == name).first()
            if not existing:
                new_traders.append(t)
    finally:
        session.close()

    if new_traders:
        print(f"[+] Enriching details for {len(new_traders)} brand-new discovered entities...")
        for trader in new_traders:
            web, ph, em = enrich_trader_details_via_gemini(trader["trader_name"])
            trader["website"] = web
            if ph != "N/A":
                trader["phone"] = ph
            if em != "N/A":
                trader["email"] = em
            time.sleep(1.5)

    # ---------------------------------------------------------
    # Persist Consolidated Data to MySQL
    # ---------------------------------------------------------
    save_master_records_to_db(discovered_traders, importers_list, part_number)
    
    return discovered_traders

def enrich_trader_details_via_gemini(trader_name):
    """
    Grounds company names with Gemini Search to retrieve official website, phone, and email.
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
                            f"electronic component seller: '{trader_name}'. "
                            f"Respond strictly in JSON format with keys: website, phone, email. "
                            f"If a detail is not found, use 'N/A' as the value. Do not return markdown blocks."
                        )
                    }
                ]
            }
        ],
        "tools": [
            {"googleSearch": {}}
        ]
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(payload).encode('utf-8'), 
        headers={'Content-Type': 'application/json'}, 
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=12) as res:
            res_data = json.loads(res.read().decode('utf-8'))
            text = res_data['candidates'][0]['content']['parts'][0]['text'].strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[0].startswith("```json") or lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines).strip()
            parsed = json.loads(text)
            return parsed.get("website", "N/A"), parsed.get("phone", "N/A"), parsed.get("email", "N/A")
    except:
        return "N/A", "N/A", "N/A"

def save_master_records_to_db(traders, importers, part_number, global_hsn_code="85423200"):
    """
    Saves and links all discovered exporters/brokers and buyer leads to MySQL.
    """
    session = db_session()
    traders_saved = 0
    importers_saved = 0
    
    try:
        # 1. Sync Exporters / Independent Brokers
        for record in traders:
            name = record.get("trader_name").strip()
            website = record.get("website") or "N/A"
            phone = record.get("phone") or "N/A"
            email = record.get("email") or "N/A"
            t_type = record.get("type") or "Independent Broker"
            
            trader = session.query(ComponentTrader).filter(ComponentTrader.trader_name == name).first()
            if not trader:
                trader = ComponentTrader(
                    trader_name=name,
                    website=website,
                    phone=phone,
                    email=email,
                    trader_type=t_type
                )
                session.add(trader)
                session.flush()
                
            junction = session.query(TraderInventoryJunction).filter(
                TraderInventoryJunction.trader_id == trader.trader_id,
                TraderInventoryJunction.component_part_number == part_number
            ).first()
            
            if not junction:
                junction = TraderInventoryJunction(
                    trader_id=trader.trader_id,
                    component_part_number=part_number,
                    global_hsn_code=global_hsn_code
                )
                session.add(junction)
                traders_saved += 1
                
        # 2. Sync Importers (Potential buyer leads pool)
        for name in importers:
            name = name.strip()
            company = session.query(Company).filter(Company.company_name == name).first()
            if not company:
                company = Company(
                    company_name=name,
                    crawl_status="pending",
                    website="N/A",
                    company_description="Imported lead from global trade manifest records."
                )
                session.add(company)
                importers_saved += 1
                
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[-] Database consolidation sync failed: {e}")
        raise
    finally:
        session.close()
        
    print(f"[+] Consolidated DB Sync complete. Linked {traders_saved} traders and {importers_saved} buyer leads.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified master trader aggregator pipeline")
    parser.add_argument("component", type=str, nargs="?", default="CYW20706", help="Component Part Number")
    parser.add_argument("--pages", type=int, default=3, help="Max B2B page pagination limit")
    args = parser.parse_args()
    
    print(f"=== Starting Consolidated Trader Aggregator for: {args.component} ===")
    aggregate_all_traders(args.component, max_pages=args.pages)
    print("=== Consolidated Sourcing Pipeline Completed ===")
