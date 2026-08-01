import os
import sys
import json
import argparse
from database import Company, CompanyHsnJunction, CompanyNicJunction, init_db, get_session
from mca_buyer_matcher import validate_and_normalize_hsn

def get_buyer_industry_code(nic_raw):
    if not nic_raw:
        return None
    if str(nic_raw).startswith("NIC_"):
        return str(nic_raw)
    clean_nic = "".join(filter(str.isdigit, str(nic_raw)))
    if len(clean_nic) >= 4:
        return f"NIC_{clean_nic[:4]}"
    elif clean_nic:
        return f"NIC_{clean_nic}"
    return None

def import_raw_leads(leads_list: list) -> int:
    """Ingests raw MCA/NIC leads JSON payload into the MySQL tables."""
    inserted = 0
    with get_session() as session:
        added_hsns = set()
        added_nics = set()
        
        for lead in leads_list:
            cin = lead.get("cin_number")
            if not cin:
                continue
            
            # Check if company already exists
            company = session.query(Company).filter(Company.cin_number == cin).first()
            if not company:
                company = Company(
                    company_name=lead.get("company_name"),
                    website=lead.get("website", "N/A"),
                    cin_number=cin,
                    registration_date=lead.get("registration_date"),
                    registered_office_address=lead.get("registered_office_address"),
                    mca_status=lead.get("status"),
                    state_code=lead.get("state_code"),
                    canonical_url=lead.get("canonical_url", "N/A"),
                    company_description=lead.get("company_description", "N/A"),
                    emails=lead.get("emails", []),
                    phones=lead.get("phones", []),
                    addresses=lead.get("addresses", []),
                    offerings=lead.get("offerings", []),
                    crawl_status=lead.get("crawl_status", "pending"),
                    scraped_at=lead.get("scraped_at"),
                    search_snippets=lead.get("search_snippets"),
                    is_pure_software_only=lead.get("is_pure_software_only"),
                    is_hardware_related=lead.get("is_hardware_related")
                )
                session.add(company)
                session.flush()  # Secure auto-increment company_id immediately
                inserted += 1
            
            # Handle HSN code population
            hsn_val = lead.get("target_hsn_market")
            hsn_list = []
            if hsn_val:
                hsn_list.append(str(hsn_val))
            else:
                raw_hsn_markets = lead.get("target_hsn_markets", [])
                if isinstance(raw_hsn_markets, list):
                    hsn_list.extend(str(h) for h in raw_hsn_markets)
            
            for hsn in hsn_list:
                hsn = validate_and_normalize_hsn(hsn)
                if hsn:
                    hsn_key = (company.company_id, hsn)
                    if hsn_key not in added_hsns:
                        hsn_exists = session.query(CompanyHsnJunction).filter(
                            CompanyHsnJunction.company_id == company.company_id,
                            CompanyHsnJunction.product_hsn == hsn
                        ).first()
                        if not hsn_exists:
                            session.add(CompanyHsnJunction(company_id=company.company_id, product_hsn=hsn))
                            added_hsns.add(hsn_key)

            # Handle NIC code population
            nic_val = lead.get("industry_nic_code")
            nic_list = []
            if nic_val:
                nic_list.append(str(nic_val))
            else:
                raw_nic_codes = lead.get("industry_nic_codes", [])
                if isinstance(raw_nic_codes, list):
                    nic_list.extend(str(n) for n in raw_nic_codes)
                    
            for nic in nic_list:
                nic_code = get_buyer_industry_code(nic)
                if nic_code:
                    nic_key = (company.company_id, nic_code)
                    if nic_key not in added_nics:
                        nic_exists = session.query(CompanyNicJunction).filter(
                            CompanyNicJunction.company_id == company.company_id,
                            CompanyNicJunction.buyer_industry_code == nic_code
                        ).first()
                        if not nic_exists:
                            session.add(CompanyNicJunction(company_id=company.company_id, buyer_industry_code=nic_code))
                            added_nics.add(nic_key)
                            
    return inserted

def import_scraped_leads(scraped_list: list) -> int:
    """Updates already scraped/synthesized leads in the database."""
    updated = 0
    with get_session() as session:
        for item in scraped_list:
            cin = item.get("cin_number")
            if not cin:
                continue
            
            company = session.query(Company).filter(Company.cin_number == cin).first()
            if company:
                company.website = item.get("website") or company.website
                company.canonical_url = item.get("canonical_url") or company.canonical_url
                company.company_description = item.get("company_description") or company.company_description
                company.emails = item.get("emails") or company.emails
                company.phones = item.get("phones") or company.phones
                company.addresses = item.get("addresses") or company.addresses
                company.offerings = item.get("offerings") or company.offerings
                company.crawl_status = item.get("crawl_status") or "synthesized"
                company.is_pure_software_only = item.get("is_pure_software_only") if item.get("is_pure_software_only") is not None else company.is_pure_software_only
                company.is_hardware_related = item.get("is_hardware_related") if item.get("is_hardware_related") is not None else company.is_hardware_related
                updated += 1
    return updated

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone MySQL Bulk Ingestion Script for Lead Pipeline")
    parser.add_argument("--file", type=str, required=True, help="Path to the JSON file to import")
    parser.add_argument("--type", type=str, choices=["raw", "scraped"], default="raw", help="Type of import: 'raw' active leads or 'scraped' finished leads")
    parser.add_argument("--init-db", action="store_true", help="Initialize tables before importing")
    
    args = parser.parse_args()
    
    if args.init_db:
        print("[+] Initializing database schemas...")
        init_db()
        print("[+] Database tables initialized successfully.")
        
    if not os.path.exists(args.file):
        print(f"[-] Error: File not found at '{args.file}'")
        sys.exit(1)
        
    print(f"[+] Reading JSON content from '{args.file}'...")
    try:
        with open(args.file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[-] Error: Failed to parse JSON file: {e}")
        sys.exit(1)
        
    if not isinstance(data, list):
        print("[-] Error: JSON content must be a list of company records.")
        sys.exit(1)
        
    print(f"[+] Loaded {len(data)} records. Starting database ingestion...")
    if args.type == "raw":
        inserted = import_raw_leads(data)
        print(f"[+] Ingestion complete! Imported {inserted} new raw leads into MySQL.")
    else:
        updated = import_scraped_leads(data)
        print(f"[+] Update complete! Updated {updated} scraped leads in MySQL.")
