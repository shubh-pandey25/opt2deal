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

# Load database configurations
from database import db_session, ComponentTrader, TraderInventoryJunction, Company

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

def clean_html_tags(text: str) -> str:
    """Cleans nested html tags to get plain text."""
    if not text:
        return ""
    text = re.sub(r'<script.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<.*?>', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def parse_customs_manifest_via_llm(page_text, part_number):
    """
    Uses Groq API to parse unformatted page text into structured customs shipment records.
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


def harvest_customs_manifests(part_number="CYW20706"):
    """
    Crawls Cybex and Volza customs data preview portals to extract shipments,
    saving exporters (suppliers) and importers (buyer leads) to the database.
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
                
    # Define search routes
    encoded_part = urllib.parse.quote(part_number)
    targets = {
        "Cybex_Customs": f"https://www.cybex.in/search-global-trade-data.aspx?mpn={encoded_part}",
        "Volza_Customs": f"https://www.volza.com/p/{encoded_part}/export/"
    }
    
    print(f"[+] Launching Customs Manifest Harvester for component: {part_number}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy=proxy_settings)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0"
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

        for platform_name, target_url in targets.items():
            print(f"    [*] Fetching customs preview records from: {platform_name}...")
            page_obj = None
            try:
                page_obj = context.new_page()
                page_obj.route("**/*", intercept_route)
                
                # Navigate with timeout and wait settling
                try:
                    page_obj.goto(target_url, timeout=20000, wait_until="domcontentloaded")
                except Exception as goto_err:
                    if "ERR_ABORTED" not in str(goto_err):
                        raise
                        
                # Wait for Cloudflare/WAF redirects to resolve (up to 15 seconds)
                for _ in range(15):
                    content = page_obj.content()
                    if "security verification" not in content and "Just a moment" not in content:
                        break
                    page_obj.wait_for_timeout(1000)
                    
                page_obj.wait_for_timeout(3000)
                try:
                    page_obj.wait_for_load_state("load", timeout=4000)
                except:
                    pass
                    
                html_content = page_obj.content()
                plain_text = clean_html_tags(html_content)
                print(f"      [Debug] HTML length: {len(html_content)}, Plain text length: {len(plain_text)}")
                if len(plain_text) > 200:
                    print(f"      [Debug] Snippet: {plain_text[:300]}")
                
                # 1. Primary Parsing Strategy: Send text to Gemini to clean-parse preview grids
                shipments = parse_customs_manifest_via_llm(plain_text[:12000], part_number) # Truncate to save token window
                print(f"      [Debug] Parser extracted {len(shipments or [])} shipments.")
                
                # 2. Fallback Parsing Strategy: Regex search for common exporter/importer table pattern rows
                if not shipments:
                    # Look for exporter/importer pattern blocks in raw text
                    # e.g., "Exporter: ABC Co Ltd Importer: XYZ Corp"
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
                            "origin_country": "N/A",
                            "destination_country": "N/A"
                        })

                # Ingest results
                for s in shipments:
                    exporter = s.get("exporter_name", "N/A").strip()
                    importer = s.get("importer_name", "N/A").strip()
                    origin = s.get("origin_country", "N/A").strip()
                    
                    # Clean exporter name and validate
                    if exporter and exporter != "N/A" and len(exporter) > 3 and not re.search(r'(exporter|shipper|name|signee)', exporter, re.IGNORECASE):
                        if exporter not in traders_dict:
                            traders_dict[exporter] = {
                                "trader_name": exporter,
                                "website": "N/A",
                                "phone": "N/A",
                                "email": "N/A",
                                "type": "Customs Exporter",
                                "origin": origin
                            }
                            
                    # Clean importer name and add to buyer leads pool
                    if importer and importer != "N/A" and len(importer) > 3 and not re.search(r'(importer|consignee|buyer|name)', importer, re.IGNORECASE):
                        if importer not in importers_list:
                            importers_list.append(importer)
                            
            except Exception as e:
                print(f"    [-] Browser query failed for {platform_name}: {e}")
            finally:
                if page_obj:
                    try:
                        page_obj.close()
                    except:
                        pass
                        
        browser.close()
        
    discovered_traders = list(traders_dict.values())
    

    
    # Enrich missing contact details via Gemini Search Grounding
    if discovered_traders:
        print("[+] Enriching newly discovered customs exporters via Gemini...")
        for trader in discovered_traders:
            web, ph, em = enrich_trader_details_via_gemini(trader["trader_name"])
            trader["website"] = web
            trader["phone"] = ph
            trader["email"] = em
            time.sleep(1.5)
            
    # Persist Exporters (Suppliers) & Importers (Buyers) to MySQL
    save_customs_records_to_db(discovered_traders, importers_list, part_number)
    
    return discovered_traders

def enrich_trader_details_via_gemini(trader_name):
    """
    Queries Gemini with Google Search grounding to retrieve missing contact info.
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
                            f"electronic component exporter: '{trader_name}'. "
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

def save_customs_records_to_db(exporters, importers, part_number, global_hsn_code="85423200"):
    """
    Saves exporters as ComponentTrader and links them.
    Saves importers as Company (leads) to feed the buyer leads crawl queue.
    """
    session = db_session()
    traders_saved = 0
    importers_saved = 0
    
    try:
        # 1. Log Exporters
        for record in exporters:
            name = record.get("trader_name").strip()
            website = record.get("website") or "N/A"
            phone = record.get("phone") or "N/A"
            email = record.get("email") or "N/A"
            
            trader = session.query(ComponentTrader).filter(ComponentTrader.trader_name == name).first()
            if not trader:
                trader = ComponentTrader(
                    trader_name=name,
                    website=website,
                    phone=phone,
                    email=email,
                    trader_type="Customs Exporter"
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
                
        # 2. Log Importers as Company (Buyer leads queue)
        for name in importers:
            name = name.strip()
            company = session.query(Company).filter(Company.company_name == name).first()
            if not company:
                company = Company(
                    company_name=name,
                    crawl_status="pending",
                    website="N/A",
                    company_description="Imported lead from global customs manifest logs."
                )
                session.add(company)
                importers_saved += 1
                
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[-] Database sync failed: {e}")
        raise
    finally:
        session.close()
        
    print(f"[+] Customs DB Sync completed. Saved {traders_saved} exporters and {importers_saved} importer leads.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Customs Manifest shipment exporter crawler")
    parser.add_argument("component", type=str, nargs="?", default="CYW20706", help="Component Part Number (default: CYW20706)")
    args = parser.parse_args()
    
    print(f"=== Starting Customs Manifest Harvester for: {args.component} ===")
    records = harvest_customs_manifests(args.component)
    print("=== Customs Manifest Harvester Completed ===")
