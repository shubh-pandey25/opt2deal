import json
import os
import argparse
from typing import Dict, List, Any
from config import get_groq_client, DEFAULT_MODEL

CACHE_FILE = "lead_relevance_cache.json"

def load_cache() -> Dict[str, Any]:
    """Loads the local query-relevance cache."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cache(cache: Dict[str, Any]):
    """Saves the local query-relevance cache."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[-] Failed to save cache: {e}")

def match_lead_with_llm(client, model: str, lead: Dict[str, Any], query: str) -> Dict[str, Any]:
    """Queries the LLM to check relevance of a lead for a specific stock query."""
    company_name = lead.get("company_name", "Unknown")
    hsn_desc = ", ".join(lead.get("target_hsn_descriptions", []))
    company_desc = lead.get("company_description", "N/A")
    offerings = json.dumps(lead.get("offerings", []))

    prompt = f"""
You are an expert sales intelligence assistant.
Determine how relevant this company is as a potential buyer/customer for the following component or stock item:
Stock Product Query: "{query}"

Company Details:
- Name: {company_name}
- Industry classifications (HSN descriptions): {hsn_desc}
- Website description: {company_desc}
- Offerings listed on website: {offerings}

Analyze the connection:
1. Relevance: Assess if they purchase or use this component type. E.g.:
   - Sreeaidc / Siramech do data center civil/cabling installations, so they buy Ethernet/power cables (relevance: 9/10), but not raw memory ICs (relevance: 0/10).
   - A server manufacturer builds servers, so they buy enterprise SSDs and DDR4 RAM (relevance: 10/10).
   - An automotive electronics manufacturer buys NOR Flash or Bluetooth modules (relevance: 8/10).
2. Rate the relevance on a scale of 0 (not relevant at all) to 10 (direct buyer / highly relevant).
3. Provide a clear one-sentence justification/rationale.

Respond strictly in JSON format with exactly two keys:
{{
  "score": <integer from 0 to 10>,
  "rationale": "<your justification text>"
}}
"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {
            "score": 0,
            "rationale": f"LLM matching failed: {e}"
        }

def run_query(query: str, input_file: str = "scraped_active_leads.json", min_score: int = 5):
    """Filters scraped leads based on relevance to the user's dynamic search query."""
    if not os.path.exists(input_file):
        # Fallback to test file if standard batch output isn't found
        if input_file == "scraped_active_leads.json" and os.path.exists("scraped_active_leads_10_test.json"):
            input_file = "scraped_active_leads_10_test.json"
        else:
            print(f"[-] Input file not found: {input_file}")
            return

    print(f"[+] Reading leads from '{input_file}'...")
    with open(input_file, "r", encoding="utf-8") as f:
        leads = json.load(f)

    cache = load_cache()
    client = get_groq_client()

    # Normalize the query string for consistent cache key mapping
    normalized_query = query.strip().lower()
    if normalized_query not in cache:
        cache[normalized_query] = {}

    query_cache = cache[normalized_query]
    matched_results = []
    new_queries_count = 0

    print(f"[+] Evaluating {len(leads)} leads for component query: '{query}'...")

    for lead in leads:
        # Use CIN as unique key, fall back to company name
        key = lead.get("cin_number") or lead.get("company_name")
        if not key:
            continue

        # Check local cache first to avoid repetitive LLM costs and delays
        if key in query_cache:
            match_data = query_cache[key]
        else:
            match_data = match_lead_with_llm(client, DEFAULT_MODEL, lead, query)
            query_cache[key] = match_data
            new_queries_count += 1

        score = match_data.get("score", 0)
        rationale = match_data.get("rationale", "N/A")

        if score >= min_score:
            lead_result = dict(lead)
            lead_result["relevance_score"] = score
            lead_result["relevance_rationale"] = rationale
            matched_results.append(lead_result)

    # Save the cache if we performed any new lookups
    if new_queries_count > 0:
        print(f"[+] Performed {new_queries_count} new LLM evaluations and updated the local cache.")
        save_cache(cache)
    else:
        print("[+] All matches resolved directly from the local cache file (zero LLM calls made).")

    # Sort results with highest relevance score first
    matched_results.sort(key=lambda x: x["relevance_score"], reverse=True)

    print(f"\n[+] Found {len(matched_results)} matches (Relevance Score >= {min_score}):\n")
    for r in matched_results:
        print(f"==================================================")
        print(f"Company:  {r['company_name']}")
        print(f"Website:  {r.get('website', 'N/A')}")
        print(f"Score:    {r['relevance_score']}/10")
        print(f"Why:      {r['relevance_rationale']}")
        print(f"Contacts: Emails: {r.get('emails', [])} | Phones: {r.get('phones', [])}")
        print(f"==================================================\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Filter leads dynamically based on stock items using a cached LLM.")
    parser.add_argument("query", type=str, help="Stock item to search/filter (e.g., 'ethernet cables', 'RAM', 'NOR Gate')")
    parser.add_argument("--input", type=str, default="scraped_active_leads.json", help="Input scraped leads file")
    parser.add_argument("--min-score", type=int, default=5, help="Minimum relevance score to qualify (0-10)")
    
    args = parser.parse_args()
    run_query(query=args.query, input_file=args.input, min_score=args.min_score)
