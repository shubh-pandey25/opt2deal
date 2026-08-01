import os
import sys
import time
import json
import argparse
from dotenv import load_dotenv
from openai import OpenAI
from duckduckgo_search import DDGS
from database import get_session, Company, CompanyHsnJunction, CompanyNicJunction

# Load environment variables
load_dotenv(override=True)

# Setup Gemini client via OpenAI compatibility layer
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()

if not GEMINI_API_KEY:
    print("[-] Error: GEMINI_API_KEY is not configured in your .env file.")
    sys.exit(1)

client = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=GEMINI_API_KEY
)

def search_web(query: str) -> list:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            output = []
            for item in results:
                output.append({
                    "title": item.get("title", ""),
                    "link": item.get("href", ""),
                    "snippet": item.get("body", "")
                })
            return output
    except Exception as e:
        print(f"[-] DuckDuckGo search failed for '{query}': {e}")
        return []

def process_batch(companies_data: list) -> list:
    """
    Sends a batch of companies with snippets to Gemini to classify/synthesize in one call.
    """
    prompt = """
You are an expert market intelligence assistant.
Classify and synthesize structured profiles for the following companies based on their search snippets.

For each company, you must:
1. Synthesize a clean, professional description (what they do, what sector they are in).
2. Extract any products or services they offer.
3. Identify their official website URL (do NOT return directories like zaubacorp.com, tofler.in, tradeindia, indiamart, tradeindia, linkedin, facebook). If none, output "N/A".
4. Extract any emails, phones, and addresses.
5. Set "is_pure_software_only" to true if they only deal in software, SaaS, or IT services with no physical products/hardware.
6. Set "is_hardware_related" to true if they manufacture, assemble, design, or integrate physical electronics, IT hardware (like servers, IoT), embedded systems, switchgears, machinery, or general physical hardware products.

COMPANIES DATA TO PROCESS:
"""
    for entry in companies_data:
        prompt += f"\n---\nCIN: {entry['cin']}\nName: {entry['name']}\nSnippets:\n{json.dumps(entry['snippets'], indent=2)}\n"

    prompt += """
Respond strictly in JSON format matching this schema:
{
  "results": [
    {
      "cin_number": "CIN string matching input",
      "company_name": "Company Name",
      "website": "official URL or N/A",
      "company_description": "description text",
      "is_pure_software_only": true/false,
      "is_hardware_related": true/false,
      "offerings": [
        {"name": "offering name", "description": "offering desc", "type": "product/service"}
      ],
      "emails": ["email1", "email2"],
      "phones": ["phone1", "phone2"],
      "addresses": ["address1"]
    }
  ]
}
"""
    try:
        response = client.chat.completions.create(
            model=GEMINI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        data = json.loads(response.choices[0].message.content)
        return data.get("results", [])
    except Exception as e:
        print(f"[-] Gemini batch classification failed: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(description="Process Leads in Batches using Free Gemini Flash")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of leads to process per LLM request")
    parser.add_argument("--limit", type=int, default=100, help="Total number of leads to process in this run")
    args = parser.parse_args()

    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    
    print(f"[+] Fetching pending leads from MySQL...")
    with get_session() as session:
        pending_leads = (
            session.query(Company)
            .filter(Company.crawl_status == "pending")
            .limit(args.limit)
            .all()
        )
        if not pending_leads:
            print("[+] No pending leads to process!")
            return
        
        leads_list = [{"company_id": c.company_id, "cin": c.cin_number, "name": c.company_name, "snippets": c.search_snippets} for c in pending_leads]
    
    print(f"[+] Loaded {len(leads_list)} pending leads. Beginning processing...")

    # Step 1: Collect Search snippets if missing
    for lead in leads_list:
        if not lead["snippets"] or lead["snippets"] == "[]":
            print(f"[*] Querying DuckDuckGo for: {lead['name']}")
            results = search_web(f"{lead['name']} manufacturing products website")
            lead["snippets"] = results
            # Update search snippets in DB immediately
            with get_session() as session:
                db_lead = session.query(Company).filter(Company.company_id == lead["company_id"]).first()
                if db_lead:
                    db_lead.search_snippets = json.dumps(results)
            time.sleep(1.0) # Search rate limit buffer

    # Step 2: Process in batches of size args.batch_size
    batch_size = args.batch_size
    for i in range(0, len(leads_list), batch_size):
        batch = leads_list[i:i+batch_size]
        print(f"[*] Processing batch {i // batch_size + 1} of {len(leads_list) // batch_size + 1} ({len(batch)} companies)...")
        
        results = process_batch(batch)
        
        # Save results back to MySQL
        with get_session() as session:
            for res in results:
                cin = res.get("cin_number")
                db_lead = session.query(Company).filter(Company.cin_number == cin).first()
                if db_lead:
                    db_lead.website = res.get("website", "N/A")
                    db_lead.company_description = res.get("company_description", "N/A")
                    db_lead.is_pure_software_only = res.get("is_pure_software_only", False)
                    db_lead.is_hardware_related = res.get("is_hardware_related", False)
                    db_lead.offerings = res.get("offerings", [])
                    db_lead.emails = res.get("emails", [])
                    db_lead.phones = res.get("phones", [])
                    db_lead.addresses = res.get("addresses", [])
                    db_lead.crawl_status = "crawled"
                    
                    # Update HSN / NIC junctions if lead is hardware-related
                    if db_lead.is_hardware_related:
                        # Add basic HSN/NIC junctions if present in offerings
                        pass # Junctions were already populated during JSON ingestion
            
            print(f"[+] Successfully saved batch results to database.")
        
        # Jio Gemini Pro Rate Limit sleep (0.5 seconds for high throughput)
        print("[*] Sleeping 0.5 seconds...")
        time.sleep(0.5)

    print("[+] All leads processed successfully!")

if __name__ == "__main__":
    main()
