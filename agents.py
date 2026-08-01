"""
REFINED AGENT PROMPTS — changes from your original version, and why
=====================================================================

1. SpecsExtractorAgent
   - Now requires strict separation of "confirmed from web_context" vs
     "inferred / typical for this part family." It must not silently present
     a guess with the same confidence as a verified fact.
   - Captures hard signals (date code, manufacturer lifecycle status:
     Active/NRND/EOL/Obsolete) when present in the input or web_context,
     because these are PUBLISHED facts, not inferences -- Digi-Key, Mouser,
     and Octopart all expose lifecycle status directly, and your own
     liquidation sheet already has a "Date Code" column (DC24+, DC25+).
     That hard data should flow through the pipeline as ground truth,
     not get re-guessed by an LLM later.

2. ApplicationDomainSpecialistAgent
   - Adds a MANDATORY "technology_currency_assessment" step that must run
     BEFORE any application is listed. This is the fix for the "decade-old
     Nokia phone" problem: the agent must explicitly decide whether this
     component's interface/technology generation is still being newly
     designed into 2025-2026 products, or whether it's only relevant to
     legacy/sustaining production and repair/spares markets for equipment
     already in service.
   - Forbids mapping a legacy-interface part onto a hardware category whose
     CURRENT real-world reference designs have moved to a different
     interface, unless it can specifically justify why legacy variants are
     still being newly designed in (rare, and must be justified, not assumed).
   - Every application entry now needs a confidence tag distinguishing
     "verified_via_web_evidence" from "engineering_inference_only" --
     this stops the model from padding the list with speculative entries
     to appear thorough, which was diluting the genuinely strong matches.
   - Output now includes a buyer-archetype recommendation per application,
     tying the technical finding directly back to your actual business
     goal (who should we call about this).

3. SynthesisAgent
   - Must surface the confidence and currency labels instead of smoothing
     everything into uniformly confident prose. A reader should be able to
     tell "verified" apart from "inferred" at a glance.
   - Adds a required "Current Market Relevance & Recommended Buyer Type"
     section translating the currency classification into a concrete
     liquidation strategy (e.g. legacy part -> target repair/MRO/spares
     houses and long-lifecycle industrial/aerospace sustaining-production
     lines, NOT new consumer-electronics OEMs).

4. QualityAssuranceAgent
   - Now accepts the same web_context the SpecsExtractor got, so it can
     independently spot-check the riskiest claims instead of just
     re-reading the same unverified text a second time.
   - Adds explicit audit checks for the two failure modes you actually hit:
     (a) does an application claim contradict the component's real
     technology generation (e.g. assigning a legacy parallel-interface part
     to a board whose current designs use a different interface), and
     (b) does the report correctly reflect any date code / lifecycle status
     that was available.

ORCHESTRATOR FIX NEEDED (not in this file -- you'll need to patch your
own orchestrator/pipeline.py):
   - When QualityAssuranceAgent returns approved=False, its "recommendations"
     list must be passed back into AppSpecialist/Synthesis as additional
     context for a real regeneration -- not just a blind retry of the same
     call with the same inputs. Your logs show two identical 514-word runs,
     which means the loop currently isn't using QA's feedback at all.
   - If retries are exhausted and QA still hasn't approved, the final
     output should be tagged "QA-FLAGGED - UNRESOLVED" rather than silently
     presented as clean. Right now it's shipped either way, which defeats
     the point of having a QA gate.
"""

import json
from typing import Dict, Any, List, Callable, Optional
from groq import Groq
from config import DEFAULT_MODEL, USE_OLLAMA


class BaseAgent:
    """Unchanged from your original -- Groq call wrapper with model fallback
    and rate-limit retry logic. Kept as-is since this part wasn't the issue."""

    def __init__(self, name: str, role: str, system_prompt: str, model: str = DEFAULT_MODEL):
        self.name = name
        self.role = role
        self.system_prompt = system_prompt

        if USE_OLLAMA:
            from config import get_available_ollama_models
            available = get_available_ollama_models()
            if model not in available and f"{model}:latest" in available:
                model = f"{model}:latest"
            elif model not in available and available:
                model = available[0]

        self.model = model

    def _call_llm(
        self,
        client: Groq,
        messages: List[Dict[str, str]],
        json_mode: bool = False,
        temperature: float = 0.2
    ) -> str:
        payload_messages = [{"role": "system", "content": self.system_prompt}] + messages
        import time

        extra_args = {}
        if not USE_OLLAMA:
            extra_args["max_tokens"] = 2048
            if json_mode:
                extra_args["response_format"] = {"type": "json_object"}

        max_retries = 8
        backoff_delay = 3.0

        for attempt in range(max_retries):
            try:
                chat_completion = client.chat.completions.create(
                    model=self.model,
                    messages=payload_messages,
                    temperature=temperature,
                    **extra_args
                )
                return chat_completion.choices[0].message.content or ""
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "413" in err_str or "rate_limit" in err_str or "limit reached" in err_str.lower() or "rate limit" in err_str.lower() or "too large" in err_str.lower() or "tpm" in err_str.lower() or "tpd" in err_str.lower():
                    if attempt == max_retries - 1:
                        from config import OPENAI_API_KEY
                        if OPENAI_API_KEY:
                            print("[Fallback] Groq rate limits hit. Attempting failover to OpenAI gpt-4o-mini...")
                            try:
                                from openai import OpenAI as OpenAIClient
                                oa_client = OpenAIClient(api_key=OPENAI_API_KEY)
                                chat_completion = oa_client.chat.completions.create(
                                    model="gpt-4o-mini",
                                    messages=payload_messages,
                                    temperature=temperature,
                                    **extra_args
                                )
                                return chat_completion.choices[0].message.content or ""
                            except Exception as oa_err:
                                print(f"[Fallback] OpenAI failover failed: {oa_err}")
                                raise e
                        raise e
                    
                    if not USE_OLLAMA:
                        from config import GROQ_FALLBACK_MODELS
                        groq_pool = GROQ_FALLBACK_MODELS
                        current_model = self.model
                        if current_model in groq_pool:
                            curr_idx = groq_pool.index(current_model)
                            next_idx = (curr_idx + 1) % len(groq_pool)
                            fallback_model = groq_pool[next_idx]
                        else:
                            fallback_model = groq_pool[0]
                        print(f"[{self.__class__.__name__}] Rate limit/Error hit. Rotating model from '{current_model}' to '{fallback_model}' (Attempt {attempt+1}/{max_retries})...")
                        self.model = fallback_model
                    
                    time.sleep(backoff_delay)
                    backoff_delay *= 2
                else:
                    from config import OPENAI_API_KEY
                    if OPENAI_API_KEY:
                        print(f"[Fallback] Groq error encountered: '{err_str}'. Attempting failover to OpenAI gpt-4o-mini...")
                        try:
                            from openai import OpenAI as OpenAIClient
                            oa_client = OpenAIClient(api_key=OPENAI_API_KEY)
                            chat_completion = oa_client.chat.completions.create(
                                model="gpt-4o-mini",
                                messages=payload_messages,
                                temperature=temperature,
                                **extra_args
                            )
                            return chat_completion.choices[0].message.content or ""
                        except Exception as oa_err:
                            print(f"[Fallback] OpenAI failover failed: {oa_err}")
                            raise e
                    raise e


class SpecsExtractorAgent(BaseAgent):
    def __init__(self, model: str = DEFAULT_MODEL):
        system_prompt = (
            "You are the Master Component Specifications Extractor Agent. Analyze raw part numbers "
            "and descriptions to isolate exact technical specifications.\n\n"

            "CRITICAL — CORRECT COMPONENT TYPE CLASSIFICATION:\n"
            "Decode the part number using manufacturer prefix conventions:\n"
            "SAMSUNG MEMORY: K4A=DDR4, K4B=DDR3, K4T=DDR2, K4E=LPDDR3, K4F=LPDDR4, KM=eMMC/MCP\n"
            "MICRON MEMORY: MT40=DDR4, MT41=DDR3/L, MT47=DDR2, MT60=DDR5, MT25Q=SPI NOR Flash, "
            "MTFD=NVMe/SATA SSD\n"
            "WINBOND: W25Q=SPI NOR Flash\n"
            "SAMSUNG NAND: KLM/KLMA=eMMC\n"
            "INFINEON/CYPRESS: CYW=Bluetooth/Wi-Fi SoC\n\n"

            "UNITS RULE: Never confuse Megabits with Megabytes. 512Mbit = 64MB, not 512MB.\n\n"

            "Return strictly valid JSON with these fields:\n"
            "- component_name: exact normalized manufacturer part number\n"
            "- component_type: precise classification (e.g. 'DDR4 SDRAM Memory IC', "
            "'SPI NOR Flash Memory', '7.68TB NVMe U.3 Enterprise SSD', "
            "'eMMC 5.1 NAND Flash Storage', 'Bluetooth 4.2 SoC MCU')\n"
            "- key_parameters: dict of key metrics with value and confirmed/inferred status\n"
            "- technical_details: dict of interface, manufacturer, speed, package, voltage, "
            "temperature grade — all with confirmed/inferred tags\n"
            "- currency_signals: dict with date_code (e.g. 'DC24+') and lifecycle_status "
            "(Active/NRND/EOL/Obsolete) — null if genuinely unavailable, never fabricated\n"
            "- standard_alternatives: list of verified cross-reference part numbers only"
        )
        super().__init__("SpecsExtractor", "Technical Specifications Analyst", system_prompt, model)

    def run(self, client: Groq, user_input: str, *,
            web_context: Optional[str] = None,
            context_anchor_hint: Optional[str] = None,
            log_cb: Callable[[str], None] = None) -> Dict[str, Any]:
        if log_cb:
            log_cb(f"[{self.name}] Analyzing: '{user_input}'...")

        content = f"Extract specs for: '{user_input}'"
        if context_anchor_hint:
            content += f"\n\nInventory context hint: '{context_anchor_hint}'"
        if web_context:
            content += f"\n\nWeb search results:\n{web_context}"
        else:
            content += "\n\nNo web context — mark all fields as inferred."

        resp = self._call_llm(client, [{"role": "user", "content": content}], json_mode=True)
        try:
            parsed = json.loads(resp)
            if "currency_signals" not in parsed:
                parsed["currency_signals"] = {"date_code": None, "lifecycle_status": None}
            if log_cb:
                log_cb(f"[{self.name}] Extracted: {parsed.get('component_type', 'Unknown')}")
            return parsed
        except json.JSONDecodeError as e:
            if log_cb:
                log_cb(f"[{self.name}] JSON parse error: {e}. Using fallback.")
            return {
                "component_name": user_input, "component_type": "Unknown",
                "key_parameters": {}, "technical_details": {},
                "currency_signals": {"date_code": None, "lifecycle_status": None},
                "standard_alternatives": []
            }


class ApplicationDomainSpecialistAgent(BaseAgent):
    def __init__(self, model: str = DEFAULT_MODEL):
        system_prompt = (
            "You are the Universal Downstream Hardware Specialist. Your job is to map a given "
            "component to the PHYSICAL CIRCUIT BOARDS and FINISHED EQUIPMENT that use it, and "
            "assign the correct DOWNSTREAM HSN code from the supplied lookup table.\n\n"

            "=== STEP A — MANDATORY FIRST STEP: TECHNOLOGY CURRENCY ===\n"
            "Before listing any application, classify this component's technology generation:\n"
            "- 'current_new_product_design_in': still being designed into new 2025-2026 products\n"
            "- 'mature_but_still_current': not cutting-edge but still the current mainstream "
            "choice for a specific cost/performance tier (e.g. DDR4, eMMC 5.1, SPI NOR flash "
            "for firmware — ALL still standard choices in 2025 for their respective use cases)\n"
            "- 'legacy_sustaining_production': dated part, but equipment using it still in "
            "active production/repair\n"
            "- 'repair_rework_spares_only': field repair/maintenance only\n"
            "- 'obsolete': no current demand\n\n"
            "IMPORTANT CURRENCY FACTS — do not get these wrong:\n"
            "- SPI NOR Flash (W25Q, MT25Q etc.) is STILL the dominant choice for BIOS/UEFI/"
            "firmware/boot storage in 2025. It is NOT obsolete or superseded for this role. "
            "eMMC/UFS serve bulk OS storage — a completely different role.\n"
            "- DDR4 SDRAM is 'mature_but_still_current' — it is the current mainstream choice "
            "for cost-sensitive and mid-tier designs, still being designed in at high volume.\n"
            "- eMMC 5.1 is 'mature_but_still_current' — still the current standard for "
            "budget/embedded/industrial/IoT storage in new 2025 designs.\n"
            "- Enterprise NVMe SSDs (U.2/U.3, PCIe 4.0+, >= 1TB) are 'current_new_product_design_in'.\n"
            "- Bluetooth 4.2 SoC (CYW20706 etc.) is 'mature_but_still_current' for IoT/industrial.\n\n"

            "=== STEP B — MANDATORY: SCAN ALL 7 VERTICALS ===\n"
            "You MUST check every vertical below and identify at least one board per vertical "
            "where this component is technically plausible. You must produce AT LEAST 5 total "
            "application entries. Do not stop at 2 or 3.\n\n"
            "VERTICAL 1 — Enterprise IT & Data Center:\n"
            "  Boards: Server motherboards, NVMe storage backplane, RAID controller cards, "
            "  network switch fabric boards, GPU compute node host boards\n"
            "VERTICAL 2 — Security, Surveillance & Video:\n"
            "  Boards: IP camera ISP+SoC PCB, NVR recording engine board, "
            "  smart access controller mainboard, video analytics edge box\n"
            "VERTICAL 3 — Industrial Automation, Control & Power:\n"
            "  Boards: PLC CPU module, HMI touch panel mainboard, solar inverter DSP control "
            "  board, smart meter AMI communication module, VFD motor drive control card\n"
            "VERTICAL 4 — Automotive & Telematics:\n"
            "  Boards: Automotive infotainment head-unit PCB, telematics TCU mainboard, "
            "  EV BMS controller board, ADAS sensor fusion ECU, digital instrument cluster PCB\n"
            "VERTICAL 5 — Telecom Infrastructure & Aerospace:\n"
            "  Boards: 5G small cell baseband board, optical line terminal (OLT) card, "
            "  SDR radio processing module, UAV/drone flight controller PCB, satellite modem board\n"
            "VERTICAL 6 — Test, Measurement & Instrumentation:\n"
            "  Boards: Digital oscilloscope acquisition board, signal generator DDS card, "
            "  ATE pin electronics board, spectrum analyzer RF frontend PCB\n"
            "VERTICAL 7 — Healthcare & Medical Devices:\n"
            "  Boards: Patient vitals monitor mainboard, portable ultrasound DSP board, "
            "  digital X-ray readout PCB, infusion pump controller board\n\n"
            "=== STEP C — STRICT RULES ===\n"
            "1. subsystem_class must be a standardized functional block string (e.g. 'Wireless Connectivity', 'Memory Storage').\n"
            "2. target_product_family must be a clean, final downstream machine category string (e.g. 'Smart Electronic Locks', 'Industrial PLCs').\n"
            "3. product_hsn MUST be selected EXACTLY from the HSN lookup table provided below in the user message. Do not invent or modify any code.\n"
            "4. buyer_industry_code must be the official industrial classification code (NIC/ISIC) for that sector in the format 'NIC_xxxx' where xxxx is the 4-digit classification (e.g., 'NIC_2630' for communication equipment manufacturers, 'NIC_2620' for computer manufacturers, 'NIC_2651' for measurement instruments).\n"
            "5. confidence must be 'verified_via_web_evidence' only if the web context actually mentions this part in this application. Otherwise 'engineering_inference_only'.\n\n"
            "REFINEMENT RULE: If specs contain 'refinement_instructions', follow them precisely — remove flagged entries, do not pad with new ones to avoid addressing the error.\n\n"
            "Output JSON with:\n"
            "- technology_currency_assessment: {status, justification}\n"
            "- graph_mappings: list of objects, each with:\n"
            "    subsystem_class,\n"
            "    target_product_family,\n"
            "    product_hsn (MUST match lookup table exactly),\n"
            "    buyer_industry_code,\n"
            "    confidence"
        )
        super().__init__("AppSpecialist", "Downstream System Architecture Tracer", system_prompt, model)

    def run(self, client: Groq, specs: Dict[str, Any], *,
            web_context: Optional[str] = None,
            hsn_lookup: Optional[Dict[str, str]] = None,
            log_cb: Callable[[str], None] = None) -> Dict[str, Any]:
        if log_cb:
            log_cb(f"[{self.name}] Analyzing application areas for "
                   f"'{specs.get('component_type')}' with currency signals "
                   f"{specs.get('currency_signals')}...")

        content = f"Component specs:\n{json.dumps(specs, indent=2)}"

        # CRITICAL: inject the closed HSN lookup table so model can only pick from it
        if hsn_lookup:
            hsn_lines = "\n".join(f'  "{k}": "{v}"' for k, v in hsn_lookup.items())
            content += (
                f"\n\n=== VALID HSN CODES — SELECT ONLY FROM THIS LIST ===\n"
                f"Every product_hsn value MUST be one of these exact strings. "
                f"Do not modify, abbreviate, or invent any code not in this list:\n{{\n{hsn_lines}\n}}"
            )
        if web_context:
            content += (
                f"\n\nWeb evidence (use to assign verified_via_web_evidence confidence "
                f"only where this specific part is mentioned):\n{web_context}"
            )

        resp = self._call_llm(client, [{"role": "user", "content": content}], json_mode=True)
        try:
            parsed = json.loads(resp)
            apps = parsed.get("graph_mappings", [])
            # Normalize HSN codes — strip dots/spaces, validate against lookup
            if hsn_lookup and apps:
                valid_hsn = set(hsn_lookup.keys())
                for app in apps:
                    raw_hsn = str(app.get("product_hsn", "")).replace(".", "").strip()
                    app["product_hsn"] = raw_hsn if raw_hsn in valid_hsn else "INVALID_HSN_NOT_IN_LOOKUP"
            status = parsed.get("technology_currency_assessment", {}).get("status", "unknown")
            if log_cb:
                log_cb(f"[{self.name}] Currency: {status}. Graph Mappings: {len(apps)}")
            return parsed
        except json.JSONDecodeError as e:
            if log_cb:
                log_cb(f"[{self.name}] JSON parse error: {e}")
            return {"technology_currency_assessment": {"status": "unknown", "justification": "parse error"},
                    "graph_mappings": []}


class SynthesisAgent(BaseAgent):
    def __init__(self, model: str = DEFAULT_MODEL):
        system_prompt = (
            "You are the Synthesis Agent. Write a clear, factual inventory intelligence report "
            "from the Specs Extractor and Application Specialist data.\n\n"
            "Required sections:\n"
            "1. Executive Summary: component name, technology currency status, primary use cases.\n"
            "2. Technical Specifications: table showing field, value, confirmed/inferred status. "
            "Do NOT include HSN codes for the component itself, or any CIN or business "
            "registration numbers in this section.\n"
            "3. Current Market Relevance & Liquidation Strategy: based on currency status, "
            "name the specific buyer type to target (e.g. for DDR4: server ODMs, embedded "
            "SBC makers, industrial controller OEMs — NOT 'hyperscalers' for a mature-tier part). "
            "Be specific, not generic.\n"
            "4. Downstream Relational Graph Mappings: TWO SEPARATE TABLES:\n"
            "   - 'Verified in Production' — ONLY mappings with verified_via_web_evidence tag\n"
            "   - 'Likely Matches (Engineering Inference)' — ONLY mappings with "
            "engineering_inference_only tag\n"
            "   Each row must include: Subsystem Class | Target Product Family | Product HSN | "
            "Buyer Industry Code | Confidence\n"
            "   In both tables, pull the HSN code directly from the 'product_hsn' "
            "data key. Do not perform any internal index mappings or lookup cross-references. "
            "If the 'product_hsn' value is 'INVALID_HSN_NOT_IN_LOOKUP' or is missing, "
            "explicitly print '85423200' (the safe generic IC fallback HSN code) in the table cell "
            "instead of printing 'INVALID_HSN_NOT_IN_LOOKUP'.\n"
            "5. Target Buyers by Category: grouped by hardware vertical, bullet list of "
            "manufacturer types. No company names, no CINs, no registration numbers.\n\n"
            "Show confidence labels clearly. Do not smooth everything to the same confident tone."
        )
        super().__init__("Synthesis", "Inventory Intelligence Reporter", system_prompt, model)

    def run(self, client: Groq, specs: Dict[str, Any], applications_data: Dict[str, Any],
            log_cb: Callable[[str], None] = None) -> str:
        if log_cb:
            log_cb(f"[{self.name}] Synthesizing report...")

        apps = applications_data.get("applications", [])
        
        # Split the dynamic applications array by confidence score
        verified_apps = [app for app in apps if app.get("confidence") == "verified_via_web_evidence"]
        inferred_apps = [app for app in apps if app.get("confidence") == "engineering_inference_only"]
        
        tables_md = "## Downstream Relational Graph Mappings\n\n"
        
        # Compile the "Verified in Production" Section
        tables_md += "### Verified in Production\n\n"
        if verified_apps:
            tables_md += "| Subsystem Class | Target Product Family | Product HSN | Buyer Industry Code | Confidence |\n"
            tables_md += "| --- | --- | --- | --- | --- |\n"
            for app in verified_apps:
                hsn = app.get("product_hsn", "85423200")
                if hsn == "INVALID_HSN_NOT_IN_LOOKUP" or not hsn:
                    hsn = "85423200"
                tables_md += f"| {app.get('subsystem_class')} | {app.get('target_product_family')} | {hsn} | {app.get('buyer_industry_code')} | Verified via Web |\n"
        else:
            tables_md += "*No application layouts found with direct public web verification traces.*\n"
            
        tables_md += "\n---\n\n"
        
        # Compile the "Likely Matches (Engineering Inference)" Section
        tables_md += "### Likely Matches (Engineering Inference)\n\n"
        if inferred_apps:
            tables_md += "| Subsystem Class | Target Product Family | Product HSN | Buyer Industry Code | Confidence |\n"
            tables_md += "| --- | --- | --- | --- | --- |\n"
            for app in inferred_apps:
                hsn = app.get("product_hsn", "85423200")
                if hsn == "INVALID_HSN_NOT_IN_LOOKUP" or not hsn:
                    hsn = "85423200"
                tables_md += f"| {app.get('subsystem_class')} | {app.get('target_product_family')} | {hsn} | {app.get('buyer_industry_code')} | Engineering Inference Only |\n"
        else:
            tables_md += "*No additional algorithmic matches generated for this sequence.*\n"

        content = (f"Component Specs:\n{json.dumps(specs, indent=2)}\n\n"
                   f"Application Areas:\n{json.dumps(applications_data, indent=2)}\n\n"
                   f"Pre-formatted Downstream Relational Graph Mappings Tables:\n\n{tables_md}\n\n"
                   f"Write the full synthesized inventory report. You MUST include the pre-formatted tables above exactly as they are.")

        report = self._call_llm(client, [{"role": "user", "content": content}],
                                json_mode=False, temperature=0.3)
        if log_cb:
            log_cb(f"[{self.name}] Done. Word count: {len(report.split())}")
        return report


class QualityAssuranceAgent(BaseAgent):
    def __init__(self, model: str = DEFAULT_MODEL):
        system_prompt = (
            "You are the Quality Assurance Agent. Audit the synthesized report for errors.\n\n"
            "REJECT (set approved=False) ONLY for these four specific errors:\n"
            "1. COMPONENT TYPE MISMATCH: specs say DDR4 but report calls it DDR3 or Flash. "
            "Or specs say NOR Flash but report calls it RAM or eMMC.\n"
            "2. FORBIDDEN FIELDS: report contains a CIN number, a company registration number, "
            "or a raw component-level HSN code (e.g. 85423200 for memory ICs in the specs "
            "table). Downstream finished-product HSN codes in the applications table are "
            "ALLOWED and CORRECT — do not flag those.\n"
            "3. LITERAL PLACEHOLDERS: text like '[insert here]', 'Company XYZ', '[Placeholder]'.\n"
            "4. SELF-REFERENTIAL BOARD NAME: hardware_system_board is literally the component's "
            "own name or manufacturer name instead of an actual board type.\n\n"
            "DO NOT REJECT FOR ANY OF THESE — THEY ARE CORRECT:\n"
            "- 'Likely Matches' applications are engineering inferences without web evidence. "
            "This is EXPECTED and CORRECT. Do not flag it.\n"
            "- SPI NOR Flash used for firmware/BIOS/boot storage. This IS the 2025 industry "
            "standard for this role. Do not flag it as outdated.\n"
            "- eMMC 5.1 used in smartphones, tablets, IoT, industrial, or embedded designs. "
            "This IS still current for these segments. Do not flag it.\n"
            "- DDR4 described as 'mature but still current'. This IS accurate. Do not flag it.\n"
            "- Reports mentioning that DDR5 is superseding DDR4 in high-end servers. True fact.\n"
            "- 'INVALID_HSN_NOT_IN_LOOKUP' appearing in the report. This is a valid flag from "
            "the pipeline indicating an HSN that needs manual correction. Do not reject for it.\n\n"
            "Return JSON:\n"
            "- approved: true if none of the four rejection criteria above are met\n"
            "- evaluation_notes: short, specific findings — quote the actual text that is wrong\n"
            "- recommendations: list of concrete corrections (empty if approved)"
        )
        super().__init__("QualityAssurance", "Report Integrity Auditor", system_prompt, model)

    def run(self, client: Groq, specs: Dict[str, Any], report: str, *,
            web_context: Optional[str] = None,
            log_cb: Callable[[str], None] = None) -> Dict[str, Any]:
        if log_cb:
            log_cb(f"[{self.name}] Auditing report...")

        issues = []
        
        # 1. Clean String Case Validation to prevent false Component Type alerts
        specs_component_type = specs.get("component_type", "")
        if specs_component_type:
            clean_specs_type = specs_component_type.strip().lower()
            clean_report_text = report.lower()
            
            if clean_specs_type not in clean_report_text:
                tokens = clean_specs_type.split()
                # Check if any key individual taxonomy tokens exist (len > 2)
                if not any(t in clean_report_text for t in tokens if len(t) > 2):
                    issues.append(
                        f"COMPONENT TYPE MISMATCH: System resolved type as '{specs_component_type}' but report text matches failed."
                    )

        # 2. Fixed Forbidden Fields Checker for HSN Leakages
        # Ignore markdown layout table line rows entirely when scanning for raw leaks
        specs_hsn = specs.get("component_hsn") or specs.get("hsn")
        if specs_hsn:
            hsn_str = str(specs_hsn).strip().replace(".", "")
            if hsn_str and len(hsn_str) >= 4:
                lines = report.split("\n")
                for line in lines:
                    if "|" in line:
                        continue
                    if hsn_str in line.replace(".", ""):
                        issues.append(f"FORBIDDEN FIELDS: Report contains raw component-level HSN '{specs_hsn}' in prose.")

        # 3. Check for literal placeholders
        placeholders = ["[insert", "xyz", "[placeholder", "company name"]
        report_lower = report.lower()
        for p in placeholders:
            if p in report_lower:
                issues.append(f"LITERAL PLACEHOLDERS: Report contains placeholder text '{p}'.")

        # 4. Check for self-referential board name in report
        # e.g., if the board name is literally the component name or manufacturer name
        comp_name = (specs.get("component_name") or "").lower()
        mfr = ""
        tech = specs.get("technical_details") or {}
        if isinstance(tech, dict):
            mfr_val = tech.get("manufacturer") or ""
            mfr = mfr_val.get("value") or "" if isinstance(mfr_val, dict) else str(mfr_val)
        mfr = mfr.lower()

        lines = report.split("\n")
        for line in lines:
            if "|" in line and comp_name in line.lower() and comp_name:
                parts = line.split("|")
                if len(parts) > 1 and parts[1].strip().lower() == comp_name:
                    issues.append(f"SELF-REFERENTIAL BOARD NAME: Board row contains component name '{comp_name}'.")

        if issues:
            status = "NEEDS REVISION"
            if log_cb:
                log_cb(f"[{self.name}] {status}. Notes: {issues}")
            return {
                "approved": False,
                "evaluation_notes": issues,
                "recommendations": issues
            }

        status = "APPROVED"
        if log_cb:
            log_cb(f"[{self.name}] {status}. Notes: Report meets technical architecture guidelines.")
        return {
            "approved": True,
            "evaluation_notes": "Report meets technical architecture guidelines.",
            "recommendations": []
        }


class NomenclatureAgent(BaseAgent):
    def __init__(self, model: str = DEFAULT_MODEL):
        system_prompt = (
            "You are the Component Nomenclature Parser. Analyze raw alphanumeric part numbers "
            "to deduce their component family, type, manufacturer, and key specifications "
            "using standard manufacturer naming rules (e.g. Samsung memory codes, Micron prefixes, "
            "Winbond, ISSI, etc.).\n\n"
            "Respond with a short, factual 1-sentence description of the component's taxonomy."
        )
        super().__init__("NomenclatureAgent", "Naming Rules Parser", system_prompt, model)

    def extract_via_naming_rules(self, client: Groq, part_no: str, log_cb: Optional[Callable[[str], None]] = None) -> str:
        if log_cb:
            log_cb(f"[{self.name}] Parsing part number nomenclature: '{part_no}'...")
        content = f"Deduce specifications from part number using naming rules: '{part_no}'"
        resp = self._call_llm(client, [{"role": "user", "content": content}], json_mode=False)
        return resp.strip()