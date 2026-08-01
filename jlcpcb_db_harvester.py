import os
import sys
import time
import json
import urllib.request
import sqlite3
from typing import Dict, Any, List, Tuple
import argparse

# Load database configurations
try:
    from database import db_session, Component, ComponentTrader, TraderInventoryJunction, ComponentAnalysis
    from sqlalchemy.dialects.mysql import insert as mysql_insert
except ImportError:
    # Fallback/mock imports for standalone testing
    print("[!] Warning: Could not import database session from local project. Running in standalone mode.")
    db_session = None

DB_URL = "https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-parts.sqlite"
DEFAULT_DB_PATH = "jlcpcb-parts.sqlite"
STATUS_FILE = "jlcpcb_download_status.json"

def get_status() -> Dict[str, Any]:
    """Reads the current download status from the status JSON file."""
    if not os.path.exists(STATUS_FILE):
        return {
            "status": "not_downloaded",
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "progress_percent": 0.0,
            "speed_mbps": 0.0,
            "eta_seconds": 0,
            "error": None
        }
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "error", "error": "Failed to read status file"}

def save_status(status_dict: Dict[str, Any]):
    """Saves the download status to the status JSON file."""
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status_dict, f, indent=2)
    except Exception as e:
        print(f"[-] Failed to write status file: {e}")

def download_database(db_path: str = DEFAULT_DB_PATH, force: bool = False) -> bool:
    """Downloads the JLCPCB parts database with streaming progress updates."""
    if os.path.exists(db_path) and not force:
        print(f"[+] Local SQLite database already exists at {db_path}.")
        # Update status file to ready
        size_bytes = os.path.getsize(db_path)
        save_status({
            "status": "ready",
            "downloaded_bytes": size_bytes,
            "total_bytes": size_bytes,
            "progress_percent": 100.0,
            "speed_mbps": 0.0,
            "eta_seconds": 0,
            "error": None
        })
        return True

    print(f"[*] Starting download from {DB_URL} to {db_path}...")
    temp_path = db_path + ".tmp"
    
    status = {
        "status": "downloading",
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "progress_percent": 0.0,
        "speed_mbps": 0.0,
        "eta_seconds": 0,
        "error": None
    }
    save_status(status)

    try:
        req = urllib.request.Request(
            DB_URL, 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            total_size = int(response.info().get('Content-Length', 0))
            status["total_bytes"] = total_size
            save_status(status)

            downloaded = 0
            block_size = 1024 * 1024  # 1 MB blocks
            start_time = time.time()
            last_update_time = start_time
            last_downloaded = 0

            with open(temp_path, "wb") as out_file:
                while True:
                    buffer = response.read(block_size)
                    if not buffer:
                        break
                    out_file.write(buffer)
                    downloaded += len(buffer)
                    
                    # Update status at most every 0.5 seconds
                    now = time.time()
                    if now - last_update_time >= 0.5 or downloaded == total_size:
                        duration = now - start_time
                        interval_duration = now - last_update_time
                        
                        # Speed calculations
                        interval_bytes = downloaded - last_downloaded
                        speed_mbps = (interval_bytes * 8) / (interval_duration * 1024 * 1024) if interval_duration > 0 else 0.0
                        
                        percent = (downloaded / total_size) * 100 if total_size > 0 else 0.0
                        eta = (total_size - downloaded) / (downloaded / duration) if downloaded > 0 and duration > 0 else 0
                        
                        status.update({
                            "downloaded_bytes": downloaded,
                            "progress_percent": round(percent, 2),
                            "speed_mbps": round(speed_mbps, 2),
                            "eta_seconds": int(eta)
                        })
                        save_status(status)
                        
                        # Command Line Progress Output
                        sys.stdout.write(
                            f"\rDownloading: {percent:.2f}% | "
                            f"{downloaded / (1024*1024):.1f}/{total_size / (1024*1024):.1f} MB | "
                            f"Speed: {speed_mbps:.2f} Mbps | "
                            f"ETA: {int(eta)}s"
                        )
                        sys.stdout.flush()
                        
                        last_update_time = now
                        last_downloaded = downloaded

            print("\n[+] Download completed successfully.")
            
        if os.path.exists(db_path):
            os.remove(db_path)
        os.rename(temp_path, db_path)
        
        status.update({
            "status": "ready",
            "progress_percent": 100.0,
            "speed_mbps": 0.0,
            "eta_seconds": 0
        })
        save_status(status)
        return True

    except Exception as e:
        print(f"\n[-] Download failed: {e}")
        status.update({
            "status": "failed",
            "error": str(e)
        })
        save_status(status)
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False

def get_db_columns(db_path: str) -> List[str]:
    """Inspects the SQLite schema of the components table dynamically."""
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(components)")
        columns = [row[1] for row in cursor.fetchall()]
        conn.close()
        return columns
    except Exception as e:
        print(f"[-] Failed to inspect SQLite columns: {e}")
        return []

def query_jlcpcb_components(
    db_path: str = DEFAULT_DB_PATH,
    search_keyword: str = "",
    manufacturer: str = "",
    min_stock: int = 1000,
    category: str = "",
    limit: int = 50,
    offset: int = 0
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Queries the SQLite components database, handling dynamic schemas.
    Returns a list of matching components and the total match count.
    """
    if not os.path.exists(db_path):
        return [], 0
        
    columns = get_db_columns(db_path)
    if not columns:
        return [], 0
        
    # Dynamically map parameters based on column existence
    part_col = "lcsc_part" if "lcsc_part" in columns else ("part_number" if "part_number" in columns else columns[0])
    mfr_col = "manufacturer" if "manufacturer" in columns else None
    stock_col = "stock_count" if "stock_count" in columns else ("stock" if "stock" in columns else None)
    
    # Category columns
    cat_1_col = "first_category" if "first_category" in columns else None
    cat_2_col = "second_category" if "second_category" in columns else None
    
    # Description column
    desc_col = "description" if "description" in columns else ("title" if "title" in columns else None)
    
    # Price column
    price_col = "price" if "price" in columns else None
    
    # Package/footprint column
    pkg_col = "package" if "package" in columns else None
    
    # Build query fields
    fields = [part_col]
    if mfr_col: fields.append(mfr_col)
    if cat_1_col: fields.append(cat_1_col)
    if cat_2_col: fields.append(cat_2_col)
    if stock_col: fields.append(stock_col)
    if desc_col: fields.append(desc_col)
    if price_col: fields.append(price_col)
    if pkg_col: fields.append(pkg_col)
    
    fields_sql = ", ".join(fields)
    
    # Build WHERE clauses
    where_clauses = []
    params = []
    
    if search_keyword:
        clauses = []
        if part_col:
            clauses.append(f"{part_col} LIKE ?")
            params.append(f"%{search_keyword}%")
        if desc_col:
            clauses.append(f"{desc_col} LIKE ?")
            params.append(f"%{search_keyword}%")
        where_clauses.append(f"({' OR '.join(clauses)})")
        
    if manufacturer and mfr_col:
        where_clauses.append(f"{mfr_col} LIKE ?")
        params.append(f"%{manufacturer}%")
        
    if min_stock > 0 and stock_col:
        where_clauses.append(f"{stock_col} >= ?")
        params.append(min_stock)
        
    if category:
        clauses = []
        if cat_1_col:
            clauses.append(f"{cat_1_col} LIKE ?")
            params.append(f"%{category}%")
        if cat_2_col:
            clauses.append(f"{cat_2_col} LIKE ?")
            params.append(f"%{category}%")
        where_clauses.append(f"({' OR '.join(clauses)})")
        
    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Get total count
    count_query = f"SELECT COUNT(*) FROM components {where_sql}"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()[0]
    
    # 2. Get rows
    query = f"SELECT {fields_sql} FROM components {where_sql} LIMIT ? OFFSET ?"
    cursor.execute(query, params + [limit, offset])
    rows = cursor.fetchall()
    
    conn.close()
    
    results = []
    for row in rows:
        item = {}
        for idx, field in enumerate(fields):
            # Map columns back to uniform standard keys
            key = field
            if field == part_col:
                key = "part_number"
            elif field == stock_col:
                key = "stock"
            elif field == desc_col:
                key = "description"
            elif field == cat_1_col:
                key = "first_category"
            elif field == cat_2_col:
                key = "second_category"
                
            item[key] = row[idx]
        results.append(item)
        
    return results, total_count

def sync_components_to_mysql(
    components_list: List[Dict[str, Any]], 
    hsn_code: str = "85423200"
) -> int:
    """
    Ingests and maps component list to the MySQL schema.
    Returns the number of successfully imported/synced elements.
    """
    if not components_list:
        print("[*] No components provided to sync.")
        return 0
        
    if not db_session:
        print("[-] MySQL session is unavailable. Skipping DB ingestion.")
        return 0
        
    session = db_session()
    imported_count = 0
    
    try:
        # 1. Ensure the JLCPCB/LCSC supplier is in component_traders
        jlc_trader = session.query(ComponentTrader).filter(
            ComponentTrader.trader_name == "JLCPCB / LCSC Electronics"
        ).first()
        
        if not jlc_trader:
            jlc_trader = ComponentTrader(
                trader_name="JLCPCB / LCSC Electronics",
                website="https://jlcpcb.com",
                phone="+86 755 8320 0000",
                email="support@jlcpcb.com",
                trader_type="Authorized Distributor"
            )
            session.add(jlc_trader)
            session.flush() # Populate trader_id
        
        for item in components_list:
            part_no = item.get("part_number")
            if not part_no:
                continue
                
            mfr = item.get("manufacturer", "Unknown")
            cat_1 = item.get("first_category", "")
            cat_2 = item.get("second_category", "")
            comp_type = cat_2 if cat_2 else (cat_1 if cat_1 else "Electronic Component")
            desc = item.get("description", "N/A")
            stock = item.get("stock", 0)
            price = item.get("price", "N/A")
            pkg = item.get("package", "N/A")
            
            # Upsert into components table
            comp = session.query(Component).filter(Component.component_id == part_no).first()
            if not comp:
                comp = Component(
                    component_id=part_no,
                    component_type=comp_type,
                    manufacturer=mfr
                )
                session.add(comp)
            else:
                comp.component_type = comp_type
                comp.manufacturer = mfr
                
            # Upsert into trader_inventory_junction
            junction = session.query(TraderInventoryJunction).filter(
                TraderInventoryJunction.trader_id == jlc_trader.trader_id,
                TraderInventoryJunction.component_part_number == part_no
            ).first()
            
            if not junction:
                junction = TraderInventoryJunction(
                    trader_id=jlc_trader.trader_id,
                    component_part_number=part_no,
                    global_hsn_code=hsn_code
                )
                session.add(junction)
                
            # Optional: Save a simplified specs payload in component_analyses for frontend metadata mapping
            analysis = session.query(ComponentAnalysis).filter(ComponentAnalysis.part_number == part_no).first()
            specs_payload = {
                "component_name": part_no,
                "manufacturer": mfr,
                "component_type": comp_type,
                "description": desc,
                "key_parameters": {
                    "package": {"value": pkg, "status": "verified"},
                    "stock_level": {"value": str(stock), "status": "verified"},
                    "unit_price": {"value": str(price), "status": "verified"}
                },
                "standard_alternatives": []
            }
            
            if not analysis:
                import uuid
                analysis_id = str(uuid.uuid4())
                analysis = ComponentAnalysis(
                    id=analysis_id,
                    component_name=f"{mfr} {part_no}",
                    part_number=part_no,
                    manufacturer=mfr,
                    component_type=comp_type,
                    specs=specs_payload,
                    applications=[{
                        "product_hsn": hsn_code,
                        "target_product_family": comp_type,
                        "confidence": "engineering_inference",
                        "technical_fit_defense": f"Component loaded from global master database catalog with in-stock levels of {stock} units."
                    }],
                    report=f"# Engineering Ledger Entry: {part_no}\n\n- **Manufacturer**: {mfr}\n- **Category**: {comp_type}\n- **Package**: {pkg}\n- **Description**: {desc}\n- **Available Stock**: {stock}\n- **Price**: {price}\n",
                    qa_notes="Imported via JLCPCB bulk database sync."
                )
                session.add(analysis)
            else:
                analysis.specs = specs_payload
                
            imported_count += 1
            
        session.commit()
        
    except Exception as e:
        if session:
            session.rollback()
        print(f"[-] MySQL Sync failed: {e}")
        raise e
    finally:
        if session:
            session.close()
            
    print(f"[+] Successfully synced {imported_count} components from JLCPCB SQLite into MySQL.")
    return imported_count

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JLCPCB Parts Database Harvester & Syncer")
    parser.add_argument("--status", action="store_true", help="Display download status of the database")
    parser.add_argument("--download", action="store_true", help="Download/Update local SQLite parts database")
    parser.add_argument("--force", action="store_true", help="Force download database even if it exists")
    parser.add_argument("--query", action="store_true", help="Perform a query search on the SQLite database")
    parser.add_argument("--import-parts", action="store_true", help="Import query results straight to MySQL schema")
    
    parser.add_argument("--keyword", type=str, default="", help="Keyword for query filter")
    parser.add_argument("--mfr", type=str, default="", help="Manufacturer filter")
    parser.add_argument("--category", type=str, default="", help="Category filter")
    parser.add_argument("--min-stock", type=int, default=10000, help="Minimum stock filter (default: 10000)")
    parser.add_argument("--hsn", type=str, default="85423200", help="HSN code to assign during ingestion")
    parser.add_argument("--limit", type=int, default=10, help="Limit output results size")
    
    args = parser.parse_args()
    
    if args.status:
        st = get_status()
        print(json.dumps(st, indent=2))
        
    elif args.download:
        download_database(force=args.force)
        
    elif args.query or args.import_parts:
        if not os.path.exists(DEFAULT_DB_PATH):
            print(f"[-] SQLite database not found at {DEFAULT_DB_PATH}. Please run with --download first.")
            sys.exit(1)
            
        print(f"[*] Querying local database with filters - Keyword: '{args.keyword}', Manufacturer: '{args.mfr}', Category: '{args.category}', Min Stock: {args.min_stock}")
        results, count = query_jlcpcb_components(
            search_keyword=args.keyword,
            manufacturer=args.mfr,
            category=args.category,
            min_stock=args.min_stock,
            limit=args.limit
        )
        print(f"[+] Found {len(results)} matches (Total available: {count})")
        
        for idx, item in enumerate(results, 1):
            print(f"  {idx}. {item.get('part_number')} | {item.get('manufacturer', 'N/A')} | {item.get('second_category', item.get('first_category', 'N/A'))} | Stock: {item.get('stock', 0)}")
            
        if args.import_parts:
            print(f"[*] Syncing matching components to MySQL...")
            sync_components_to_mysql(results, hsn_code=args.hsn)
            
    else:
        parser.print_help()
