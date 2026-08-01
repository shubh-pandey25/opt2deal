import warnings
# Target only the specific DuckDuckGo search renaming warning to keep logs clean
# while keeping database, resource, and security warnings active.
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*duckduckgo_search.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*ddgs.*")

from fastapi import FastAPI, HTTPException, Depends, Security, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import os
import uuid

from config import get_groq_client, GROQ_API_KEY, USE_OLLAMA
from orchestrator import InventoryOrchestrator
from scrape_leads_scalable import LeadsDatabase, worker_task
from mca_buyer_matcher import TARGET_HSN_MARKETS, fetch_all_saved_leads, fetch_and_filter_indian_buyers
from database import init_db

import sentry_sdk

sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        traces_sample_rate=1.0,
    )

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    expected_api_key = os.getenv("APP_API_KEY")
    if not expected_api_key:
        return api_key # Allow bypass if not explicitly configured
    if not api_key or api_key != expected_api_key:
        raise HTTPException(status_code=403, detail="Could not validate API KEY")
    return api_key

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the database on startup
    try:
        init_db()
        print("[+] Database initialized successfully on startup.")
    except Exception as e:
        print(f"[-] Warning: Database initialization failed: {e}")
    yield
    # Cleanup on shutdown

app = FastAPI(
    title="Multi-Agent Inventory Application Finder API",
    description="Backend API to analyze electrical components and map application areas using the Groq API.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalysisRequest(BaseModel):
    component_desc: str = Field(..., description="Text description of the component", json_schema_extra={"example": "Crucial P3 1TB NVMe PCIe M.2 SSD"})
    model: Optional[str] = Field(None, description="Optional Groq model selection override")
    refinements: int = Field(1, ge=0, le=3, description="Maximum number of QA audit refinement loops")

class AnalysisResponse(BaseModel):
    id: str = Field(..., description="Unique search and analysis ID")
    success: bool
    original_input: str
    specs: Dict[str, Any]
    applications: List[Dict[str, Any]]
    report: str
    qa_notes: str
    logs: List[str]

@app.get("/health", tags=["System"])
def health_check():
    """Returns the API health status and key configuration checks."""
    return {
        "status": "healthy",
        "ollama_enabled": USE_OLLAMA,
        "api_key_configured": bool(GROQ_API_KEY) if not USE_OLLAMA else True,
        "api_key_prefix": GROQ_API_KEY[:6] + "..." if (GROQ_API_KEY and not USE_OLLAMA) else None
    }

@app.post("/analyze", response_model=AnalysisResponse, tags=["Analysis"], dependencies=[Depends(verify_api_key)])
def analyze_component(request: AnalysisRequest):
    """
    Triggers the multi-agent analysis pipeline for the provided component.
    Runs specifications extraction, application mapping, report synthesis, and QA audit.
    """
    # 1. Generate unique request ID
    request_id = str(uuid.uuid4())

    # 2. Verify Groq client credentials
    try:
        client = get_groq_client()
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    
    # 3. Run Orchestrator Pipeline
    try:
        orchestrator = InventoryOrchestrator(client=client, model=request.model)
        
        # We capture logs to return in response
        execution_logs = [f"Generated unique search ID: {request_id}"]
        def log_cb(msg: str):
            execution_logs.append(msg)
            
        result = orchestrator.run_pipeline(
            user_input=request.component_desc,
            log_callback=log_cb,
            max_refinement_loops=request.refinements,
            run_id=request_id
        )
        
        # Prepend our initial log to the result logs
        result["logs"] = execution_logs + result.get("logs", [])
        
        # Save to database if successful
        if result.get("success", False):
            try:
                db = LeadsDatabase()
                specs_data = result.get("specs") or {}
                
                part_no = specs_data.get("component_name") or request.component_desc
                tech_details = specs_data.get("technical_details") or {}
                mfr_raw = tech_details.get("manufacturer") or specs_data.get("manufacturer") or "Unknown"
                if isinstance(mfr_raw, dict):
                    mfr = mfr_raw.get("value") or "Unknown"
                else:
                    mfr = str(mfr_raw)
                comp_type = specs_data.get("component_type") or "Unknown"
                
                db.save_component_analysis(
                    analysis_id=request_id,
                    component_name=request.component_desc,
                    part_number=part_no,
                    manufacturer=mfr,
                    component_type=comp_type,
                    specs=specs_data,
                    applications=result.get("applications", []),
                    report=result.get("report", ""),
                    qa_notes=result.get("qa_notes", "")
                )
                
                # Run matching
                matches_count = db.match_and_save_leads_for_component(request_id)
                db.close()
                result["logs"].append(f"[Database] Successfully saved analysis and generated {matches_count} matching buyer leads.")
            except Exception as db_err:
                result["logs"].append(f"[Database] Warning: Failed to save to MySQL database: {str(db_err)}")
                
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred during multi-agent pipeline execution: {str(e)}"
        )

class LeadsRequest(BaseModel):
    hsn: str = Field(..., description="Downstream finished product HSN code (or 'all' for all saved codes)", json_schema_extra={"example": "85371000"})
    state: str = Field("MH", description="Target Indian State Code (e.g. MH, KA, DL, TN, or 'all')", json_schema_extra={"example": "MH"})

class BatchLeadsRequest(BaseModel):
    state: str = Field("all", description="Target Indian State Code (e.g. MH, KA, DL, TN, or 'all')", json_schema_extra={"example": "all"})

@app.post("/leads", tags=["MCA Leads"], dependencies=[Depends(verify_api_key)])
def get_buyer_leads(request: LeadsRequest, background_tasks: BackgroundTasks):
    """
    Queries the live MCA/RoC database for target electronics manufacturers
    matching a downstream finished product HSN code. If 'all' is provided, performs a batch run in the background.
    """
    try:
        def run_fetch():
            try:
                if request.hsn.lower() == "all" or request.state.lower() == "all":
                    fetch_all_saved_leads(state_filter=request.state)
                else:
                    fetch_and_filter_indian_buyers(downstream_hsn=request.hsn, state_filter=request.state)
            except Exception as e:
                print(f"[-] Background fetch failed: {e}")

        background_tasks.add_task(run_fetch)
        hsn_desc = "All target markets" if request.hsn.lower() == "all" else TARGET_HSN_MARKETS.get(request.hsn, "Unknown target market")
            
        return {
            "status": "accepted",
            "hsn": request.hsn,
            "hsn_description": hsn_desc,
            "state": request.state,
            "message": "Scraping and matching process has been started in the background."
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while initiating corporate buyer leads fetch: {str(e)}"
        )

@app.get("/leads", tags=["MCA Leads"], dependencies=[Depends(verify_api_key)])
def get_buyer_leads_get(hsn: str = "85371000", state: str = "MH"):
    """
    Queries the live MCA/RoC database for target electronics manufacturers
    matching a downstream finished product HSN code via GET parameters.
    """
    try:
        if hsn.lower() == "all" or state.lower() == "all":
            leads = fetch_all_saved_leads(state_filter=state)
            hsn_desc = "All target markets"
        else:
            leads = fetch_and_filter_indian_buyers(downstream_hsn=hsn, state_filter=state)
            hsn_desc = TARGET_HSN_MARKETS.get(hsn, "Unknown target market")
            
        return {
            "hsn": hsn,
            "hsn_description": hsn_desc,
            "state": state,
            "total_leads": len(leads),
            "leads": leads
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching corporate buyer leads: {str(e)}"
        )

@app.post("/leads/all", tags=["MCA Leads"], dependencies=[Depends(verify_api_key)])
def get_all_buyer_leads_post(request: BatchLeadsRequest, background_tasks: BackgroundTasks):
    """
    Triggers batch execution for ALL saved HSN codes and states in the background, 
    saving compiled active leads to 'all_active_leads.json'.
    """
    try:
        def run_fetch_all():
            try:
                fetch_all_saved_leads(state_filter=request.state)
            except Exception as e:
                print(f"[-] Background batch compilation failed: {e}")

        background_tasks.add_task(run_fetch_all)
        return {
            "status": "accepted",
            "message": "Batch compilation has been started in the background.",
            "state_filter": request.state,
            "output_file": "all_active_leads.json"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred during batch lead compilation initiation: {str(e)}"
        )

@app.get("/leads/all", tags=["MCA Leads"], dependencies=[Depends(verify_api_key)])
def get_all_buyer_leads_get(state: str = "all"):
    """
    Triggers batch execution for ALL saved HSN codes and states via GET parameters, saving compiled active leads
    to 'all_active_leads.json' and returning the aggregated results.
    """
    try:
        leads = fetch_all_saved_leads(state_filter=state)
        return {
            "message": "Batch compilation completed successfully.",
            "state_filter": state,
            "output_file": "all_active_leads.json",
            "total_leads": len(leads),
            "leads": leads
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred during batch lead compilation: {str(e)}"
        )

@app.get("/analyses", response_model=List[Dict[str, Any]], tags=["Analysis Database"], dependencies=[Depends(verify_api_key)])
def list_analyses():
    """
    Returns a list of all saved component analyses in the database.
    """
    try:
        db = LeadsDatabase()
        analyses = db.get_all_component_analyses()
        db.close()
        for a in analyses:
            if "analyzed_at" in a and a["analyzed_at"]:
                a["analyzed_at"] = a["analyzed_at"].isoformat()
        return analyses
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve analyses from database: {str(e)}"
        )

@app.get("/analyses/{analysis_id}", response_model=Dict[str, Any], tags=["Analysis Database"], dependencies=[Depends(verify_api_key)])
def get_analysis_detail(analysis_id: str):
    """
    Retrieves the detailed specifications and synthesized report for a saved component analysis.
    """
    try:
        db = LeadsDatabase()
        analysis = db.get_component_analysis(analysis_id)
        db.close()
        if not analysis:
            raise HTTPException(
                status_code=404,
                detail=f"Analysis with ID '{analysis_id}' not found."
            )
        if "analyzed_at" in analysis and analysis["analyzed_at"]:
            analysis["analyzed_at"] = analysis["analyzed_at"].isoformat()
        return analysis
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve analysis detail: {str(e)}"
        )

@app.get("/analyses/{analysis_id}/matches", tags=["Analysis Database"], dependencies=[Depends(verify_api_key)])
def get_analysis_matches(analysis_id: str):
    """
    Retrieves all matching corporate buyer leads from the database for this component.
    """
    try:
        db = LeadsDatabase()
        analysis = db.get_component_analysis(analysis_id)
        if not analysis:
            db.close()
            raise HTTPException(
                status_code=404,
                detail=f"Analysis with ID '{analysis_id}' not found."
            )
        matches = db.get_component_matches(analysis_id)
        db.close()
        
        for m in matches:
            if "matched_at" in m and m["matched_at"]:
                m["matched_at"] = m["matched_at"].isoformat()
                
        return {
            "analysis_id": analysis_id,
            "component_name": analysis["component_name"],
            "part_number": analysis["part_number"],
            "total_matches": len(matches),
            "matches": matches
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve matches for analysis: {str(e)}"
        )

@app.post("/analyses/{analysis_id}/rematch", tags=["Analysis Database"], dependencies=[Depends(verify_api_key)])
def rematch_component(analysis_id: str):
    """
    Forces recalculation of buyer leads matching this component analysis against the latest leads table.
    """
    try:
        db = LeadsDatabase()
        analysis = db.get_component_analysis(analysis_id)
        if not analysis:
            db.close()
            raise HTTPException(
                status_code=404,
                detail=f"Analysis with ID '{analysis_id}' not found."
            )
        matches_count = db.match_and_save_leads_for_component(analysis_id)
        db.close()
        return {
            "success": True,
            "analysis_id": analysis_id,
            "component_name": analysis["component_name"],
            "total_matches": matches_count,
            "message": f"Successfully recalculated and stored {matches_count} matching buyer leads."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to run rematching: {str(e)}"
        )

@app.delete("/analyses/{analysis_id}", tags=["Analysis Database"], dependencies=[Depends(verify_api_key)])
def delete_analysis(analysis_id: str):
    """
    Deletes the component analysis and all associated matches from the database.
    """
    try:
        db = LeadsDatabase()
        deleted = db.delete_component_analysis(analysis_id)
        db.close()
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Analysis with ID '{analysis_id}' not found or already deleted."
            )
        return {
            "success": True,
            "message": f"Successfully deleted analysis '{analysis_id}' and all cascade-deleted matches."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete analysis: {str(e)}"
        )

@app.get("/stats", tags=["Analysis Database"], dependencies=[Depends(verify_api_key)])
def get_database_stats():
    """
    Returns statistics about the leads database.
    """
    try:
        db = LeadsDatabase()
        stats = db.get_stats()
        db.close()
        return stats
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch database stats: {str(e)}"
        )

@app.get("/db-leads", tags=["Analysis Database"], dependencies=[Depends(verify_api_key)])
def get_db_leads(skip: int = 0, limit: int = 100, status: Optional[str] = None, search: Optional[str] = None):
    """
    Returns a paginated, filterable list of saved leads from the MySQL database.
    """
    try:
        from database import get_session, Company
        with get_session() as session:
            query = session.query(Company)
            
            if status and status.lower() != "all":
                query = query.filter(Company.crawl_status == status.lower())
            if search:
                query = query.filter(Company.company_name.like(f"%{search}%"))
                
            total_count = query.count()
            records = query.order_by(Company.company_name.asc()).offset(skip).limit(limit).all()
            
            leads_list = []
            for r in records:
                leads_list.append({
                    "cin_number": r.cin_number,
                    "company_name": r.company_name,
                    "state_code": r.state_code,
                    "crawl_status": r.crawl_status,
                    "website": r.website,
                    "emails": r.emails or [],
                    "phones": r.phones or [],
                    "offerings": r.offerings or []
                })
                
            return {
                "total": total_count,
                "skip": skip,
                "limit": limit,
                "leads": leads_list
            }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve leads from database: {str(e)}"
        )

@app.post("/crawl-step", tags=["Analysis Database"], dependencies=[Depends(verify_api_key)])
def crawl_step():
    """
    Triggers crawling of the next pending lead in the database.
    """
    try:
        db = LeadsDatabase()
        pending = db.get_pending_leads(limit=1)
        if not pending:
            return {"success": True, "message": "No pending leads to crawl."}
        
        lead = pending[0]
        serper_api_key = os.environ.get("SERPER_API_KEY")
        proxy_url = os.environ.get("PROXY_URL")
        
        # Close the db connection before running the worker task (which opens its own)
        db.close()
        
        worker_task(lead, max_pages=5, serper_key=serper_api_key, proxy_url=proxy_url)
        
        return {
            "success": True,
            "message": f"Successfully crawled and synthesized lead: {lead.get('company_name')}"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Crawl step failed: {str(e)}"
        )


class TraderHarvestRequest(BaseModel):
    component_part_number: str = Field(..., description="Component part number to harvest", json_schema_extra={"example": "CYW20706"})
    source: Optional[str] = Field("independent", description="Source aggregator engine: 'nexar', 'independent', 'customs', or 'all'", json_schema_extra={"example": "independent"})


@app.post("/traders/harvest", tags=["Sourcing Traders"], dependencies=[Depends(verify_api_key)])
def harvest_traders(request: TraderHarvestRequest):
    """
    Queries components sourcing indices to harvest traders and link them to the target part number.
    Supports 'nexar', 'independent', or 'all' source routing.
    """
    try:
        traders = []
        source = (request.source or "independent").lower()
        
        # 1. Fetch from Nexar if selected
        if source in ("nexar", "all"):
            try:
                from trader_harvesting_engine import harvest_live_component_traders
                nexar_traders = harvest_live_component_traders(request.component_part_number)
                traders.extend(nexar_traders)
            except Exception as ex:
                print(f"[-] Nexar harvesting sub-routine error: {ex}")
                
        # 2. Fetch from Unified Master Trader Aggregator (Independent + Customs channels)
        if source in ("independent", "customs", "all"):
            try:
                from master_trader_aggregator import aggregate_all_traders
                consolidated_traders = aggregate_all_traders(request.component_part_number)
                traders.extend(consolidated_traders)
            except Exception as ex:
                print(f"[-] Master Trader Aggregator harvesting error: {ex}")

        # Remove duplicates from combined list based on name
        seen = set()
        unique_traders = []
        for t in traders:
            name = t.get("trader_name") or t.get("name")
            if name and name.strip().lower() not in seen:
                seen.add(name.strip().lower())
                unique_traders.append({
                    "trader_name": name,
                    "website": t.get("website") or "N/A",
                    "phone": t.get("phone") or "N/A",
                    "email": t.get("email") or "N/A",
                    "type": t.get("type") or t.get("business_type") or "Independent Broker"
                })

        # Save to DB
        if source == "nexar":
            from independent_network_harvester import save_harvested_traders_to_db
            linked_count = save_harvested_traders_to_db(
                unique_traders, 
                request.component_part_number, 
                None # Let it auto-default to "85423200" in the database layer
            )
        else:
            # The unified aggregator already synced and linked all traders internally.
            # Get the current linked count from the database to return in the API response.
            from database import db_session, TraderInventoryJunction
            session = db_session()
            try:
                linked_count = session.query(TraderInventoryJunction).filter(
                    TraderInventoryJunction.component_part_number == request.component_part_number
                ).count()
            finally:
                session.close()
        
        return {
            "success": True,
            "component_part_number": request.component_part_number,
            "source_engine": source,
            "total_harvested": len(unique_traders),
            "new_linked_traders": linked_count,
            "traders": unique_traders
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to harvest component traders: {str(e)}"
        )


@app.get("/traders", tags=["Sourcing Traders"], dependencies=[Depends(verify_api_key)])
def get_traders(component_part_number: Optional[str] = None):
    """
    Retrieves all component traders, optionally filtered by the component part number they stock.
    """
    try:
        from database import get_session, ComponentTrader, TraderInventoryJunction
        
        with get_session() as session:
            if component_part_number:
                # Query traders linked to the specific component
                results = (
                    session.query(ComponentTrader, TraderInventoryJunction.global_hsn_code)
                    .join(TraderInventoryJunction, ComponentTrader.trader_id == TraderInventoryJunction.trader_id)
                    .filter(TraderInventoryJunction.component_part_number == component_part_number)
                    .all()
                )
                traders_list = []
                for trader, hsn in results:
                    traders_list.append({
                        "trader_id": trader.trader_id,
                        "trader_name": trader.trader_name,
                        "website": trader.website,
                        "phone": trader.phone,
                        "email": trader.email,
                        "trader_type": trader.trader_type,
                        "last_inventory_sync": trader.last_inventory_sync.isoformat() if trader.last_inventory_sync else None,
                        "global_hsn_code": hsn
                    })
            else:
                # Query all traders
                results = session.query(ComponentTrader).all()
                traders_list = []
                for trader in results:
                    traders_list.append({
                        "trader_id": trader.trader_id,
                        "trader_name": trader.trader_name,
                        "website": trader.website,
                        "phone": trader.phone,
                        "email": trader.email,
                        "trader_type": trader.trader_type,
                        "last_inventory_sync": trader.last_inventory_sync.isoformat() if trader.last_inventory_sync else None
                    })
                    
            return {
                "success": True,
                "total_traders": len(traders_list),
                "traders": traders_list
            }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch component traders: {str(e)}"
        )


# ==========================================
# JLCPCB Open Parts Database Endpoints
# ==========================================

class JLCPCBQueryRequest(BaseModel):
    keyword: str = Field("", description="Keyword search matched on part number and description")
    manufacturer: str = Field("", description="Filter by manufacturer name")
    category: str = Field("", description="Filter by category or subcategory name")
    min_stock: int = Field(10000, description="Minimum stock count available in inventory")
    limit: int = Field(50, description="Pagination limit")
    offset: int = Field(0, description="Pagination offset")

class JLCPCBImportRequest(BaseModel):
    components: List[Dict[str, Any]] = Field(..., description="List of components selected for import")
    hsn: str = Field("85423200", description="HSN classification code to assign during sync")

@app.get("/jlcpcb/status", tags=["JLCPCB Database"])
def check_jlcpcb_status():
    """Checks download and index status of the local 1 GB JLCPCB parts database."""
    from jlcpcb_db_harvester import get_status, save_status
    import os
    
    status = get_status()
    file_exists = os.path.exists("jlcpcb-parts.sqlite")
    
    # Synchronize logical status with filesystem truth
    if file_exists and status.get("status") == "not_downloaded":
        size = os.path.getsize("jlcpcb-parts.sqlite")
        status = {
            "status": "ready",
            "downloaded_bytes": size,
            "total_bytes": size,
            "progress_percent": 100.0,
            "speed_mbps": 0.0,
            "eta_seconds": 0,
            "error": None
        }
        save_status(status)
    elif not file_exists and status.get("status") == "ready":
        status = {
            "status": "not_downloaded",
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "progress_percent": 0.0,
            "speed_mbps": 0.0,
            "eta_seconds": 0,
            "error": None
        }
        save_status(status)
        
    return status

@app.post("/jlcpcb/download", tags=["JLCPCB Database"])
def trigger_jlcpcb_download(background_tasks: BackgroundTasks):
    """Triggers streaming download of the 1 GB parts database as a background worker."""
    from jlcpcb_db_harvester import download_database, get_status
    
    status = get_status()
    if status.get("status") == "downloading":
        return {"status": "already_downloading", "message": "Database download is already in progress."}
        
    background_tasks.add_task(download_database)
    return {"status": "started", "message": "Database download has started in the background."}

@app.post("/jlcpcb/query", tags=["JLCPCB Database"])
def query_jlcpcb(req: JLCPCBQueryRequest):
    """Queries the local SQLite parts database using keyword and parametric parameters."""
    from jlcpcb_db_harvester import query_jlcpcb_components
    import os
    
    if not os.path.exists("jlcpcb-parts.sqlite"):
        raise HTTPException(
            status_code=404, 
            detail="JLCPCB SQLite database is not downloaded. Please trigger /jlcpcb/download first."
        )
        
    try:
        results, count = query_jlcpcb_components(
            search_keyword=req.keyword,
            manufacturer=req.manufacturer,
            category=req.category,
            min_stock=req.min_stock,
            limit=req.limit,
            offset=req.offset
        )
        return {
            "success": True,
            "total_matches": count,
            "limit": req.limit,
            "offset": req.offset,
            "components": results
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Query transaction failed: {str(e)}"
        )

@app.post("/jlcpcb/import", tags=["JLCPCB Database"])
def import_jlcpcb(req: JLCPCBImportRequest):
    """Maps selected parts into the MySQL components & traders junction schema in bulk."""
    from jlcpcb_db_harvester import sync_components_to_mysql
    
    try:
        imported = sync_components_to_mysql(req.components, hsn_code=req.hsn)
        return {
            "success": True,
            "imported_count": imported,
            "message": f"Successfully mapped and ingested {imported} component nodes straight into MySQL."
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Bulk insertion schema sync failed: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    # Read host and port from environment or defaults
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("main:app", host=host, port=port, reload=True)

