import os
import sys
import time
import random
import re
import json
import argparse
import httpx
from bs4 import BeautifulSoup

# Standard browser headers to ensure the requests go through smoothly
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5"
}

# Broader Wikipedia categories containing semiconductor and component manufacturers
WIKIPEDIA_CATEGORIES = [
    "Category:Semiconductor_companies_of_the_United_States",
    "Category:Semiconductor_companies_of_Japan",
    "Category:Semiconductor_companies_of_Taiwan",
    "Category:Semiconductor_companies_of_South_Korea",
    "Category:Semiconductor_companies_of_Germany",
    "Category:Semiconductor_companies_of_China",
    "Category:Semiconductor_companies_of_the_United_Kingdom",
    "Category:Semiconductor_companies_of_France",
    "Category:Electronic_component_manufacturers"
]

# Static top-tier semiconductor/electronic component manufacturers as ultimate fallback
FALLBACK_MANUFACTURERS = [
    "Intel", "AMD", "NVIDIA", "Texas Instruments", "STMicroelectronics", 
    "Infineon Technologies", "Microchip Technology", "Analog Devices", 
    "NXP Semiconductors", "Samsung Semiconductor", "TSMC", "Qualcomm", 
    "Broadcom", "Renesas Electronics", "ON Semiconductor", "Micron Technology",
    "TE Connectivity", "Molex", "Amphenol", "JST", "Hirose", "Phoenix Contact",
    "Murata Manufacturing", "TDK", "Yageo", "Kyocera AVX", "Kemet", "Vishay",
    "Panasonic", "Toshiba", "Sony", "Maxim Integrated", "Cypress Semiconductor",
    "Xilinx", "Altera", "Lattice Semiconductor", "Nordic Semiconductor",
    "Silicon Labs", "Espressif Systems", "Realtek", "MediaTek"
]

def get_mouser_manufacturers():
    """Extracts the list of distinct company names from Mouser's public manufacturer index."""
    url = "https://www.mouser.com/ManufacturerList/"
    print(f"[*] Fetching distinct company names from {url}...")
    
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            response = client.get(url)
            
            # Check if blocked by DataDome / Akamai
            if "Access to this page has been denied" in response.text or "captcha-delivery" in response.text:
                print("[!] Mouser request blocked by DataDome CAPTCHA/Akamai. Falling back to other sources...")
                return []
                
            if response.status_code != 200:
                print(f"[!] Failed to fetch manufacturer list. Status code: {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            companies = set()
            
            for link in soup.select("div.manufacturer-list a, ul.manufacturer-list-group a"):
                name = link.text.strip()
                if name and not name.startswith(('All', 'View')):
                    companies.add(name)
            
            if not companies:
                for link in soup.find_all('a', href=re.compile(r'/Manufacturer/')):
                    name = link.text.strip()
                    if name:
                        companies.add(name)
                        
            print(f"[+] Found {len(companies)} distinct companies from Mouser.")
            return sorted(list(companies))
            
    except Exception as e:
        print(f"[!] Error reading Mouser Manufacturer List: {e}")
        return []

def get_wikipedia_manufacturers(limit_per_category=40):
    """Fetches semiconductor companies from Wikipedia's Category Members API."""
    print("[*] Fetching company names from Wikipedia category members API...")
    companies = set()
    url = "https://en.wikipedia.org/w/api.php"
    
    # Wikipedia API policy requires a descriptive User-Agent
    wiki_headers = {
        "User-Agent": "CompanyLogoCrawler/1.0 (contact: info@example.com)"
    }
    
    for category in WIKIPEDIA_CATEGORIES:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmtype": "page",
            "cmlimit": str(limit_per_category),
            "format": "json"
        }
        try:
            r = httpx.get(url, params=params, headers=wiki_headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                members = data.get("query", {}).get("categorymembers", [])
                for m in members:
                    title = m.get("title", "")
                    # Filter out helper pages and lists
                    if title and not any(x in title.lower() for x in ["list of", "semiconductor consolidation", "sector"]):
                        companies.add(title)
        except Exception as e:
            print(f"[-] Error fetching category {category} from Wikipedia: {e}")
            
    print(f"[+] Found {len(companies)} distinct companies from Wikipedia.")
    return sorted(list(companies))

def get_local_db_manufacturers():
    """Extracts distinct manufacturer names from the local project database if available."""
    print("[*] Attempting to fetch manufacturer names from local project database...")
    try:
        # Add local workspace path to sys.path to import database module
        sys.path.append(os.path.abspath(os.path.dirname(__file__)))
        from database import get_session, ComponentAnalysis
        from sqlalchemy import select
        
        companies = set()
        with get_session() as session:
            mfrs = session.scalars(select(ComponentAnalysis.manufacturer).distinct()).all()
            for m in mfrs:
                if m and m.lower() != "unknown":
                    companies.add(m)
        print(f"[+] Found {len(companies)} distinct manufacturers in local database.")
        return sorted(list(companies))
    except Exception as e:
        # Database module not found or connection not configured
        print(f"[-] Local database retrieval skipped or not configured: {e}")
        return []

def get_combined_companies():
    """Combines company lists from Mouser, Wikipedia, local DB, and fallback lists."""
    companies = set()
    
    # 1. Local database
    db_companies = get_local_db_manufacturers()
    companies.update(db_companies)
    
    # 2. Wikipedia API
    wiki_companies = get_wikipedia_manufacturers()
    companies.update(wiki_companies)
    
    # 3. Mouser scraping (if not blocked)
    mouser_companies = get_mouser_manufacturers()
    companies.update(mouser_companies)
    
    # 4. If still empty, load fallback list
    if not companies:
        print("[!] No companies found from active sources. Loading static fallback list...")
        companies.update(FALLBACK_MANUFACTURERS)
    else:
        # Always inject the top tier to ensure they are represented
        companies.update(FALLBACK_MANUFACTURERS)
        
    # Clean up company names (e.g. remove trailing parentheticals from wiki titles)
    cleaned_companies = set()
    for name in companies:
        # Remove anything in parentheses, e.g. "Ambarella Inc. (company)" -> "Ambarella Inc."
        cleaned = re.sub(r'\s*\([^)]*\)', '', name).strip()
        if cleaned:
            cleaned_companies.add(cleaned)
            
    print(f"[+] Final aggregated list has {len(cleaned_companies)} distinct companies.")
    return sorted(list(cleaned_companies))

def fetch_logo_via_ddg_library(company_name):
    """Uses the unauthenticated DuckDuckGo Images search library to get the logo URL."""
    query = f"{company_name} company logo transparent png"
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=3))
            if results:
                # Prefer PNG images
                for r in results:
                    img_url = r.get("image")
                    if img_url and img_url.lower().endswith(".png"):
                        return img_url
                # Fallback to first result
                return results[0].get("image")
    except Exception:
        pass
    return None

def fetch_logo_via_ddg_scrape(company_name):
    """Fallback method scraping DuckDuckGo's HTML search backend directly."""
    query = f"{company_name} company logo transparent png"
    url = "https://html.duckduckgo.com/html/"
    data = {'q': query}
    
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=10) as client:
            response = client.post(url, data=data)
            if response.status_code != 200:
                return None
                
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look through results for domains likely containing raw brand assets
            for result in soup.select('.result__url'):
                raw_url = result.text.strip()
                if "wikipedia" in raw_url or "wikimedia" in raw_url or "vectorlogo" in raw_url:
                    return f"https://{raw_url}"
            
            # Guess using domain & Clearbit
            first_result = soup.select_one('.result__url')
            if first_result:
                domain = first_result.text.strip().split('/')[0]
                if domain:
                    return f"https://logo.clearbit.com/{domain}"
    except Exception:
        pass
    return None

def guess_clearbit_logo_url(company_name):
    """Generates guessed domain logo URLs using Clearbit's free endpoint."""
    name = company_name.lower()
    # Strip common suffixes
    for suffix in [" technologies", " technology", " semiconductor", " semiconductors", " electronics", " corporation", " incorporated", " group", " corp", " inc", " ltd", " gmbh", " co", " sa"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
        name = name.replace(suffix + " ", " ")
    
    # Strip non-alphanumeric except spaces
    name = "".join(c for c in name if c.isalnum() or c.isspace()).strip()
    domain_base = name.replace(" ", "")
    
    # Common mappings
    mappings = {
        "texasinstruments": "ti.com",
        "stmicroelectronics": "st.com",
        "analogdevices": "analog.com",
        "advancedmicrodevices": "amd.com",
        "samsungsemiconductor": "samsung.com"
    }
    
    domain = mappings.get(domain_base, f"{domain_base}.com")
    return f"https://logo.clearbit.com/{domain}"

def fetch_free_logo_url(company_name):
    """Orchestrates logo fetching using library search, web scraping, and domain guessing."""
    # 1. Try DuckDuckGo Search Library (Fastest, returns direct images)
    logo_url = fetch_logo_via_ddg_library(company_name)
    if logo_url:
        return logo_url
        
    # 2. Try scraping DuckDuckGo HTML backend
    logo_url = fetch_logo_via_ddg_scrape(company_name)
    if logo_url:
        return logo_url
        
    # 3. Fallback to guessed Clearbit logo URL
    return guess_clearbit_logo_url(company_name)

def main():
    parser = argparse.ArgumentParser(description="Automated Company Logo Discovery Script")
    parser.add_argument("--limit", type=int, default=50, help="Limit the number of companies to process for testing")
    parser.add_argument("--output", type=str, default="company_logos.json", help="Path to save mapping JSON file")
    args = parser.parse_args()

    print("=" * 60)
    print("      AUTOMATED COMPANY & LOGO DISCOVERY SERVICE (FREE APIs)")
    print("=" * 60)

    # 1. Fetch distinct company names
    companies = get_combined_companies()
    
    if not companies:
        print("[!] No company names could be resolved. Aborting.")
        return

    # Slice list if limit is set
    processing_list = companies
    if args.limit and args.limit > 0:
        processing_list = companies[:args.limit]
        print(f"[*] Limiting run to first {args.limit} companies for testing.")

    print(f"\n[*] Starting logo discovery workflow for {len(processing_list)} companies...")
    results = []
    
    for idx, company in enumerate(processing_list):
        print(f"[{idx+1}/{len(processing_list)}] Resolving logo for: {company}...")
        
        logo_url = fetch_free_logo_url(company)
        
        if logo_url:
            print(f"    -> Found: {logo_url}")
            results.append({"company": company, "logo_url": logo_url})
        else:
            print(f"    -> [No logo resolved]")
            results.append({"company": company, "logo_url": None})
            
        # Throttling to respect rate limits on public search structures
        time.sleep(random.uniform(1.0, 2.0))

    # Save to JSON
    try:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4)
        print(f"\n[+] Saved mappings for {len(results)} companies to {args.output}")
    except Exception as e:
        print(f"\n[!] Error saving JSON output: {e}")

    print("=" * 60)
    print("                      PROCESS COMPLETED")
    print("=" * 60)

if __name__ == "__main__":
    main()
