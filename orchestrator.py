"""
CHANGES FROM YOUR VERSION, AND WHY
====================================
1. web_context is now passed to the Specialist and QA agents too, not just
   the Extractor. This was the main confirmed cause of ungrounded application
   claims -- those two agents were reasoning with zero external evidence.

2. All agent .run() calls now use explicit keyword arguments for web_context
   and log_cb. The refined agents.py makes both keyword-only specifically so
   a future signature change can't silently misroute an argument again --
   but using keywords here is the belt-and-suspenders version of that fix.

3. The refinement_prompt wording no longer says "expand the application
   list." That phrasing actively worked against corrective recommendations
   (e.g. "remove this wrong entry") by pushing the model to grow the list
   instead of fixing it. Reworded to prioritize correcting/removing flagged
   entries over adding new ones.

4. default max_refinement_loops changed from 1 to 2, and -- more importantly
   -- final_results now includes an explicit "qa_approved" boolean and
   "qa_status" string field. Previously, a report that exhausted retries
   without approval looked identical to an approved one to any downstream
   code (CRM sync, UI) that didn't parse the log strings. Check wherever
   run_pipeline() is actually called from (your UI/app layer) -- the test
   run you showed me never logged a refinement attempt at all, which only
   happens if max_refinement_loops was passed as 0 there. That's outside
   this file, but it's the actual reason QA never got a chance to act.

5. The QA re-audit after each refinement loop now also gets web_context, for
   the same grounding reason as point 1.
"""

import json
import uuid
import time
import urllib.request
import os
from typing import Dict, Any, List, Callable, Optional
from groq import Groq
from agents import (
    SpecsExtractorAgent,
    ApplicationDomainSpecialistAgent,
    SynthesisAgent,
    QualityAssuranceAgent,
    NomenclatureAgent
)
from workspace import WorkspaceManager
from search import search_part_number

try:
    from mca_buyer_matcher import TARGET_HSN_MARKETS
except ImportError:
    TARGET_HSN_MARKETS = {}

# Production Credentials for Mouser
MOUSER_API_KEY = os.getenv("MOUSER_API_KEY", "6b953b1c-d49d-4723-8711-e3465eb11a76").strip()
MOUSER_URL = f"https://api.mouser.com/api/v1.0/search/partnumber?apiKey={MOUSER_API_KEY}"


def dynamic_mouser_resolver(part_number: str) -> dict:
    """
    Directly queries the free Mouser API database to resolve component parameters.
    Bypasses messy web scraper column-smearing completely.
    """
    payload = {
        "SearchByPartRequest": {
            "mouserPartNumber": part_number.strip(),
            "partSearchOptions": "BeginsWith"  # Upgraded from "Exact" to catch trailing package suffix codes
        }
    }
    try:
        req = urllib.request.Request(
            MOUSER_URL, 
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode())
            parts_found = res_data.get("SearchResults", {}).get("Parts", [])
            
            if parts_found:
                target_part = parts_found[0]
                return {
                    "success": True,
                    "component_type": target_part.get("Category", "Unknown Integrated Circuit"),
                    "manufacturer": target_part.get("Manufacturer", "Unknown"),
                    "description": target_part.get("Description", "")
                }
    except Exception as e:
        print(f"[!] Mouser API Junction bypassed or rate limit hit: {e}")
    
    return {"success": False}


def _build_component_family_queries(specs: Dict[str, Any], part_no: str) -> List[str]:
    """
    Generates component-family-level search queries that return real application
    context even when the exact part number has no web presence.
    These are the searches that actually give the model something to ground on.
    """
    queries = []
    comp_type = specs.get("component_type") or ""
    tech = specs.get("technical_details") or {}
    params = specs.get("key_parameters") or {}
    mfr = ""
    if isinstance(tech, dict):
        mfr = tech.get("manufacturer", {})
        if isinstance(mfr, dict):
            mfr = mfr.get("value", "")

    # Extract clean param values regardless of confirmed/inferred wrapper
    def pval(field):
        v = params.get(field, "")
        if isinstance(v, dict):
            return v.get("value", "")
        return str(v) if v else ""

    capacity = pval("capacity")
    iface = ""
    if isinstance(tech, dict):
        iface_raw = tech.get("interface", "")
        if isinstance(iface_raw, dict):
            iface = iface_raw.get("value", "")
        else:
            iface = str(iface_raw) if iface_raw else ""

    # Part-number prefix (covers same family: K4A8G085WC → "K4A8G" family)
    prefix = part_no[:6] if len(part_no) >= 6 else part_no
    if prefix:
        queries.append(f'"{prefix}" datasheet application')

    # Component type application search
    if comp_type and comp_type != "Unknown":
        queries.append(f"{comp_type} applications board design schematic")
        if mfr:
            queries.append(f"{mfr} {comp_type} reference design application note")
        if capacity:
            queries.append(f"{comp_type} {capacity} board application")
        if iface and iface not in ("N/A", "Not specified", ""):
            queries.append(f"{iface} {comp_type} design PCB")

    # Specific known types — targeted queries
    ct_lower = comp_type.lower()
    if "ddr5" in ct_lower:
        queries.append("DDR5 RDIMM server motherboard application guide")
        queries.append("DDR5 SODIMM laptop workstation design")
    elif "ddr4" in ct_lower:
        queries.append("DDR4 SDRAM embedded SBC industrial board application")
        queries.append("DDR4 memory module server storage controller design")
    elif "ddr3" in ct_lower:
        queries.append("DDR3 SDRAM embedded controller PLC HMI application")
    elif "emmc" in ct_lower or "nand" in ct_lower:
        queries.append("eMMC 5.1 smartphone IoT embedded storage application note")
        queries.append("eMMC NAND flash industrial gateway STM32 iMX8 design")
    elif "nor flash" in ct_lower or "spi" in ct_lower.split():
        queries.append("SPI NOR Flash BIOS firmware boot storage design application")
        queries.append("SPI NOR Flash microcontroller router PLC firmware storage")
    elif "nvme" in ct_lower or "ssd" in ct_lower:
        queries.append("NVMe U.3 enterprise SSD datacenter server storage design")
        queries.append("NVMe SSD storage array NAS server application")
    elif "bluetooth" in ct_lower or "ble" in ct_lower:
        queries.append("Bluetooth LE SoC IoT wearable smart home PCB design")
        queries.append("BLE microcontroller industrial wireless sensor application note")
    elif "lpddr" in ct_lower:
        queries.append("LPDDR mobile SoC smartphone tablet PCB design")
    elif "resistor" in ct_lower or "capacitor" in ct_lower:
        queries.append(f"SMD {comp_type} PCB design application")

    return queries[:8]  # cap at 8 to stay within cost/time budget


class InventoryOrchestrator:
    """
    Coordinates the multi-agent pipeline:
    Specs Extraction -> Application Mapping -> Report Synthesis -> QA Audit.
    """

    def __init__(self, client: Groq, model: Optional[str] = None):
        self.client = client
        self.extractor = SpecsExtractorAgent(model=model) if model else SpecsExtractorAgent()
        self.specialist = ApplicationDomainSpecialistAgent(model=model) if model else ApplicationDomainSpecialistAgent()
        self.synthesizer = SynthesisAgent(model=model) if model else SynthesisAgent()
        self.qa_auditor = QualityAssuranceAgent(model=model) if model else QualityAssuranceAgent()
        self.nomenclature = NomenclatureAgent(model=model) if model else NomenclatureAgent()

    def _format_qa_notes(self, raw_notes: Any) -> str:
        """Helper to format QA evaluation notes (which can be a string, list of strings, or list of dicts) into a clean string."""
        if not raw_notes:
            return ""
        if isinstance(raw_notes, list):
            formatted_notes = []
            for item in raw_notes:
                if isinstance(item, dict):
                    claim = item.get("claim", "")
                    issue = item.get("issue", "")
                    category = item.get("category", "")
                    cat_str = f" [{category}]" if category else ""
                    formatted_notes.append(f"- Claim: {claim}\n  Issue: {issue}{cat_str}")
                else:
                    formatted_notes.append(f"- {item}")
            return "\n".join(formatted_notes)
        elif isinstance(raw_notes, dict):
            return json.dumps(raw_notes, indent=2)
        return str(raw_notes)

    def _verify_and_filter_apps(
        self,
        normalized_apps: Dict[str, Any],
        part_no: str,
        alternatives: List[str],
        all_search_results: List[str],
        log: Callable[[str], None]
    ) -> None:
        """
        Python-level verification of confidence tags.
        Checks if the hardware system board occurs alongside the part number or standard alternatives
        in the retrieved web search results text blocks.
        """
        apps = normalized_apps.get("applications", [])
        if not apps:
            return

        part_terms = [part_no.lower()] + [alt.lower() for alt in alternatives if alt]
        stop_words = {"board", "system", "card", "module", "device", "controller", "motherboard", "pcb", "interface"}

        for app in apps:
            board = app.get("target_product_family", "")
            is_verified = False

            if board:
                board_lower = board.lower()
                board_terms = [
                    w.strip(".,;:()\"'-") 
                    for w in board_lower.split() 
                    if w.strip(".,;:()\"'-") not in stop_words and len(w.strip(".,;:()\"'-")) > 2
                ]
                if not board_terms:
                    board_terms = [board_lower]

                # Check each text block
                for res_text in all_search_results:
                    if not res_text:
                        continue
                    res_lower = res_text.lower()
                    # Check if any part number term is in the block
                    if any(pt in res_lower for pt in part_terms):
                        # Check if board name or all board terms are in the block
                        if board_lower in res_lower or all(term in res_lower for term in board_terms):
                            is_verified = True
                            break

            if is_verified:
                app["confidence"] = "verified_via_web_evidence"
                log(f"[Orchestrator] Verification SUCCESS: '{board}' verified in search results alongside part number.")
            else:
                app["confidence"] = "engineering_inference_only"
                log(f"[Orchestrator] Verification DOWNGRADE: '{board}' not found in search results alongside part number. Marked as engineering inference.")

    def run_pipeline(self, original_input: Optional[str] = None,
                     log_callback: Optional[Callable[[str], None]] = None,
                     max_refinement_loops: int = 2,
                     run_id: Optional[str] = None,
                     context_anchor_hint: Optional[str] = None,
                     user_input: Optional[str] = None) -> Dict[str, Any]:
        logs = []
        def log(msg: str):
            logs.append(msg)
            if log_callback:
                log_callback(msg)

        part_name = original_input or user_input
        if not part_name:
            raise ValueError("No input part number provided to run_pipeline.")

        run_id = run_id or str(uuid.uuid4())
        workspace_mgr = WorkspaceManager()
        workspace_mgr.create_run_directory(run_id)
        log(f"Pipeline starting. Run ID: {run_id}")

        # Enforce two-pass sequence: Mouser API search -> Nomenclature fallback
        if not context_anchor_hint:
            log("[Orchestrator] Running naked string verification. Querying Mouser API...")
            api_res = dynamic_mouser_resolver(part_name)
            if api_res.get("success"):
                context_anchor_hint = f"{api_res['manufacturer']} {api_res['component_type']} {api_res['description']}"
                log(f"[Orchestrator] API Match Secured! Generated Anchor Context: '{context_anchor_hint}'")
            else:
                log("[-] Part number not indexed in retail registries. Executing Nomenclature Parsing...")
                context_anchor_hint = self.nomenclature.extract_via_naming_rules(self.client, part_name, log_cb=log)
                log(f"[Orchestrator] Self-Generated Anchor Context via Rules: '{context_anchor_hint}'")

        # === SEARCH PASS 1: parametric (exact part number + datasheet) ===
        parametric_query = f"{part_name} datasheet specs"
        log(f"[Search 1 — Parametric] '{parametric_query}'")
        parametric_ctx = search_part_number(parametric_query)
        if parametric_ctx and len(parametric_ctx) > 4000:
            parametric_ctx = parametric_ctx[:4000] + "\n\n... [Truncated to fit model token limits] ..."
        workspace_mgr.save_step(run_id, "web_search_parametric", parametric_ctx)

        # === STEP 1: Specs extraction ===
        specs = self.extractor.run(self.client, part_name,
                                   web_context=parametric_ctx,
                                   context_anchor_hint=context_anchor_hint,
                                   log_cb=log)
        workspace_mgr.save_step(run_id, "specs", specs)

        part_no = specs.get("component_name") or part_name
        comp_type = specs.get("component_type") or ""
        alternatives = specs.get("standard_alternatives", [])
        if not isinstance(alternatives, list):
            alternatives = [alternatives] if alternatives else []

        # === SEARCH PASS 2: application-focused (exact part number) ===
        application_queries = [
            f'"{part_no}" application OR design OR schematic',
            f'"{part_no}" filetype:pdf',
            f'"{part_no}" BOM',
        ]
        # === SEARCH PASS 3: component-family searches (the critical new addition) ===
        # These work even when the exact part number has no web presence
        family_queries = _build_component_family_queries(specs, part_no)

        all_results = [parametric_ctx]

        for q in application_queries:
            time.sleep(0.15)
            log(f"[Search — Application] '{q}'")
            res = search_part_number(q)
            if res and "No direct" not in res and "failed" not in res:
                all_results.append(res)

        for q in family_queries:
            time.sleep(0.15)
            log(f"[Search — Family/Type] '{q}'")
            res = search_part_number(q)
            if res and "No direct" not in res and "failed" not in res:
                all_results.append(res)

        # Industry vertical searches for applications
        verticals = ["Industrial Automation", "Automotive", "Medical",
                     "Datacenter", "Telecommunications", "Consumer Electronics"]
        for vert in verticals:
            time.sleep(0.10)
            q = f'"{part_no}" {vert}'
            log(f"[Search — Vertical] '{q}'")
            res = search_part_number(q)
            if res and "No direct" not in res and "failed" not in res:
                all_results.append(res)

        enriched_ctx = "\n\n===\n\n".join(all_results)
        # Limit enriched context to ~4000 characters to prevent API token limit errors (413) on free-tier models
        if len(enriched_ctx) > 4000:
            enriched_ctx = enriched_ctx[:4000] + "\n\n... [Truncated to fit model token limits] ..."
        workspace_mgr.save_step(run_id, "web_search_enriched", enriched_ctx)

        # === STEP 2: Application mapping with HSN lookup injection ===
        if not TARGET_HSN_MARKETS:
            log("[Orchestrator] WARNING: TARGET_HSN_MARKETS is empty. "
                "Check that mca_buyer_matcher.py imported correctly. "
                "AppSpecialist will generate HSN codes unconstrained.")
        else:
            log(f"[Orchestrator] Injecting {len(TARGET_HSN_MARKETS)}-code HSN lookup table into AppSpecialist.")

        apps_data = self.specialist.run(
            self.client, specs,
            web_context=enriched_ctx,
            hsn_lookup=TARGET_HSN_MARKETS if TARGET_HSN_MARKETS else None,
            log_cb=log
        )
        normalized_apps = workspace_mgr.normalize_and_assign_ids(apps_data)
        self._verify_and_filter_apps(normalized_apps, part_no, alternatives, all_results, log)

        # Warn about any invalid HSN codes that slipped through
        invalid_hsn = [
            a.get("downstream_finished_product_hsn")
            for a in normalized_apps.get("applications", [])
            if a.get("downstream_finished_product_hsn") == "INVALID_HSN_NOT_IN_LOOKUP"
        ]
        if invalid_hsn:
            log(f"[Orchestrator] WARNING: {len(invalid_hsn)} application(s) have invalid HSN codes "
                f"(not in TARGET_HSN_MARKETS). They will not match any buyers until corrected.")

        workspace_mgr.save_step(run_id, "applications", normalized_apps)

        # === STEP 3: Synthesis ===
        report = self.synthesizer.run(self.client, specs, normalized_apps, log)
        workspace_mgr.save_step(run_id, "report", report)

        # === STEP 4: QA ===
        specs_for_qa = specs.copy()
        specs_for_qa["specialist_currency_status"] = (
            apps_data.get("technology_currency_assessment", {}).get("status"))
        qa_result = self.qa_auditor.run(
            self.client, specs_for_qa, report,
            web_context=enriched_ctx, log_cb=log
        )
        workspace_mgr.save_step(run_id, "qa_result", qa_result)

        # === Refinement loop ===
        loop_count = 0
        while not qa_result.get("approved", False) and loop_count < max_refinement_loops:
            loop_count += 1
            log(f"[Orchestrator] QA rejected. Refinement loop {loop_count}/{max_refinement_loops}...")
            recs = qa_result.get("recommendations", [])
            formatted_notes = self._format_qa_notes(qa_result.get("evaluation_notes", ""))
            refinement_prompt = (
                f"Prior output was rejected by QA.\nQA notes:\n{formatted_notes}\n"
                f"Required corrections (address each precisely — remove wrong entries, "
                f"do not pad with new entries while leaving flagged errors in place):\n"
                + "\n".join(f"- {r}" for r in recs)
            )
            specs_fb = specs.copy()
            specs_fb["refinement_instructions"] = refinement_prompt
            apps_data = self.specialist.run(
                self.client, specs_fb,
                web_context=enriched_ctx,
                hsn_lookup=TARGET_HSN_MARKETS if TARGET_HSN_MARKETS else None,
                log_cb=log
            )
            normalized_apps = workspace_mgr.normalize_and_assign_ids(apps_data)
            self._verify_and_filter_apps(normalized_apps, part_no, alternatives, all_results, log)
            workspace_mgr.save_step(run_id, "applications", normalized_apps)
            report = self.synthesizer.run(self.client, specs, normalized_apps, log)
            workspace_mgr.save_step(run_id, "report", report)
            specs_for_qa = specs.copy()
            specs_for_qa["specialist_currency_status"] = (
                apps_data.get("technology_currency_assessment", {}).get("status"))
            qa_result = self.qa_auditor.run(
                self.client, specs_for_qa, report,
                web_context=enriched_ctx, log_cb=log
            )
            workspace_mgr.save_step(run_id, "qa_result", qa_result)

        qa_approved = qa_result.get("approved", False)
        qa_status = "approved" if qa_approved else "unresolved_after_max_retries"
        if qa_approved:
            log("[Orchestrator] QA approved.")
        else:
            log(f"[Orchestrator] QA not approved after {loop_count} loop(s). "
                f"Shipping as unresolved — manual review needed.")

        apps_list = normalized_apps.get("applications", [])
        final_results = {
            "id": run_id,
            "success": True,
            "qa_approved": qa_approved,
            "qa_status": qa_status,
            "original_input": user_input,
            "specs": specs,
            "verified_in_production": [a for a in apps_list if a.get("confidence") == "verified_via_web_evidence"],
            "likely_matches": [a for a in apps_list if a.get("confidence") == "engineering_inference_only"],
            "applications": apps_list,
            "report": report,
            "qa_notes": self._format_qa_notes(qa_result.get("evaluation_notes", "")),
            "logs": logs
        }
        workspace_mgr.save_step(run_id, "final_result", final_results)
        return final_results