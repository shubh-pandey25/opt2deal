import json
import os

def deduplicate_leads(file_path="all_active_leads.json"):
    if not os.path.exists(file_path):
        print(f"[-] File not found: {file_path}")
        return

    print(f"[+] Reading leads from '{file_path}'...")
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            leads = json.load(f)
        except Exception as e:
            print(f"[-] Error parsing JSON: {e}")
            return

    print(f"[+] Total raw records: {len(leads)}")
    
    grouped_leads = {}
    
    for lead in leads:
        # Use CIN as the unique identifier; fallback to company name
        key = lead.get("cin_number") or lead.get("company_name")
        if not key:
            continue
            
        # Extract HSN markets and descriptions (handle both raw and grouped inputs)
        hsn_list = lead.get("target_hsn_markets")
        if hsn_list is None:
            hsn = lead.get("target_hsn_market")
            hsn_list = [hsn] if hsn else []
            
        hsn_desc_list = lead.get("target_hsn_descriptions")
        if hsn_desc_list is None:
            hsn_desc = lead.get("target_hsn_description")
            hsn_desc_list = [hsn_desc] if hsn_desc else []
            
        # Extract NIC codes (handle both raw and grouped inputs)
        nic_list = lead.get("industry_nic_codes")
        if nic_list is None:
            nic = lead.get("industry_nic_code")
            nic_list = [nic] if nic else []
        
        if key not in grouped_leads:
            # Create a base record and convert HSN and NIC fields to lists
            base_record = dict(lead)
            
            # Clean up old single/plural keys to maintain clean schema
            base_record.pop("target_hsn_market", None)
            base_record.pop("target_hsn_description", None)
            base_record.pop("industry_nic_code", None)
            
            # Initialize list fields
            base_record["target_hsn_markets"] = list(hsn_list)
            base_record["target_hsn_descriptions"] = list(hsn_desc_list)
            base_record["industry_nic_codes"] = list(nic_list)
            
            grouped_leads[key] = base_record
        else:
            # Merge fields into lists if not already present
            record = grouped_leads[key]
            for hsn_val in hsn_list:
                if hsn_val not in record["target_hsn_markets"]:
                    record["target_hsn_markets"].append(hsn_val)
            for desc_val in hsn_desc_list:
                if desc_val not in record["target_hsn_descriptions"]:
                    record["target_hsn_descriptions"].append(desc_val)
            for nic_val in nic_list:
                if nic_val not in record["industry_nic_codes"]:
                    record["industry_nic_codes"].append(nic_val)

    deduplicated_list = list(grouped_leads.values())
    print(f"[+] Total unique companies: {len(deduplicated_list)}")
    
    # Save a backup of the original raw file
    backup_path = file_path.replace(".json", "_raw.json")
    try:
        os.rename(file_path, backup_path)
        print(f"[+] Saved original raw data to '{backup_path}'")
    except Exception as e:
        print(f"[!] Warning: Could not create backup: {e}")
        
    # Write the clean deduplicated data back
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(deduplicated_list, indent=2, fp=f)
    print(f"[+] Successfully wrote grouped data back to '{file_path}'")

if __name__ == "__main__":
    deduplicate_leads()
