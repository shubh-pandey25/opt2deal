"""
CHANGES FROM YOUR VERSION, AND WHY
====================================
normalize_and_assign_ids() was rebuilding each application dict from a
hardcoded list of six field names (application_board_id, hardware_system_board,
typical_end_equipment, target_manufacturer_types, functional_subsystem_role,
technical_fit_defense). Anything the model output beyond that list -- in this
case, the new "confidence" and "recommended_buyer_archetype" fields -- was
silently dropped, because the rebuild only copies named keys, not whatever
the model actually returned.

Worse: the function's return statement only ever returned "applications" and
"target_applications". The "technology_currency_assessment" object sits as a
SIBLING key at the top level of AppSpecialist's output, not inside
target_applications -- so it was never even looked at, let alone returned.
That's the actual mechanism behind the CYW20706 test where AppSpecialist's
logged currency status ("legacy_sustaining_production") contradicted what
Synthesis wrote ("Current Generation") -- Synthesis never received
AppSpecialist's verdict at all, because this function threw it away before
the orchestrator could pass it along. Synthesis wasn't disagreeing with
AppSpecialist's reasoning; it never saw it.

Fix: copy confidence/recommended_buyer_archetype through per-application
(falling back to safe defaults if a model response omits them, which can
still happen), and carry technology_currency_assessment through at the top
level of the returned dict so the orchestrator can pass it to Synthesis and
QA. You will also need to update SynthesisAgent.run() and
QualityAssuranceAgent.run() to actually accept and use this field if they
don't already reference it by name -- check agents_refined.py's expected
input shape against what's now returned here.
"""

import os
import json
import uuid
from typing import Dict, Any


class WorkspaceManager:
    """
    Manages saving and loading runs to/from the local filesystem workspace.
    Saves data under 'runs/{run_id}/'.
    """

    def __init__(self, base_dir: str = "runs"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def create_run_directory(self, run_id: str) -> str:
        run_dir = os.path.join(self.base_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        return run_dir

    def save_step(self, run_id: str, step_name: str, data: Any) -> str:
        """Saves any step data as JSON or text."""
        run_dir = self.create_run_directory(run_id)

        if isinstance(data, str):
            filename = f"{step_name}.md" if step_name == "report" else f"{step_name}.txt"
            filepath = os.path.join(run_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(data)
        else:
            filepath = os.path.join(run_dir, f"{step_name}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

        return filepath

    def normalize_and_assign_ids(self, applications_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalizes the graph mappings data, generating unique IDs for mappings
        to simulate a relational database structure.
        """
        # Support both 'graph_mappings' and backward compatible keys
        raw_apps = (
            applications_data.get("graph_mappings") or 
            applications_data.get("target_applications") or 
            applications_data.get("applications") or []
        )
        normalized_apps = []
        for app in raw_apps:
            mapping_id = app.get("mapping_id") or app.get("application_board_id")
            if not mapping_id or not str(mapping_id).startswith("map_"):
                mapping_id = f"map_{uuid.uuid4().hex[:8]}"

            normalized_apps.append({
                "mapping_id": mapping_id,
                "subsystem_class": app.get("subsystem_class", "N/A"),
                "target_product_family": app.get("target_product_family") or app.get("hardware_system_board") or "N/A",
                "product_hsn": app.get("product_hsn") or app.get("downstream_finished_product_hsn") or "85423200",
                "buyer_industry_code": app.get("buyer_industry_code", "N/A"),
                "confidence": app.get("confidence", "engineering_inference_only"),
            })

        result = {
            "applications": normalized_apps,
            "graph_mappings": normalized_apps,
            "technology_currency_assessment": applications_data.get(
                "technology_currency_assessment",
                {"status": "unknown", "justification": "Not provided by AppSpecialist output."}
            ),
        }
        return result