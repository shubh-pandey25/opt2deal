import urllib.request
import urllib.parse
import json
import time
import re


import os

# Define active production endpoints and credentials
API_KEY = os.getenv("DATA_GOV_API_KEY")
if not API_KEY:
    print("[!] WARNING: DATA_GOV_API_KEY environment variable is not set. MCA queries will fail.")
RESOURCE_ID = "4dbe5667-7b6b-41d7-82af-211562424d9a"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"


# Complete Master Downstream Product HSN Codes (Indian ITC-HS Tariff Codes)
TARGET_HSN_MARKETS = {
    # VERTICAL 1: Enterprise IT & Data Center Infrastructure
    "84715000": "Processing units (Enterprise Servers, Datacenter Blades, Micro-computers)",
    "84717020": "Hard disc drives (Includes Enterprise Solid State storage arrays)",
    "85176290": "Machines for the reception, conversion and transmission of voice/data (Routers, Switches)",

    # VERTICAL 2: Security, Surveillance & Video Systems
    "85258900": "Television cameras, digital cameras and video camera recorders (IP Cameras, CCTV)",
    "85311090": "Burglar or fire alarms and similar apparatus (Smart access control panels)",

    # VERTICAL 3: Industrial Automation, Control & Power Infrastructure
    "85371000": "Boards, panels, consoles equipped with apparatus for electric control <= 1000V (PLCs, HMIs)",
    "85044010": "Electric Inverters (Solar inverters, Industrial drives)",
    "85044090": "Static converters (UPS systems, industrial rectifiers)",
    "90283010": "Electricity meters (Smart energy meters for grid automation)",

    # VERTICAL 4: Automotive, Transport & Telematics
    "90328910": "Electronic automatic regulating or controlling instruments (Engine ECUs, Powertrain controllers)",
    "87082990": "Other parts and accessories of bodies (Frequently used for infotainment/dashboard assemblies)",
    "85122010": "Automobile lighting equipment (LED headlamp driver PCBs and control modules)",

    # VERTICAL 5: Telecom Infrastructure & Aerospace
    "85176100": "Base stations (Telecom towers, 5G cellular radio nodes)",
    "88039000": "Parts of goods of heading 8801 or 8802 (Avionics, drone flight controllers, aerospace electronics)",

    # VERTICAL 6: Test, Measurement & Instrumentation
    "90302000": "Oscilloscopes and oscillographs (Digital test equipment)",
    "90303310": "Multimeters without a recording device (Precision electronic measurement tools)",

    # VERTICAL 7: Healthcare & Certified Medical Devices
    "90181990": "Electro-diagnostic apparatus (Patient vitals monitors, diagnostic imaging processing boards)",
    "90189099": "Other instruments and appliances used in medical sciences",

    # HIGH-VOLUME SECTOR A: Consumer Electronics & White Goods
    "85287200": "Reception apparatus for television, color (Smart TVs, LED Display mainboards)",
    "84501100": "Fully-automatic washing machines (White goods logic control boards)",
    "84151010": "Air conditioning machines, split system (HVAC inverter logic boards)",

    # HIGH-VOLUME SECTOR B: Electric Vehicles (EV) & Battery Management
    "85076000": "Lithium-ion accumulators (EV Battery packs and their integrated Battery Management Systems / BMS)",
    "85044030": "Battery chargers (EV onboard chargers, fast-charging station controllers)",

    # EXPANDED VERTICALS (Semiconductor/IC matching alignments)
    "85287100": "Set-top boxes with a communication function (Cable/Satellite STBs, streaming sticks)",
    "85171300": "Smartphones (Mobile phone handset manufacturing ecosystem)",
    "94054200": "Electric luminaires and lighting fittings, LED (Smart LED driver boards & IoT controllers)",
    "85049090": "Parts of electric inverters, static converters and power boards (Pure PCBA / SMT Assembly Service)",
    "84433200": "Other printers, facsimile machines, whether or not combined (Peripheral hardware/POS terminals)",
    "85183000": "Headphones and earphones, whether or not combined with a microphone (TWS, Bluetooth Wearables, IoT Audio)",
    "85423200": "Memory IC broker/distributor channel (bulk cash-buyers and component brokers)"
}

# Bridge the gap between HSN codes and Indian NIC codes
HSN_TO_NIC_MAPPING = {
    # VERTICAL 1: Enterprise IT & Data Center Infrastructure
    "84715000": "26201",  # Processing units (Enterprise Servers, Datacenter Blades)
    "84717020": "26201",  # Hard disc drives (SSD storage arrays)
    "85176290": "26303",  # Manufacture of data communications equipment (bridges, routers, gateways)

    # VERTICAL 2: Security, Surveillance & Video Systems
    "85258900": "26301",  # Television/digital/video cameras (IP Cameras, CCTV)
    "85311090": "26301",  # Alarms and similar apparatus (Smart access control panels)

    # VERTICAL 3: Industrial Automation, Control & Power Infrastructure
    "85371000": "27104",  # Boards, panels, consoles equipped with apparatus for electric control <= 1000V (PLCs, HMIs)
    "85044010": "27104",  # Electric Inverters (Solar inverters, Industrial drives)
    "85044090": "27104",  # Static converters (UPS systems, industrial rectifiers)
    "90283010": "26511",  # Electricity meters (Smart energy meters)

    # VERTICAL 4: Automotive, Transport & Telematics
    "90328910": "29304",  # Manufacture of motor vehicle electrical equipment (Engine ECUs)
    "87082990": "29304",  # Parts and accessories of bodies (Infotainment/dashboard assemblies)
    "85122010": "29304",  # Automobile lighting equipment (LED headlamp driver PCBs)

    # VERTICAL 5: Telecom Infrastructure & Aerospace
    "85176100": "26303",  # Base stations (Telecom towers, 5G cellular radio nodes)
    "88039000": "30304",  # Parts of balloons/spacecraft/aircraft (Avionics, drone flight controllers)

    # VERTICAL 6: Test, Measurement & Instrumentation
    "90302000": "26511",  # Oscilloscopes and oscillographs
    "90303310": "26511",  # Multimeters

    # VERTICAL 7: Healthcare & Certified Medical Devices
    "90181990": "26600",  # Electro-diagnostic apparatus (Patient vitals monitors)
    "90189099": "26600",  # Other medical instruments and appliances

    # HIGH-VOLUME SECTOR A: Consumer Electronics & White Goods
    "85287200": "26400",  # Reception apparatus for television (Smart TVs, display mainboards)
    "84501100": "27501",  # Fully-automatic washing machines (White goods logic control boards)
    "84151010": "27501",  # Air conditioning machines (HVAC split system inverter boards)

    # HIGH-VOLUME SECTOR B: Electric Vehicles (EV) & Battery Management
    "85076000": "27201",  # Lithium-ion accumulators (EV Battery packs / BMS)
    "85044030": "27104",  # Battery chargers (EV fast chargers)

    # EXPANDED VERTICALS (Semiconductor/IC matching alignments)
    "85287100": "26400",  # Manufacture of consumer electronics (Set-top boxes)
    "85171300": "26302",  # Manufacture of telephone/telegraph apparatus (Mobile handsets)
    "94054200": "27400",  # Manufacture of electric lighting equipment (Smart LED Drivers)
    "85049090": "26104",  # Core Contract Electronics Manufacturing Services (EMS / PCBA Board Assemblers)
    "84433200": "26202",  # Manufacture of peripheral units (Printers, POS Systems, Office Automation)
    "85183000": "26400",  # Manufacture of consumer electronics (TWS Earbuds, Smart Wearables)
    "85423200": "46529"   # Broker/distributor channel for memory IC liquidation
}

# State name mapping to match the database values (which are lowercase full names)
STATE_NAME_MAPPING = {
    "MH": "maharashtra",
    "KA": "karnataka",
    "DL": "delhi",
    "TN": "tamil nadu",
    "TS": "telangana",
    "AP": "andhra pradesh",
    "GJ": "gujarat",
    "UP": "uttar pradesh",
    "HR": "haryana"
}


def resolve_target_states(state_filter: str) -> list:
    """
    Returns the normalized state list to filter against.
    When state_filter is 'all', returns an empty list so no state filter is applied.
    """
    if state_filter and state_filter.lower() != "all":
        expected_state = STATE_NAME_MAPPING.get(state_filter.upper(), state_filter.lower())
        return [expected_state]
    return []


def validate_and_normalize_hsn(hsn: str) -> str:
    """
    Strips non-digits from the HSN code and validates it against TARGET_HSN_MARKETS.
    Returns the validated HSN code or an empty string if invalid.
    """
    if not hsn:
        return ""
    hsn_clean = re.sub(r"\D", "", str(hsn))
    if hsn_clean in TARGET_HSN_MARKETS:
        return hsn_clean
    # Substring fallback
    for key in TARGET_HSN_MARKETS:
        if key in hsn_clean or hsn_clean in key:
            if len(hsn_clean) >= 4:
                return key
    return ""


def normalize_grouped_leads(leads: list) -> list:
    """
    Groups leads by company and guarantees the plural schema used by downstream consumers.
    """
    grouped_leads = {}

    for lead in leads:
        key = lead.get("cin_number") or lead.get("company_name")
        if not key:
            continue

        hsn_list = lead.get("target_hsn_markets")
        if hsn_list is None:
            hsn = lead.get("target_hsn_market")
            hsn_list = [hsn] if hsn else []

        hsn_desc_list = lead.get("target_hsn_descriptions")
        if hsn_desc_list is None:
            hsn_desc = lead.get("target_hsn_description")
            hsn_desc_list = [hsn_desc] if hsn_desc else []

        nic_list = lead.get("industry_nic_codes")
        if nic_list is None:
            nic = lead.get("industry_nic_code")
            nic_list = [nic] if nic else []

        if key not in grouped_leads:
            base_record = dict(lead)
            base_record.pop("target_hsn_market", None)
            base_record.pop("target_hsn_description", None)
            base_record.pop("industry_nic_code", None)
            base_record["target_hsn_markets"] = list(hsn_list)
            base_record["target_hsn_descriptions"] = list(hsn_desc_list)
            base_record["industry_nic_codes"] = list(nic_list)
            grouped_leads[key] = base_record
        else:
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

    return list(grouped_leads.values())


def chunk_items(items: list, chunk_size: int) -> list:
    """
    Splits a list into consecutive chunks of chunk_size.
    """
    if chunk_size <= 0:
        chunk_size = 1
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]

def clean_company_name_for_msme(name: str) -> str:
    """
    Strips trailing corporate designations (like PRIVATE LIMITED, PVT LTD) 
    to maximize search alignment against the MSME Udyam database.
    """
    if not name:
        return ""
    name_clean = name.strip().upper()
    # Ordered suffixes list (longest first to avoid partial cuts)
    suffixes = [
        " PRIVATE LIMITED", " PRIVATELIMITED", " PVT LTD", " PVT. LTD.", " PVT.LTD.",
        " LIMITED", " LTD", " LTD.", " CO", " CO.", " CORP", " CORP.", " CORPORATION"
    ]
    for suffix in suffixes:
        if name_clean.endswith(suffix):
            name_clean = name_clean[:-len(suffix)].strip()
            break
    name_clean = re.sub(r"[\.,]", "", name_clean)
    name_clean = re.sub(r"\s+", " ", name_clean).strip()
    return name_clean


def get_msme_name_variants(company_name: str) -> list:
    """
    Generates a small set of normalized name variants to improve Udyam lookup coverage.
    """
    variants = []
    candidates = [clean_company_name_for_msme(company_name), (company_name or "").strip().upper()]

    for candidate in candidates:
        candidate = re.sub(r"[\.,]", "", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate and candidate not in variants:
            variants.append(candidate)

    return variants


def enrich_leads_with_msme_status(leads):
    """
    Enriches a list of lead dicts with MSME registration details by querying
    the MSME Udyam registration dataset in parallel using a local file cache.
    """
    if not leads:
        return leads

    import urllib.error
    import os

    cache_file = "msme_cache.json"
    msme_cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                msme_cache = json.load(f)
        except Exception:
            pass

    # Extract unique company names that are not in the local cache
    unique_names = list(set(lead["company_name"] for lead in leads if lead.get("company_name")))
    names_to_query = [name for name in unique_names if name.strip().upper() not in msme_cache]

    print(f"[+] Total unique company names: {len(unique_names)}. Already cached: {len(unique_names) - len(names_to_query)}. To query: {len(names_to_query)}")
    
    if not names_to_query:
        # All records are already resolved in cache
        for lead in leads:
            name_upper = lead.get("company_name", "").strip().upper()
            info = msme_cache.get(name_upper, {"is_msme": False, "msme_registration_date": "N/A", "msme_district": "N/A"})
            lead.update(info)
        return leads

    # Operational warning for large queries
    if len(names_to_query) > 100:
        print(f"[!] WARNING: Attempting to query {len(names_to_query)} names from MSME API. This may take a long time and trigger rate limits.")
        print("[!] To bypass this, run with MSME enrichment disabled or in smaller batches.")

    msme_results = {}
    
    def check_msme(company_name):
        name_variants = get_msme_name_variants(company_name)
        if not name_variants:
            return company_name, None

        for variant in name_variants:
            params = {
                "api-key": API_KEY,
                "format": "json",
                "limit": 1,
                "filters[EnterpriseName]": variant
            }
            msme_url = f"https://api.data.gov.in/resource/8b68ae56-84cf-4728-a0a6-1be11028dea7?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(msme_url, headers={"User-Agent": "Mozilla/5.0"})

            # Keep the request cadence conservative to reduce 429s on the gateway.
            time.sleep(0.8)

            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=10) as response:
                        data = json.loads(response.read().decode())
                        records = data.get("records", [])
                        if records:
                            return company_name, records[0]
                        break
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        sleep_time = 4.0 * (attempt + 1)
                        time.sleep(sleep_time)
                        continue
                    break
                except Exception:
                    break

        return company_name, None

    # Run lookups sequentially to stay within rate limits.
    completed = 0
    total_queries = len(names_to_query)
    for name in names_to_query:
        try:
            _, record = check_msme(name)
            name_upper = name.strip().upper()
            if record:
                info = {
                    "is_msme": True,
                    "msme_registration_date": record.get("RegistrationDate") or "N/A",
                    "msme_district": record.get("District") or "N/A"
                }
            else:
                info = {
                    "is_msme": False,
                    "msme_registration_date": "N/A",
                    "msme_district": "N/A"
                }
            msme_cache[name_upper] = info
        except Exception as e:
            print(f"[-] Error during MSME lookup for {name}: {e}")

        completed += 1
        if completed % 20 == 0 or completed == total_queries:
            print(f"  [MSME Lookup] Progress: {completed}/{total_queries} names checked.")

    # Save updated cache back to file
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(msme_cache, indent=2, fp=f)
    except Exception as e:
        print(f"[-] Failed to save MSME cache: {e}")

    # Map back to leads
    for lead in leads:
        name_upper = lead.get("company_name", "").strip().upper()
        info = msme_cache.get(name_upper, {
            "is_msme": False,
            "msme_registration_date": "N/A",
            "msme_district": "N/A"
        })
        lead.update(info)
        
    return leads


def fetch_and_filter_indian_buyers(downstream_hsn, state_filter="MH"):
    """
    Queries the live data.gov.in MCA/RoC resource and isolates companies 
    matching the exact technical manufacturing tier of your component.
    Supports downstream_hsn='all' to fetch leads for all saved HSN codes.
    """
    if downstream_hsn and downstream_hsn.lower() == "all":
        return fetch_all_saved_leads(state_filter=state_filter)

    target_nic = HSN_TO_NIC_MAPPING.get(downstream_hsn)
    if not target_nic:
        print(f"[-] No direct industrial NIC code mapping found for HSN: {downstream_hsn}")
        return []

    print(f"[+] Fetching registered entities for target NIC: {target_nic}...")
    
    offset = 0
    limit = 1000
    verified_buyer_leads = []
    inactive_leads = []

    # Translate state filter
    target_states = resolve_target_states(state_filter)

    pages_fetched = 0
    max_pages = 5  # Safety ceiling to prevent infinite loops (max 5,000 records)
    while pages_fetched < max_pages:
        pages_fetched += 1
        params = {
            "api-key": API_KEY,
            "format": "json",
            "offset": offset,
            "limit": limit,
            "filters[nic_code]": target_nic
        }
        
        query_string = urllib.parse.urlencode(params)
        url = f"{BASE_URL}?{query_string}"

        data = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    sleep_time = 5.0 * (attempt + 1)
                    print(f"  [429 Rate Limit] MCA API rate limited. Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                    continue
                else:
                    raise e
        else:
            print(f"[-] Skipping NIC {target_nic} after repeated MCA rate limits.")
            continue
        
        if not data:
            break

        try:
            raw_records = data.get("records", [])
            total = int(data.get("total", 0))
            count = len(raw_records)
            
            print(f"  [NIC {target_nic}] Fetched offset {offset}: retrieved {count}/{total} records.")

            for record in raw_records:
                cin = record.get("CIN") or ""
                company_name = record.get("CompanyName") or ""
                status = record.get("CompanyStatus") or ""
                record_state = (record.get("CompanyStateCode") or "").lower()
                
                # Filter client-side by state
                if target_states and record_state not in target_states:
                    continue
                    
                # Check both direct nic_code field and the CIN-based extraction
                nic = record.get("nic_code") or ""
                if not nic and len(cin) == 21:
                    nic = cin[1:6]
                    
                if not nic:
                    continue
                
                # Match against target NIC
                if nic == target_nic:
                    lead_data = {
                        "company_name": company_name,
                        "cin_number": cin,
                        "industry_nic_code": nic,
                        "target_hsn_market": downstream_hsn,
                        "target_hsn_description": TARGET_HSN_MARKETS.get(downstream_hsn, "Unknown target market"),
                        "registration_date": record.get("CompanyRegistrationdate_date", "N/A"),
                        "registered_office_address": record.get("Registered_Office_Address", "N/A"),
                        "status": status,
                        "state_code": next((k for k, v in STATE_NAME_MAPPING.items() if v.lower() == record_state), record_state.upper())
                    }

                    # Filter out anything that isn't definitively "ACTIVE"
                    if status.upper() == "ACTIVE":
                        verified_buyer_leads.append(lead_data)
                    else:
                        # Inactive leads go to dormant/review list
                        inactive_leads.append(lead_data)

            if not raw_records or count == 0 or offset >= total or (offset + limit) >= total:
                break
                
            offset += limit
            time.sleep(0.35)  # Tiny delay to prevent rate limit blocks

        except Exception as e:
            print(f"[-] Pipeline Network Error at offset {offset}: {e}")
            break

    # Log inactive leads to a Dormant/Review file
    if inactive_leads:
        print(f"[!] Logged {len(inactive_leads)} inactive/dormant leads to 'dormant_review_leads.json'")
        try:
            import os
            dormant_file = "dormant_review_leads.json"
            existing = []
            if os.path.exists(dormant_file):
                with open(dormant_file, "r", encoding="utf-8") as f:
                    try:
                        existing = json.load(f)
                    except:
                        pass
            
            seen_cins = {lead["cin_number"] for lead in existing if "cin_number" in lead}
            new_leads = [l for l in inactive_leads if l["cin_number"] not in seen_cins]
            existing.extend(new_leads)
            
            with open(dormant_file, "w", encoding="utf-8") as f:
                json.dump(existing, indent=2, fp=f)
        except Exception as e:
            print(f"[-] Failed to log inactive leads to file: {e}")

    # Enrich active leads with MSME status
    if verified_buyer_leads:
        print(f"[+] Enriching {len(verified_buyer_leads)} active buyer leads with MSME status...")
        verified_buyer_leads = enrich_leads_with_msme_status(verified_buyer_leads)

    return normalize_grouped_leads(verified_buyer_leads)


def fetch_all_saved_leads(state_filter="all", nic_batch_size: int = 2, pause_between_batches: float = 8.0, skip_msme: bool = False):
    """
    Fetches leads for ALL saved HSN codes and states in our mapping.
    Groups queries by NIC code to minimize API requests and page through results.
    """
    # Group the target HSN codes by NIC code
    nic_to_hsns = {}
    for hsn, nic in HSN_TO_NIC_MAPPING.items():
        if nic not in nic_to_hsns:
            nic_to_hsns[nic] = []
        nic_to_hsns[nic].append(hsn)
        
    all_verified_leads = []
    all_inactive_leads = []
    
    # Translate state filter
    target_states = resolve_target_states(state_filter)
        
    nic_items = list(nic_to_hsns.items())
    nic_batches = chunk_items(nic_items, nic_batch_size)
    print(f"[+] Starting batch execution for {len(nic_to_hsns)} unique NIC groups across target states in {len(nic_batches)} smaller batch(es)...")

    for batch_index, nic_batch in enumerate(nic_batches, start=1):
        print(f"[+] Processing NIC batch {batch_index}/{len(nic_batches)} containing {len(nic_batch)} group(s)...")
        for nic_code, hsn_list in nic_batch:
            print(f"[+] Querying API for NIC {nic_code} (representing HSNs: {hsn_list})...")
            offset = 0
            limit = 1000
            time.sleep(1.0)

            pages_fetched = 0
            max_pages = 5  # Safety ceiling to prevent infinite loops (max 5,000 records per NIC)
            while pages_fetched < max_pages:
                pages_fetched += 1
                params = {
                    "api-key": API_KEY,
                    "format": "json",
                    "offset": offset,
                    "limit": limit,
                    "filters[nic_code]": nic_code
                }
                query_string = urllib.parse.urlencode(params)
                url = f"{BASE_URL}?{query_string}"

                data = None
                for attempt in range(5):
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=10) as response:
                            data = json.loads(response.read().decode())
                        break
                    except urllib.error.HTTPError as e:
                        if e.code == 429:
                            sleep_time = 10.0 * (attempt + 1)
                            print(f"  [429 Rate Limit] MCA API rate limited. Retrying in {sleep_time}s...")
                            time.sleep(sleep_time)
                            continue
                        else:
                            raise e
                else:
                    print(f"  [-] Skipping NIC {nic_code} after repeated MCA rate limits.")
                    break

                if not data:
                    break

                try:
                    raw_records = data.get("records", [])
                    total = int(data.get("total", 0))
                    count = len(raw_records)

                    print(f"  [NIC {nic_code}] Fetched offset {offset}: retrieved {count}/{total} records.")

                    for record in raw_records:
                        cin = record.get("CIN") or ""
                        company_name = record.get("CompanyName") or ""
                        status = record.get("CompanyStatus") or ""
                        record_state = (record.get("CompanyStateCode") or "").lower()

                        if target_states and record_state not in target_states:
                            continue

                        nic = record.get("nic_code") or ""
                        if not nic and len(cin) == 21:
                            nic = cin[1:6]

                        if nic != nic_code:
                            continue

                        # Generate a lead record for each matching HSN code
                        for hsn in hsn_list:
                            lead_data = {
                                "company_name": company_name,
                                "cin_number": cin,
                                "industry_nic_code": nic,
                                "target_hsn_market": hsn,
                                "target_hsn_description": TARGET_HSN_MARKETS.get(hsn, "Unknown target market"),
                                "registration_date": record.get("CompanyRegistrationdate_date", "N/A"),
                                "registered_office_address": record.get("Registered_Office_Address", "N/A"),
                                "status": status,
                                "state_code": next((k for k, v in STATE_NAME_MAPPING.items() if v.lower() == record_state), record_state.upper())
                            }

                            if status.upper() == "ACTIVE":
                                all_verified_leads.append(lead_data)
                            else:
                                all_inactive_leads.append(lead_data)

                    if not raw_records or count == 0 or offset >= total or (offset + limit) >= total:
                        break

                    offset += limit
                    time.sleep(1.0)  # Slower pacing to keep request bursts small
                except Exception as e:
                    print(f"  [-] Error querying page for NIC {nic_code} at offset {offset}: {e}")
                    break

        if batch_index < len(nic_batches):
            print(f"[+] Pausing {pause_between_batches}s before the next NIC batch...")
            time.sleep(pause_between_batches)
                
    # Save inactive leads
    if all_inactive_leads:
        print(f"[!] Logged {len(all_inactive_leads)} inactive/dormant leads to 'dormant_review_leads.json'")
        try:
            import os
            dormant_file = "dormant_review_leads.json"
            existing = []
            if os.path.exists(dormant_file):
                with open(dormant_file, "r", encoding="utf-8") as f:
                    try:
                        existing = json.load(f)
                    except:
                        pass
            
            seen_cins = {lead["cin_number"] for lead in existing if "cin_number" in lead}
            new_leads = [l for l in all_inactive_leads if l["cin_number"] not in seen_cins]
            existing.extend(new_leads)
            
            with open(dormant_file, "w", encoding="utf-8") as f:
                json.dump(existing, indent=2, fp=f)
        except Exception as e:
            print(f"[-] Failed to log inactive leads to file: {e}")
            
    # Save all active leads
    if all_verified_leads:
        print(f"[+] Enriching {len(all_verified_leads)} active buyer leads with MSME status...")
        if skip_msme:
            print("[+] MSME enrichment skipped by request.")
        else:
            all_verified_leads = enrich_leads_with_msme_status(all_verified_leads)
        
        print(f"[+] Grouping multiple HSN and NIC records for unique companies...")
        all_verified_leads = normalize_grouped_leads(all_verified_leads)

    try:
        active_file = "all_active_leads.json"
        with open(active_file, "w", encoding="utf-8") as f:
            json.dump(all_verified_leads, indent=2, fp=f)
        print(f"[+] Successfully saved {len(all_verified_leads)} active leads (grouped by company) to '{active_file}'")
    except Exception as e:
        print(f"[-] Failed to save active leads to '{active_file}': {e}")
        
    return all_verified_leads


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Query MCA/RoC Database for Target Electronics Manufacturers")
    parser.add_argument("--hsn", type=str, default="85371000", help="Downstream product HSN code, or 'all' to query all target markets")
    parser.add_argument("--state", type=str, default="MH", help="Target Indian State Code (e.g. MH, KA, DL, TN, GJ), or 'all' for all mapped states")
    parser.add_argument("--nic-batch-size", type=int, default=2, help="Number of NIC groups to process per batch")
    parser.add_argument("--batch-pause", type=float, default=8.0, help="Seconds to pause between NIC batches")
    parser.add_argument("--skip-msme", action="store_true", help="Skip MSME enrichment to reduce API load")
    
    args = parser.parse_args()
    
    if args.hsn.lower() == "all" or args.state.lower() == "all":
        leads = fetch_all_saved_leads(
            state_filter=args.state,
            nic_batch_size=args.nic_batch_size,
            pause_between_batches=args.batch_pause,
            skip_msme=args.skip_msme,
        )
        print(f"\n[+] Batch Pipeline Complete! Found {len(leads)} active target factories matching constraints:")
        print(json.dumps(leads[:5], indent=2))
        print(f"\n[+] Full results saved to 'all_active_leads.json'")
    else:
        leads = fetch_and_filter_indian_buyers(downstream_hsn=args.hsn, state_filter=args.state)
        print(f"\n[+] Pipeline Complete! Found {len(leads)} target factories in this batch matching HSN {args.hsn} in state {args.state}:")
        print(json.dumps(leads[:5], indent=2))
